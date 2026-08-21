from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Protocol

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


class MediaProvider(Protocol):
    async def submit(self, request: MediaRequest) -> ProviderTask: ...
    async def query(self, task_id: str) -> ProviderTask: ...
    async def cancel(self, task_id: str) -> None: ...
    async def fetch_result(self, task_id: str) -> MediaResult: ...


class ComfyUIProvider:
    """Thin ComfyUI API adapter; business logic never depends on workflow JSON details."""

    def __init__(self, base_url: str, workflow: Dict[str, Any]) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow = workflow

    async def submit(self, request: MediaRequest) -> ProviderTask:
        payload = {"prompt": self._compile_workflow(request), "client_id": request.idempotency_key}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/prompt", json=payload)
            response.raise_for_status()
            data = response.json()
        return ProviderTask(id=data["prompt_id"], status=ProviderState.queued, raw=data)

    async def query(self, task_id: str) -> ProviderTask:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/history/{task_id}")
            response.raise_for_status()
            data = response.json()
        state = ProviderState.succeeded if task_id in data and data[task_id].get("outputs") else ProviderState.running
        return ProviderTask(id=task_id, status=state, raw=data)

    async def cancel(self, task_id: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.base_url}/interrupt")
            response.raise_for_status()

    async def fetch_result(self, task_id: str) -> MediaResult:
        task = await self.query(task_id)
        if task.status != ProviderState.succeeded:
            raise RuntimeError("ComfyUI task is not complete")
        outputs = task.raw[task_id]["outputs"]
        first_node = next(iter(outputs.values()))
        media = (first_node.get("videos") or first_node.get("images") or [])[0]
        uri = f"{self.base_url}/view?filename={media['filename']}&subfolder={media.get('subfolder', '')}&type={media.get('type', 'output')}"
        return MediaResult(task_id=task_id, uri=uri, cost=0, elapsed_sec=0)

    def _compile_workflow(self, request: MediaRequest) -> Dict[str, Any]:
        workflow = {key: dict(value) for key, value in self.workflow.items()}
        for node in workflow.values():
            inputs = node.get("inputs", {})
            if "text" in inputs and "negative" not in str(inputs).lower():
                inputs["text"] = request.prompt
            if "seed" in inputs and "seed" in request.parameters:
                inputs["seed"] = request.parameters["seed"]
        return workflow


class OpenAICompatibleMediaProvider:
    """Generic adapter for cloud image/video providers exposing task endpoints."""

    def __init__(self, base_url: str, api_key: str, media_kind: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.media_kind = media_kind

    async def submit(self, request: MediaRequest) -> ProviderTask:
        payload = {"model": request.model, "prompt": request.prompt, **request.parameters}
        if request.reference_uri:
            payload["reference_uri"] = request.reference_uri
        headers = {**self.headers, "Idempotency-Key": request.idempotency_key}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.base_url}/{self.media_kind}/tasks", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return ProviderTask(id=data["id"], status=ProviderState(data.get("status", "queued")), raw=data)

    async def query(self, task_id: str) -> ProviderTask:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/{self.media_kind}/tasks/{task_id}", headers=self.headers)
            response.raise_for_status()
            data = response.json()
        return ProviderTask(id=task_id, status=ProviderState(data["status"]), raw=data)

    async def cancel(self, task_id: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/{self.media_kind}/tasks/{task_id}/cancel", headers=self.headers)
            response.raise_for_status()

    async def fetch_result(self, task_id: str) -> MediaResult:
        task = await self.query(task_id)
        if task.status != ProviderState.succeeded:
            raise RuntimeError("Provider task is not complete")
        return MediaResult(task_id=task_id, uri=task.raw["output_uri"], cost=float(task.raw.get("cost", 0)), elapsed_sec=float(task.raw.get("elapsed_sec", 0)))


class TTSProvider(OpenAICompatibleMediaProvider):
    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__(base_url, api_key, "audio")

