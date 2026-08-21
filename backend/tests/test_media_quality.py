from pathlib import Path

import cv2
import numpy as np

from app.media_quality import MediaQualityAnalyzer


def _video(path: Path, frames: list[np.ndarray], fps: float = 10) -> Path:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    assert writer.isOpened(), "OpenCV MJPG writer is required by media tests"
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def _solid(color, count=20):
    return [np.full((64, 96, 3), color, dtype=np.uint8) for _ in range(count)]


def test_stable_real_video_passes_decode_duration_resolution_and_reference(tmp_path):
    frames = _solid((40, 80, 120))
    path = _video(tmp_path / "stable.avi", frames)
    reference = tmp_path / "reference.jpg"
    cv2.imwrite(str(reference), frames[0])
    result = MediaQualityAnalyzer().inspect(path, 2.0, 96, 64, reference)
    assert result.decodable
    assert result.hard_gate_passed
    assert result.duration_sec == 2.0
    assert result.reference_similarity > 0.95
    assert result.metric_sources["flicker"].startswith("P90")


def test_black_and_flickering_video_fails_objective_gates(tmp_path):
    black, white = np.zeros((64, 96, 3), np.uint8), np.full((64, 96, 3), 255, np.uint8)
    path = _video(tmp_path / "flicker.avi", [black if index % 2 == 0 else white for index in range(20)])
    result = MediaQualityAnalyzer().inspect(path, 2.0)
    assert not result.hard_gate_passed
    assert "black_frames" in result.failures
    assert "flicker" in result.failures
    assert result.black_frame_ratio >= 0.45


def test_duration_resolution_and_identity_drift_are_measured_from_files(tmp_path):
    path = _video(tmp_path / "short_blue.avi", _solid((255, 0, 0), count=5))
    reference = tmp_path / "red_reference.jpg"
    cv2.imwrite(str(reference), _solid((0, 0, 255), count=1)[0])
    result = MediaQualityAnalyzer().inspect(path, 2.0, 192, 108, reference)
    assert set(result.failures) >= {"duration_mismatch", "resolution_mismatch", "identity_drift"}
    assert result.duration_sec == 0.5
    assert result.reference_similarity < 0.55


def test_missing_or_invalid_media_fails_decode(tmp_path):
    missing = MediaQualityAnalyzer().inspect(tmp_path / "missing.avi", 1)
    assert missing.failures == ["media_missing"]
    broken = tmp_path / "broken.avi"
    broken.write_bytes(b"not a video")
    result = MediaQualityAnalyzer().inspect(broken, 1)
    assert result.failures == ["decode_failed"]


def test_media_inspection_is_exposed_over_http(client, tmp_path, mars_payload):
    project = client.post("/api/projects", json=mars_payload).json()
    path = _video(tmp_path / "http_stable.avi", _solid((40, 80, 120)))
    response = client.post(f"/api/projects/{project['id']}/shots/shot_01/inspect-media", json={
        "media_path": str(path), "expected_duration_sec": 2.0,
        "expected_width": 96, "expected_height": 64,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric_source"] == "decoded-media"
    assert payload["inspection"]["hard_gate_passed"] is True
