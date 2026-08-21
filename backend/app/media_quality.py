from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .domain import MediaInspection


class MediaQualityAnalyzer:
    """Objective video inspection using decoded frames, not caller-provided scores."""

    def inspect(
        self,
        media_path: Path,
        expected_duration_sec: float,
        expected_width: int | None = None,
        expected_height: int | None = None,
        reference_image_path: Path | None = None,
    ) -> MediaInspection:
        failures: list[str] = []
        if not media_path.is_file():
            return MediaInspection(decodable=False, hard_gate_passed=False, failures=["media_missing"])
        capture = cv2.VideoCapture(str(media_path))
        if not capture.isOpened():
            return MediaInspection(decodable=False, hard_gate_passed=False, failures=["decode_failed"])
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        declared_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        luminance: list[float] = []
        black_frames = 0
        first_frame = None
        decoded = 0
        while decoded < 1200:
            ok, frame = capture.read()
            if not ok:
                break
            if first_frame is None:
                first_frame = frame.copy()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean = float(gray.mean())
            luminance.append(mean)
            black_frames += int(mean < 8.0)
            decoded += 1
        capture.release()
        if decoded == 0 or fps <= 0 or width <= 0 or height <= 0:
            return MediaInspection(decodable=False, hard_gate_passed=False, failures=["decode_failed"])
        frame_count = declared_count if declared_count > 0 else decoded
        duration = frame_count / fps
        black_ratio = black_frames / decoded
        changes = np.abs(np.diff(np.asarray(luminance, dtype=np.float32))) / 255 if len(luminance) > 1 else np.array([0.0])
        # Repeated large luminance jumps are a useful deterministic proxy for flicker.
        flicker = float(np.clip(np.percentile(changes, 90) * 2.2, 0, 1))
        similarity = self._reference_similarity(first_frame, reference_image_path)
        duration_tolerance = max(0.35, expected_duration_sec * 0.12)
        if abs(duration - expected_duration_sec) > duration_tolerance:
            failures.append("duration_mismatch")
        if expected_width and width != expected_width:
            failures.append("resolution_mismatch")
        if expected_height and height != expected_height and "resolution_mismatch" not in failures:
            failures.append("resolution_mismatch")
        if black_ratio > 0.08:
            failures.append("black_frames")
        if flicker > 0.22:
            failures.append("flicker")
        if similarity is not None and similarity < 0.55:
            failures.append("identity_drift")
        return MediaInspection(
            decodable=True, width=width, height=height, fps=round(fps, 3),
            duration_sec=round(duration, 3), frame_count=frame_count,
            black_frame_ratio=round(black_ratio, 4), flicker_score=round(flicker, 4),
            reference_similarity=None if similarity is None else round(similarity, 4),
            hard_gate_passed=not failures, failures=failures,
            metric_sources={
                "decode": "OpenCV VideoCapture",
                "duration": "decoded frame count / FPS",
                "black_frames": "frame luminance threshold",
                "flicker": "P90 consecutive-frame luminance delta",
                "reference_similarity": "first-frame/reference normalized pixel similarity" if reference_image_path else "not measured",
            },
        )

    @staticmethod
    def _reference_similarity(frame, reference_path: Path | None) -> float | None:
        if frame is None or reference_path is None or not reference_path.is_file():
            return None
        reference = cv2.imread(str(reference_path))
        if reference is None:
            return None
        sample = cv2.resize(frame, (96, 96)).astype(np.float32) / 255
        reference = cv2.resize(reference, (96, 96)).astype(np.float32) / 255
        mse = float(np.mean((sample - reference) ** 2))
        return float(np.clip(1 - mse * 4, 0, 1))

