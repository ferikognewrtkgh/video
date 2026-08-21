import json
import hashlib
import hmac

import httpx
import pytest

from app.providers import (
    ComfyUIProvider,
    MediaRequest,
    OpenAICompatibleMediaProvider,
    ProviderProtocolError,
    ProviderState,
    create_media_provider,
    create_stage_provider,
)


def request():
    return MediaRequest(prompt="hero in rain", model="i2v-v1", idempotency_key="idem-123", parameters={"seed": 42})


@pytest.mark.anyio
async def test_cloud_provider_submit_sends_idempotency_and_parses_task():
    async def handler(req: httpx.Request):
        assert req.headers["Idempotency-Key"] == "idem-123"
        assert json.loads(req.content)["prompt"] == "hero in rain"
        return httpx.Response(200, json={"id": "task-1", "status": "queued"})
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(handler))
    task = await provider.submit(request())
    assert task.id == "task-1"
    assert task.status == ProviderState.queued


def test_provider_capabilities_estimate_webhook_and_error_normalization():
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video")
    capabilities = provider.capabilities()
    assert capabilities.supports_reference_image is True
    assert capabilities.supports_idempotency_recovery is True
    estimate = provider.estimate(MediaRequest(
        prompt="x", model="video-v1", idempotency_key="key", parameters={"duration": 5},
    ))
    assert estimate.amount == 0.6
    payload = b'{"id":"task-1"}'
    signature = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    assert provider.verify_webhook(payload, f"sha256={signature}") is True
    timeout = httpx.ReadTimeout("lost")
    normalized = provider.normalize_error(timeout)
    assert normalized.retryable is True
    assert normalized.outcome_unknown is True


@pytest.mark.anyio
async def test_provider_recovery_returns_none_on_404():
    provider = OpenAICompatibleMediaProvider(
        "https://provider.test", "secret", "video",
        httpx.MockTransport(lambda _: httpx.Response(404, json={"error": "missing"})),
    )
    assert await provider.recover("unknown-key") is None


@pytest.mark.anyio
async def test_submit_timeout_recovers_existing_task_by_idempotency_key():
    calls = []
    async def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.method == "POST":
            raise httpx.ReadTimeout("response lost", request=req)
        return httpx.Response(200, json={"id": "already-created", "status": "running"})
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(handler))
    task = await provider.submit(request())
    assert task.id == "already-created"
    assert calls == ["/video/tasks", "/video/tasks/by-idempotency/idem-123"]


@pytest.mark.anyio
async def test_provider_rejects_missing_or_unknown_response_fields():
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "queued"})))
    with pytest.raises(ProviderProtocolError, match="task id"):
        await provider.submit(request())
    unknown = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "x", "status": "mystery"})))
    with pytest.raises(ProviderProtocolError, match="Unknown provider status"):
        await unknown.submit(request())


@pytest.mark.anyio
async def test_query_and_cancel_http_failures_propagate():
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "down"})))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.query("task-1")
    with pytest.raises(httpx.HTTPStatusError):
        await provider.cancel("task-1")


@pytest.mark.anyio
async def test_completed_task_requires_output_uri():
    provider = OpenAICompatibleMediaProvider("https://provider.test", "secret", "video", httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "x", "status": "succeeded"})))
    with pytest.raises(ProviderProtocolError, match="output_uri"):
        await provider.fetch_result("x")


@pytest.mark.anyio
async def test_comfyui_compiles_copy_and_finds_nonstandard_output_node():
    workflow = {"text": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}}, "sampler": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    async def handler(req: httpx.Request):
        if req.url.path == "/prompt":
            body = json.loads(req.content)
            assert body["prompt"]["text"]["inputs"]["text"] == "hero in rain"
            assert body["prompt"]["sampler"]["inputs"]["seed"] == 42
            return httpx.Response(200, json={"prompt_id": "comfy-1"})
        return httpx.Response(200, json={"comfy-1": {"outputs": {"random-node-id": {"videos": [{"filename": "shot.mp4", "type": "output"}]}}}})
    provider = ComfyUIProvider("https://comfy.test", workflow, httpx.MockTransport(handler))
    await provider.submit(request())
    result = await provider.fetch_result("comfy-1")
    assert result.uri.endswith("filename=shot.mp4&subfolder=&type=output")
    assert workflow["text"]["inputs"]["text"] == "old"


