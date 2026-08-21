from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol

import httpx


class ProviderState(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class MediaRequest:
    prompt: str
    model: str
    idempotency_key: str
    parameters: Dict[str, Any]
    reference_uri: Optional[str] = None


@dataclass
class ProviderTask:
    id: str
    status: ProviderState
    raw: Dict[str, Any]


@dataclass
class MediaResult:
    task_id: str
    uri: str
    cost: float
    elapsed_sec: float


@dataclass(frozen=True)
class ProviderCapabilities:
    media_kinds: tuple[str, ...]
    supports_reference_image: bool
    supports_last_frame: bool
    supports_webhook: bool
    supports_idempotency_recovery: bool
    max_duration_sec: float


@dataclass(frozen=True)
class ProviderEstimate:
    amount: float
    currency: str
    expected_latency_sec: float


@dataclass(frozen=True)
class NormalizedProviderError:
    code: str
    message: str
    retryable: bool
    outcome_unknown: bool = False


class ProviderProtocolError(RuntimeError):
    pass


class MediaProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def estimate(self, request: MediaRequest) -> ProviderEstimate: ...
    def normalize_error(self, error: Exception) -> NormalizedProviderError: ...
    def verify_webhook(self, payload: bytes, signature: str) -> bool: ...
    async def submit(self, request: MediaRequest) -> ProviderTask: ...
    async def recover(self, idempotency_key: str) -> ProviderTask | None: ...
    async def query(self, task_id: str) -> ProviderTask: ...
    async def cancel(self, task_id: str) -> None: ...
    async def fetch_result(self, task_id: str) -> MediaResult: ...
    async def health(self) -> bool: ...


class _HTTPProvider:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)


class ComfyUIProvider(_HTTPProvider):
    def __init__(self, base_url: str, workflow: Dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport)
        self.base_url = base_url.rstrip("/")
        self.workflow = workflow

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(("image", "video"), True, False, False, False, 20)

    def estimate(self, request: MediaRequest) -> ProviderEstimate:
        return ProviderEstimate(0, "CNY", 45)

    def normalize_error(self, error: Exception) -> NormalizedProviderError:
        return _normalize_http_error(error)

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        return False

    async def submit(self, request: MediaRequest) -> ProviderTask:
        payload = {"prompt": self._compile_workflow(request), "client_id": request.idempotency_key}
        async with self.client(30) as client:
            response = await client.post(f"{self.base_url}/prompt", json=payload)
            response.raise_for_status()
            data = response.json()
        if not data.get("prompt_id"):
            raise ProviderProtocolError("ComfyUI response is missing prompt_id")
        return ProviderTask(id=data["prompt_id"], status=ProviderState.queued, raw=data)

    async def query(self, task_id: str) -> ProviderTask:
        async with self.client(15) as client:
            response = await client.get(f"{self.base_url}/history/{task_id}")
            response.raise_for_status()
            data = response.json()
        state = ProviderState.succeeded if task_id in data and data[task_id].get("outputs") else ProviderState.running
        return ProviderTask(id=task_id, status=state, raw=data)

    async def recover(self, idempotency_key: str) -> ProviderTask | None:
        return None

    async def cancel(self, task_id: str) -> None:
        async with self.client(10) as client:
            response = await client.post(f"{self.base_url}/interrupt", json={"prompt_id": task_id})
            response.raise_for_status()

    async def fetch_result(self, task_id: str) -> MediaResult:
        task = await self.query(task_id)
        if task.status != ProviderState.succeeded:
            raise RuntimeError("ComfyUI task is not complete")
        outputs = task.raw[task_id].get("outputs", {})
        for node in outputs.values():
            media_list = node.get("videos") or node.get("gifs") or node.get("images") or []
            if media_list:
                media = media_list[0]
                if "filename" not in media:
                    raise ProviderProtocolError("ComfyUI output is missing filename")
                uri = f"{self.base_url}/view?filename={media['filename']}&subfolder={media.get('subfolder', '')}&type={media.get('type', 'output')}"
                return MediaResult(task_id=task_id, uri=uri, cost=0, elapsed_sec=0)
        raise ProviderProtocolError("ComfyUI workflow completed without a media output node")

    async def health(self) -> bool:
        try:
            async with self.client(5) as client:
                return (await client.get(f"{self.base_url}/system_stats")).is_success
        except httpx.HTTPError:
            return False

    def _compile_workflow(self, request: MediaRequest) -> Dict[str, Any]:
        workflow = copy.deepcopy(self.workflow)
        for node in workflow.values():
            inputs = node.get("inputs", {})
            if "text" in inputs and "negative" not in str(node.get("class_type", "")).lower():
                inputs["text"] = request.prompt
            if "seed" in inputs and "seed" in request.parameters:
                inputs["seed"] = request.parameters["seed"]
        return workflow


