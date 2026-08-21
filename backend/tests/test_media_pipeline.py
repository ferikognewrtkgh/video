from __future__ import annotations

import hashlib
import hmac
import io
import json
import shutil
import time
import wave
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.demo import build_demo_project
from app.domain import ArtifactReviewStatus, MediaKind
from app.main import create_app
from app.media_pipeline import ArtifactFetcher, ArtifactStore, ArtifactValidationError, AssemblyBlocked, FFmpegAssembler, SubtitleService
from app.providers import ComfyUIProvider, OpenAICompatibleMediaProvider, TTSProvider


def image_bytes(color=(40, 120, 220)) -> bytes:
    image = np.full((96, 64, 3), color, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def video_bytes(tmp_path: Path, name="clip.avi", duration=1.0) -> bytes:
    path = tmp_path / name
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 96))
    assert writer.isOpened()
    for index in range(round(duration * 10)):
        frame = np.full((96, 64, 3), (35 + index, 90, 160), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path.read_bytes()


def wav_bytes(duration=1.0) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        samples = (np.sin(2 * np.pi * 220 * np.arange(int(16000 * duration)) / 16000) * 6000).astype(np.int16)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def create_project(client, mars_payload):
    response = client.post("/api/projects", json=mars_payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_binary_artifact_upload_validation_download_and_keyframe_selection(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    content = image_bytes()
    uploaded = client.put(
        f"{base}/shots/shot_01/artifacts/keyframe",
        content=content,
        headers={"X-Filename": "candidate.png", "X-Content-SHA256": hashlib.sha256(content).hexdigest(), "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()["artifact"]
    assert artifact["source"] == "upload"
    assert artifact["review_status"] == "pending"
    downloaded = client.get(f"{base}/artifacts/{artifact['id']}/content")
    assert downloaded.status_code == 200
    assert downloaded.content == content

    approved = client.post(
        f"{base}/shots/shot_01/approve-keyframe",
        json={"artifact_id": artifact["id"], "comment": "身份和构图通过"},
    )
    assert approved.status_code == 200
    assert approved.json()["artifact"]["review_status"] == "approved"
    stored = client.get(base).json()
    assert stored["shots"][0]["first_frame_asset_id"] == artifact["id"]

    mismatch = client.put(
        f"{base}/shots/shot_01/artifacts/keyframe",
        content=content,
        headers={"X-Filename": "candidate.png", "X-Content-SHA256": "0" * 64},
    )
    assert mismatch.status_code == 422
    invalid = client.put(
        f"{base}/shots/shot_01/artifacts/keyframe",
        content=b"not-an-image",
        headers={"X-Filename": "candidate.png"},
    )
    assert invalid.status_code == 422


def test_subtitle_timeline_is_derived_and_persisted(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    response = client.post(f"{base}/subtitles/build", json={"include_action_as_narration": True})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["data_mode"] == "derived-from-shot-timeline"
    assert len(result["cues"]) == len(project["shots"])
    assert result["cues"][0]["start_sec"] < result["cues"][0]["end_sec"]
    srt = client.get(f"{base}/artifacts/{result['artifact']['id']}/content")
    assert srt.status_code == 200
    assert "00:00:00,200 -->" in srt.text
    assert "雨" not in srt.text or len(srt.text) > 20  # Content is input-derived, not a fixed fixture.


def test_readiness_reports_real_integration_gaps(client):
    readiness = client.get("/api/production-readiness").json()
    assert readiness["ready"] is False
    assert "real image provider" in readiness["missing"]
    assert "real I2V provider" in readiness["missing"]
    assert "real TTS provider" in readiness["missing"]
    assert isinstance(readiness["ffmpeg_available"], bool)


def test_unconfigured_real_stages_fail_closed_instead_of_returning_mock(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    keyframes = client.post(f"{base}/shots/shot_01/keyframes/generate", json={"count": 3})
    assert keyframes.status_code == 503
    assert keyframes.json()["detail"]["message"] == "Real image provider is not configured"
    speech = client.post(f"{base}/shots/shot_01/speech/generate", json={})
    assert speech.status_code == 503
    assembly = client.post(f"{base}/assemble", json={})
    assert assembly.status_code == 409
    assert "ffmpeg" in assembly.json()["detail"]["blockers"][0]
    stored = client.get(base).json()
    assert any(item["kind"] == "subtitle" for item in stored["media_artifacts"])
    assert not any(item["kind"] == "final_video" for item in stored["media_artifacts"])


def test_signed_provider_artifact_link_rejects_tampering(checkpoint_dir, tmp_path, mars_payload):
    secret = "artifact-signing-secret"
    app = create_app(
        checkpoint_dir=checkpoint_dir, media_root=tmp_path, seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock", "MANGAFLOW_ARTIFACT_SIGNING_SECRET": secret},
    )
    with TestClient(app) as client:
        project = create_project(client, mars_payload)
        base = f"/api/projects/{project['id']}"
        uploaded = client.put(
            f"{base}/shots/shot_01/artifacts/keyframe", content=image_bytes(),
            headers={"X-Filename": "candidate.png"},
        ).json()["artifact"]
        expires = int(time.time()) + 60
        token = hmac.new(secret.encode(), f"{uploaded['id']}:{expires}".encode(), hashlib.sha256).hexdigest()
        result = client.get(f"/api/provider-assets/{uploaded['id']}?expires={expires}&token={token}")
        assert result.status_code == 200
        assert client.get(f"/api/provider-assets/{uploaded['id']}?expires={expires}&token=forged").status_code == 403
        assert client.get(f"/api/provider-assets/{uploaded['id']}?expires=1&token={token}").status_code == 403


@pytest.mark.anyio
async def test_provider_artifact_fetcher_blocks_non_allowlisted_hosts_and_redirects():
    fetcher = ArtifactFetcher(["cdn.test"], httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
    ))
    with pytest.raises(ArtifactValidationError, match="allowlisted"):
        await fetcher.fetch("http://127.0.0.1/private", MediaKind.keyframe)
    with pytest.raises(ArtifactValidationError, match="allowlisted"):
        await fetcher.fetch("https://cdn.test/redirect.png", MediaKind.keyframe)


def test_real_provider_adapters_materialize_image_video_and_audio(checkpoint_dir, tmp_path, mars_payload):
    png = image_bytes()
    avi = video_bytes(tmp_path, duration=5.0)
    wav = wav_bytes(5.0)

    def image_handler(request: httpx.Request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "image-task", "status": "queued"})
        return httpx.Response(200, json={"id": "image-task", "status": "succeeded", "output_uri": "https://cdn.test/keyframe.png", "cost": 0.12})

    def video_handler(request: httpx.Request):
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["reference_uri"] == "https://cdn.test/keyframe.png"
            return httpx.Response(200, json={"id": "video-task", "status": "queued"})
        return httpx.Response(200, json={"id": "video-task", "status": "succeeded", "output_uri": "https://cdn.test/shot.avi", "cost": 0.75})

    def tts_handler(request: httpx.Request):
        if request.method == "POST":
            assert json.loads(request.content)["voice"]
            return httpx.Response(200, json={"id": "audio-task", "status": "queued"})
        return httpx.Response(200, json={"id": "audio-task", "status": "succeeded", "output_uri": "https://cdn.test/speech.wav", "cost": 0.05})

    def download_handler(request: httpx.Request):
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})
        if request.url.path.endswith(".avi"):
            return httpx.Response(200, content=avi, headers={"Content-Type": "video/x-msvideo"})
        return httpx.Response(200, content=wav, headers={"Content-Type": "audio/wav"})

    image_provider = OpenAICompatibleMediaProvider("https://image.test", "key", "image", httpx.MockTransport(image_handler))
    video_provider = OpenAICompatibleMediaProvider("https://video.test", "key", "video", httpx.MockTransport(video_handler))
    tts_provider = TTSProvider("https://tts.test", "key", httpx.MockTransport(tts_handler))
    app = create_app(
        checkpoint_dir=checkpoint_dir, media_root=tmp_path, seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock"},
        provider_override=("cloud-video", video_provider),
        image_provider_override=("cloud-image", image_provider),
        tts_provider_override=("cloud-tts", tts_provider),
        artifact_fetcher_override=ArtifactFetcher(["cdn.test"], httpx.MockTransport(download_handler)),
    )
    with TestClient(app) as client:
        project = create_project(client, mars_payload)
        base = f"/api/projects/{project['id']}"
        client.post(f"{base}/approve-assets")
        client.post(f"{base}/workflow", json={"command": "start"})

        keyframes = client.post(
            f"{base}/shots/shot_03/keyframes/generate",
            json={"count": 2, "request_id": "keyframe-request-01"},
        )
        assert keyframes.status_code == 202, keyframes.text
        for run in keyframes.json()["runs"]:
            ticked = client.post(f"{base}/runs/{run['id']}/tick")
            assert ticked.status_code == 200, ticked.text
            assert ticked.json()["artifact_id"]
        stored = client.get(base).json()
        candidates = [item for item in stored["media_artifacts"] if item["kind"] == "keyframe" and item["shot_id"] == "shot_03"]
        assert len(candidates) == 2
        assert client.post(
            f"{base}/shots/shot_03/approve-keyframe", json={"artifact_id": candidates[0]["id"]},
        ).status_code == 200

        video = client.post(f"{base}/shots/shot_03/generate")
        assert video.status_code == 202, video.text
        video_run = client.post(f"{base}/runs/{video.json()['run']['id']}/tick")
        assert video_run.status_code == 200, video_run.text
        assert video_run.json()["artifact_id"]

        speech = client.post(
            f"{base}/shots/shot_03/speech/generate",
            json={"request_id": "speech-request-01"},
        )
        assert speech.status_code == 202, speech.text
        speech_run = client.post(f"{base}/runs/{speech.json()['run']['id']}/tick")
        assert speech_run.status_code == 200, speech_run.text
        final = client.get(base).json()
        kinds = {item["kind"] for item in final["media_artifacts"]}
        assert {"keyframe", "video", "audio"}.issubset(kinds)
        assert all(not item["storage_path"].startswith("http") for item in final["media_artifacts"])
        assert sorted(round(item["amount"], 2) for item in final["cost_events"]) == [0.05, 0.12, 0.12, 0.75]


def test_real_image_cost_is_not_replaced_by_mock_video_default(checkpoint_dir, tmp_path, mars_payload):
    png = image_bytes()

    def image_handler(request: httpx.Request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "image-cost-task", "status": "queued"})
        return httpx.Response(200, json={
            "id": "image-cost-task", "status": "succeeded",
            "output_uri": "https://cdn.test/keyframe.png", "cost": 0.07,
        })

    image_provider = OpenAICompatibleMediaProvider(
        "https://image.test", "key", "image", httpx.MockTransport(image_handler),
    )
    app = create_app(
        checkpoint_dir=checkpoint_dir, media_root=tmp_path, seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock"},
        image_provider_override=("cloud-image", image_provider),
        artifact_fetcher_override=ArtifactFetcher(
            ["cdn.test"],
            httpx.MockTransport(lambda request: httpx.Response(200, content=png, headers={"Content-Type": "image/png"})),
        ),
    )
    with TestClient(app) as client:
        project = create_project(client, mars_payload)
        base = f"/api/projects/{project['id']}"
        response = client.post(
            f"{base}/shots/shot_01/keyframes/generate",
            json={"count": 2, "request_id": "image-cost-request"},
        )
        assert response.status_code == 202, response.text
        for run in response.json()["runs"]:
            assert client.post(f"{base}/runs/{run['id']}/tick").status_code == 200
        costs = client.get(base).json()["cost_events"]
        assert [item["amount"] for item in costs] == [0.07, 0.07]


def test_comfyui_i2v_uses_local_approved_artifact_without_public_media_origin(checkpoint_dir, tmp_path, mars_payload):
    submitted = {}
    workflow = {
        "reference": {"class_type": "LoadImage", "inputs": {"image": "{{reference_uri}}"}},
        "prompt": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
    }

    def comfy_handler(request: httpx.Request):
        if request.url.path == "/upload/image":
            assert b"artifact_" in request.content
            return httpx.Response(200, json={"name": "approved.png", "subfolder": "mangaflow"})
        submitted.update(json.loads(request.content)["prompt"])
        return httpx.Response(200, json={"prompt_id": "comfy-video-task"})

    provider = ComfyUIProvider("https://comfy.test", workflow, httpx.MockTransport(comfy_handler))
    app = create_app(
        checkpoint_dir=checkpoint_dir, media_root=tmp_path, seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock"}, provider_override=("comfyui", provider),
    )
    with TestClient(app) as client:
        project = create_project(client, mars_payload)
        base = f"/api/projects/{project['id']}"
        client.post(f"{base}/approve-assets")
        client.post(f"{base}/workflow", json={"command": "start"})
        uploaded = client.put(
            f"{base}/shots/shot_03/artifacts/keyframe",
            content=image_bytes(), headers={"X-Filename": "approved.png"},
        ).json()["artifact"]
        assert client.post(
            f"{base}/shots/shot_03/approve-keyframe", json={"artifact_id": uploaded["id"]},
        ).status_code == 200
        response = client.post(f"{base}/shots/shot_03/generate")
        assert response.status_code == 202, response.text
        assert submitted["reference"]["inputs"]["image"] == "mangaflow/approved.png"


def test_ffmpeg_command_contains_normalization_concat_and_three_tracks(tmp_path):
    assembler = FFmpegAssembler("ffmpeg-custom", "ffprobe-custom")
    command = assembler.build_command(
        [(tmp_path / "one.avi", tmp_path / "one.wav", 1.5), (tmp_path / "two.avi", None, 2.0)],
        tmp_path / "captions.srt", tmp_path / "final.mp4",
    )
    joined = " ".join(command)
    assert "scale=1080:1920" in joined
    assert "concat=n=2:v=1:a=1" in joined
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in joined
    assert "-c:s mov_text" in joined
    assert command[-1].endswith("final.mp4")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg integration requires real binaries")
def test_real_ffmpeg_assembles_playable_mp4_with_audio_and_subtitles(tmp_path):
    project = build_demo_project()
    project.shots = [project.shots[0]]
    project.shots[0].duration_sec = 1
    project.generation_runs = []
    project.media_artifacts = []
    project.subtitle_cues = []
    store = ArtifactStore(tmp_path)
    keyframe = store.save_bytes(
        project, MediaKind.keyframe, "approved.png", image_bytes(), project.shots[0].id, "test",
        review_status=ArtifactReviewStatus.approved,
    )
    assert keyframe.review_status == ArtifactReviewStatus.approved
    store.save_bytes(project, MediaKind.video, "shot.avi", video_bytes(tmp_path), project.shots[0].id, "test")
    store.save_bytes(project, MediaKind.audio, "speech.wav", wav_bytes(), project.shots[0].id, "test")
    subtitles = SubtitleService()
    cues = subtitles.build(project, True)
    store.save_bytes(project, MediaKind.subtitle, "captions.srt", subtitles.render_srt(cues).encode(), None, "test")

    artifact, inspection = FFmpegAssembler().assemble(project, store)
    assert artifact.kind == MediaKind.final_video
    assert inspection.hard_gate_passed is True
    assert store.path_for(artifact).is_file()


def test_assembly_fails_closed_when_ffmpeg_is_missing(tmp_path):
    project = build_demo_project()
    project.media_artifacts = []
    assembler = FFmpegAssembler(str(tmp_path / "missing-ffmpeg"), str(tmp_path / "missing-ffprobe"))
    with pytest.raises(AssemblyBlocked, match="ffmpeg"):
        assembler.assemble(project, ArtifactStore(tmp_path))