@pytest.mark.anyio
async def test_comfyui_i2v_requires_and_consumes_explicit_reference_placeholder():
    captured = {}
    workflow = {
        "positive": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative_prompt}}"}},
        "reference": {"class_type": "LoadImageFromUrl", "inputs": {"url": "{{reference_uri}}"}},
    }

    def handler(req: httpx.Request):
        captured.update(json.loads(req.content)["prompt"])
        return httpx.Response(200, json={"prompt_id": "i2v-1"})

    provider = ComfyUIProvider("https://comfy.test", workflow, httpx.MockTransport(handler))
    media_request = MediaRequest(
        prompt="hero", model="i2v", idempotency_key="key", parameters={"negative_prompt": "flicker"},
        reference_uri="https://studio.test/keyframe.png",
    )
    await provider.submit(media_request)
    assert captured["positive"]["inputs"]["text"] == "hero"
    assert captured["negative"]["inputs"]["text"] == "flicker"
    assert captured["reference"]["inputs"]["url"] == "https://studio.test/keyframe.png"

    unsafe = ComfyUIProvider("https://comfy.test", {"node": {"inputs": {"text": "old"}}})
    with pytest.raises(ProviderProtocolError, match="reference_uri"):
        await unsafe.submit(media_request)


@pytest.mark.anyio
async def test_comfyui_uploads_local_approved_keyframe_before_i2v_submit():
    submitted = {}
    workflow = {
        "reference": {"class_type": "LoadImage", "inputs": {"image": "{{reference_uri}}"}},
        "prompt": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
    }

    def handler(req: httpx.Request):
        if req.url.path == "/upload/image":
            assert b"approved.png" in req.content
            assert b"real-image-bytes" in req.content
            return httpx.Response(200, json={"name": "approved.png", "subfolder": "mangaflow", "type": "input"})
        submitted.update(json.loads(req.content)["prompt"])
        return httpx.Response(200, json={"prompt_id": "i2v-upload-1"})

    provider = ComfyUIProvider("https://comfy.test", workflow, httpx.MockTransport(handler))
    await provider.submit(MediaRequest(
        prompt="hero", model="i2v", idempotency_key="key", parameters={},
        reference_uri="artifact://approved", reference_content=b"real-image-bytes",
        reference_filename="approved.png",
    ))
    assert submitted["reference"]["inputs"]["image"] == "mangaflow/approved.png"


def test_provider_factory_validates_configuration():
    name, provider = create_media_provider({"MANGAFLOW_PROVIDER": "mock"})
    assert name == "mock" and provider is None
    with pytest.raises(ValueError, match="CLOUD_VIDEO"):
        create_media_provider({"MANGAFLOW_PROVIDER": "cloud"})
    with pytest.raises(ValueError, match="Unsupported"):
        create_media_provider({"MANGAFLOW_PROVIDER": "unknown"})


def test_auxiliary_provider_factory_never_silently_substitutes_mock():
    name, provider = create_stage_provider("image", {})
    assert name == "disabled" and provider is None
    name, provider = create_stage_provider("tts", {
        "MANGAFLOW_TTS_PROVIDER": "cloud-tts",
        "TTS_PROVIDER_BASE_URL": "https://tts.test",
        "TTS_PROVIDER_API_KEY": "secret",
    })
    assert name == "cloud-tts"
    assert provider.capabilities().media_kinds == ("audio",)
    with pytest.raises(ValueError, match="IMAGE_PROVIDER"):
        create_stage_provider("image", {"MANGAFLOW_IMAGE_PROVIDER": "cloud-image"})
    with pytest.raises(ValueError, match="Unsupported production stage"):
        create_stage_provider("music", {})