class OpenAICompatibleMediaProvider(_HTTPProvider):
    def __init__(self, base_url: str, api_key: str, media_kind: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport)
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.api_key = api_key
        self.media_kind = media_kind

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities((self.media_kind,), True, True, True, True, 20 if self.media_kind == "video" else 600)

    def estimate(self, request: MediaRequest) -> ProviderEstimate:
        duration = float(request.parameters.get("duration", 5))
        unit = 0.12 if self.media_kind == "video" else 0.02
        return ProviderEstimate(round(unit * duration, 4), "CNY", 90 if self.media_kind == "video" else 15)

    def normalize_error(self, error: Exception) -> NormalizedProviderError:
        return _normalize_http_error(error)

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(self.api_key.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.removeprefix("sha256="))

    async def submit(self, request: MediaRequest) -> ProviderTask:
        payload = {"model": request.model, "prompt": request.prompt, **request.parameters}
        if request.reference_uri:
            payload["reference_uri"] = request.reference_uri
        headers = {**self.headers, "Idempotency-Key": request.idempotency_key}
        try:
            async with self.client(60) as client:
                response = await client.post(f"{self.base_url}/{self.media_kind}/tasks", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            # The remote service may have accepted the request before our connection timed out.
            # Recover by the same idempotency key instead of submitting and charging twice.
            async with self.client(20) as client:
                response = await client.get(
                    f"{self.base_url}/{self.media_kind}/tasks/by-idempotency/{request.idempotency_key}",
                    headers=self.headers,
                )
                response.raise_for_status()
                data = response.json()
        if not data.get("id"):
            raise ProviderProtocolError("Provider response is missing task id")
        try:
            state = ProviderState(data.get("status", "queued"))
        except ValueError as exc:
            raise ProviderProtocolError(f"Unknown provider status: {data.get('status')}") from exc
        return ProviderTask(id=data["id"], status=state, raw=data)

    async def recover(self, idempotency_key: str) -> ProviderTask | None:
        async with self.client(20) as client:
            response = await client.get(
                f"{self.base_url}/{self.media_kind}/tasks/by-idempotency/{idempotency_key}",
                headers=self.headers,
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if not data.get("id") or not data.get("status"):
            raise ProviderProtocolError("Provider recovery response is incomplete")
        return ProviderTask(id=data["id"], status=ProviderState(data["status"]), raw=data)

    async def query(self, task_id: str) -> ProviderTask:
        async with self.client(20) as client:
            response = await client.get(f"{self.base_url}/{self.media_kind}/tasks/{task_id}", headers=self.headers)
            response.raise_for_status()
            data = response.json()
        if "status" not in data:
            raise ProviderProtocolError("Provider query response is missing status")
        return ProviderTask(id=task_id, status=ProviderState(data["status"]), raw=data)

    async def cancel(self, task_id: str) -> None:
        async with self.client(20) as client:
            response = await client.post(f"{self.base_url}/{self.media_kind}/tasks/{task_id}/cancel", headers=self.headers)
            response.raise_for_status()

    async def fetch_result(self, task_id: str) -> MediaResult:
        task = await self.query(task_id)
        if task.status != ProviderState.succeeded:
            raise RuntimeError("Provider task is not complete")
        if not task.raw.get("output_uri"):
            raise ProviderProtocolError("Completed provider task is missing output_uri")
        return MediaResult(task_id=task_id, uri=task.raw["output_uri"], cost=float(task.raw.get("cost", 0)), elapsed_sec=float(task.raw.get("elapsed_sec", 0)))

    async def health(self) -> bool:
        try:
            async with self.client(5) as client:
                response = await client.get(f"{self.base_url}/health", headers=self.headers)
            return response.is_success
        except httpx.HTTPError:
            return False


class TTSProvider(OpenAICompatibleMediaProvider):
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url, api_key, "audio", transport)


def create_media_provider(
    environment: Mapping[str, str] | None = None,
    comfy_workflow: Dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, MediaProvider | None]:
    env = environment or os.environ
    name = env.get("MANGAFLOW_PROVIDER", "mock").lower()
    if name == "mock":
        return name, None
    if name == "comfyui":
        base_url = env.get("COMFYUI_BASE_URL")
        if not base_url or comfy_workflow is None:
            raise ValueError("COMFYUI_BASE_URL and workflow are required for comfyui provider")
        return name, ComfyUIProvider(base_url, comfy_workflow, transport)
    if name in {"cloud", "cloud-video"}:
        base_url, key = env.get("CLOUD_VIDEO_BASE_URL"), env.get("CLOUD_VIDEO_API_KEY")
        if not base_url or not key:
            raise ValueError("CLOUD_VIDEO_BASE_URL and CLOUD_VIDEO_API_KEY are required")
        return "cloud-video", OpenAICompatibleMediaProvider(base_url, key, "video", transport)
    raise ValueError(f"Unsupported MANGAFLOW_PROVIDER: {name}")


def _normalize_http_error(error: Exception) -> NormalizedProviderError:
    if isinstance(error, httpx.TimeoutException):
        return NormalizedProviderError("PROVIDER_TIMEOUT", str(error), True, outcome_unknown=True)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return NormalizedProviderError(
            f"PROVIDER_HTTP_{status}", str(error), status == 429 or status >= 500,
        )
    if isinstance(error, ProviderProtocolError):
        return NormalizedProviderError("PROVIDER_PROTOCOL_ERROR", str(error), False)
    return NormalizedProviderError("PROVIDER_ERROR", str(error), False)
