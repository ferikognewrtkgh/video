from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urlparse
from uuid import uuid4

import cv2
import httpx
import numpy as np

from .domain import (
    ArtifactReviewStatus,
    MediaArtifact,
    MediaKind,
    ProductionReadiness,
    Project,
    SubtitleCue,
)
from .media_quality import MediaQualityAnalyzer


class ArtifactValidationError(ValueError):
    pass


class AssemblyBlocked(RuntimeError):
    def __init__(self, blockers: list[str]):
        super().__init__("; ".join(blockers))
        self.blockers = blockers


class AssemblyFailed(RuntimeError):
    pass


ALLOWED_SUFFIXES = {
    MediaKind.keyframe: {".png", ".jpg", ".jpeg", ".webp"},
    MediaKind.video: {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    MediaKind.audio: {".wav", ".mp3", ".m4a", ".aac", ".ogg"},
    MediaKind.subtitle: {".srt"},
    MediaKind.final_video: {".mp4"},
}

DEFAULT_SUFFIX = {
    MediaKind.keyframe: ".png",
    MediaKind.video: ".mp4",
    MediaKind.audio: ".wav",
    MediaKind.subtitle: ".srt",
    MediaKind.final_video: ".mp4",
}

MIME_BY_SUFFIX = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".webm": "video/webm", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".srt": "application/x-subrip",
}

MAX_BYTES = {
    MediaKind.keyframe: 20 * 1024 * 1024,
    MediaKind.video: 250 * 1024 * 1024,
    MediaKind.audio: 50 * 1024 * 1024,
    MediaKind.subtitle: 2 * 1024 * 1024,
    MediaKind.final_video: 1024 * 1024 * 1024,
}


def _safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    if not result or result in {".", ".."}:
        raise ArtifactValidationError("Invalid artifact path component")
    return result


class ArtifactStore:
    """Content-addressed local artifact storage with media validation."""

    def __init__(self, media_root: Path) -> None:
        self.media_root = media_root.resolve()

    def save_bytes(
        self,
        project: Project,
        kind: MediaKind,
        filename: str,
        content: bytes,
        shot_id: str | None,
        source: str,
        expected_sha256: str | None = None,
        provider: str | None = None,
        provider_task_id: str | None = None,
        mime_type: str | None = None,
        review_status: ArtifactReviewStatus | None = None,
        metadata: dict | None = None,
    ) -> MediaArtifact:
        if not content:
            raise ArtifactValidationError("Artifact body is empty")
        if len(content) > MAX_BYTES[kind]:
            raise ArtifactValidationError(f"{kind.value} artifact exceeds the size limit")
        suffix = Path(filename).suffix.lower() or DEFAULT_SUFFIX[kind]
        if suffix not in ALLOWED_SUFFIXES[kind]:
            raise ArtifactValidationError(f"Unsupported {kind.value} file extension: {suffix}")
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 and digest != expected_sha256.lower():
            raise ArtifactValidationError("Artifact SHA-256 does not match X-Content-SHA256")
        artifact_id = f"artifact_{uuid4().hex[:12]}"
        folder = self._folder(project.id, shot_id, kind)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{artifact_id}{suffix}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        try:
            self._validate_file(kind, temporary, suffix)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        relative = destination.relative_to(self.media_root).as_posix()
        artifact = MediaArtifact(
            id=artifact_id,
            organization_id=project.organization_id,
            project_id=project.id,
            shot_id=shot_id,
            kind=kind,
            storage_path=relative,
            mime_type=mime_type or MIME_BY_SUFFIX[suffix],
            checksum_sha256=digest,
            file_size=len(content),
            source=source,
            provider=provider,
            provider_task_id=provider_task_id,
            review_status=review_status or (
                ArtifactReviewStatus.pending if kind == MediaKind.keyframe else ArtifactReviewStatus.not_required
            ),
            measured=kind in {MediaKind.keyframe, MediaKind.video, MediaKind.audio, MediaKind.final_video},
            metadata=metadata or {},
        )
        project.media_artifacts.append(artifact)
        return artifact

    def register_existing(
        self,
        project: Project,
        kind: MediaKind,
        path: Path,
        shot_id: str | None,
        source: str,
        metadata: dict | None = None,
    ) -> MediaArtifact:
        path = path.resolve()
        self._ensure_inside_root(path)
        if not path.is_file():
            raise ArtifactValidationError("Artifact file does not exist")
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES[kind]:
            raise ArtifactValidationError(f"Unsupported {kind.value} file extension: {suffix}")
        self._validate_file(kind, path, suffix)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = MediaArtifact(
            id=f"artifact_{uuid4().hex[:12]}", organization_id=project.organization_id,
            project_id=project.id, shot_id=shot_id, kind=kind,
            storage_path=path.relative_to(self.media_root).as_posix(), mime_type=MIME_BY_SUFFIX[suffix],
            checksum_sha256=digest, file_size=path.stat().st_size, source=source, measured=True,
            metadata=metadata or {},
        )
        project.media_artifacts.append(artifact)
        return artifact

    def path_for(self, artifact: MediaArtifact) -> Path:
        path = (self.media_root / artifact.storage_path).resolve()
        self._ensure_inside_root(path)
        return path

    def output_path(self, project: Project) -> Path:
        folder = self._folder(project.id, None, MediaKind.final_video)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{project.id}-{uuid4().hex[:8]}.mp4"

    def _folder(self, project_id: str, shot_id: str | None, kind: MediaKind) -> Path:
        parts = [self.media_root, "projects", _safe_component(project_id)]
        if shot_id:
            parts.extend(["shots", _safe_component(shot_id)])
        parts.append(kind.value)
        folder = Path(*parts).resolve()
        self._ensure_inside_root(folder)
        return folder

    def _ensure_inside_root(self, path: Path) -> None:
        if path != self.media_root and self.media_root not in path.parents:
            raise ArtifactValidationError("Artifact path escapes MANGAFLOW_MEDIA_ROOT")

    @staticmethod
    def _validate_file(kind: MediaKind, path: Path, suffix: str) -> None:
        if kind == MediaKind.keyframe:
            data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
            if cv2.imdecode(data, cv2.IMREAD_COLOR) is None:
                raise ArtifactValidationError("Image artifact cannot be decoded")
        elif kind in {MediaKind.video, MediaKind.final_video}:
            capture = cv2.VideoCapture(str(path))
            ok, _ = capture.read() if capture.isOpened() else (False, None)
            capture.release()
            if not ok:
                raise ArtifactValidationError("Video artifact cannot be decoded")
        elif kind == MediaKind.audio:
            if suffix == ".wav":
                try:
                    with wave.open(str(path), "rb") as audio:
                        if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                            raise ArtifactValidationError("WAV artifact has no samples")
                except (wave.Error, EOFError) as exc:
                    raise ArtifactValidationError("WAV artifact cannot be decoded") from exc
            else:
                header = path.read_bytes()[:16]
                valid = header.startswith((b"ID3", b"OggS")) or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"} or b"ftyp" in header
                if not valid:
                    raise ArtifactValidationError("Audio artifact has an invalid container signature")
        elif kind == MediaKind.subtitle:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ArtifactValidationError("Subtitle artifact must be UTF-8") from exc
            if "-->" not in text:
                raise ArtifactValidationError("Subtitle artifact has no SRT cues")


@dataclass(frozen=True)
class DownloadedArtifact:
    content: bytes
    filename: str
    mime_type: str | None


class ArtifactFetcher:
    """Download only from explicitly trusted Provider output hosts."""

    def __init__(self, allowed_hosts: Iterable[str], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.allowed_hosts = {host.lower() for host in allowed_hosts if host}
        self.transport = transport

    async def fetch(self, uri: str, kind: MediaKind) -> DownloadedArtifact:
        current = uri
        for _ in range(4):
            parsed = urlparse(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname.lower() not in self.allowed_hosts:
                raise ArtifactValidationError("Provider output URI host is not allowlisted")
            async with httpx.AsyncClient(timeout=60, transport=self.transport, follow_redirects=False) as client:
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ArtifactValidationError("Provider redirect has no location")
                        current = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > MAX_BYTES[kind]:
                        raise ArtifactValidationError("Provider artifact exceeds the size limit")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES[kind]:
                            raise ArtifactValidationError("Provider artifact exceeds the size limit")
                        chunks.append(chunk)
                    filename = Path(unquote(parsed.path)).name or f"provider-output{DEFAULT_SUFFIX[kind]}"
                    if not Path(filename).suffix:
                        filename += DEFAULT_SUFFIX[kind]
                    return DownloadedArtifact(b"".join(chunks), filename, response.headers.get("content-type"))
        raise ArtifactValidationError("Provider output redirected too many times")


class SubtitleService:
    def build(self, project: Project, include_action_as_narration: bool = True) -> list[SubtitleCue]:
        cues: list[SubtitleCue] = []
        cursor = 0.0
        for shot in sorted(project.shots, key=lambda item: item.order):
            text = shot.dialogue.strip() or (shot.action.strip() if include_action_as_narration else "")
            if text:
                start = cursor + min(0.2, shot.duration_sec / 10)
                end = max(start + 0.1, cursor + shot.duration_sec - min(0.2, shot.duration_sec / 10))
                speaker = None
                if shot.characters:
                    character = next((item for item in project.characters if item.id == shot.characters[0]), None)
                    speaker = character.name if character else None
                cues.append(SubtitleCue(
                    id=f"subtitle_{uuid4().hex[:10]}", shot_id=shot.id, index=len(cues) + 1,
                    start_sec=round(start, 3), end_sec=round(end, 3), text=text, speaker=speaker,
                ))
            cursor += shot.duration_sec
        project.subtitle_cues = cues
        return cues

    def render_srt(self, cues: list[SubtitleCue]) -> str:
        return "\n".join(
            f"{index}\n{self._timestamp(cue.start_sec)} --> {self._timestamp(cue.end_sec)}\n{cue.text}\n"
            for index, cue in enumerate(cues, 1)
        )

    @staticmethod
    def _timestamp(seconds: float) -> str:
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def _default_runner(arguments: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)


class FFmpegAssembler:
    WIDTH = 1080
    HEIGHT = 1920
    FPS = 24

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        runner: Runner | None = None,
        analyzer: MediaQualityAnalyzer | None = None,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.runner = runner or _default_runner
        self.analyzer = analyzer or MediaQualityAnalyzer()

    def available(self, binary: str) -> bool:
        candidate = Path(binary)
        return candidate.is_file() if candidate.is_absolute() else shutil.which(binary) is not None

    def preflight(self, image_provider: str, video_provider: str, tts_provider: str) -> ProductionReadiness:
        ffmpeg = self.available(self.ffmpeg_path)
        ffprobe = self.available(self.ffprobe_path)
        missing = []
        if image_provider in {"disabled", "none", "mock"}:
            missing.append("real image provider")
        if video_provider == "mock":
            missing.append("real I2V provider")
        if tts_provider in {"disabled", "none", "mock"}:
            missing.append("real TTS provider")
        if not ffmpeg:
            missing.append("ffmpeg binary")
        if not ffprobe:
            missing.append("ffprobe binary")
        return ProductionReadiness(
            ready=not missing, image_provider=image_provider, video_provider=video_provider,
            tts_provider=tts_provider, ffmpeg_available=ffmpeg, ffprobe_available=ffprobe,
            missing=missing,
            notes=["Uploaded real artifacts may replace a missing generation Provider, but are never labeled as generated."],
        )

    def assemble(
        self,
        project: Project,
        artifacts: ArtifactStore,
        require_audio: bool = True,
        require_approved_keyframes: bool = True,
    ) -> tuple[MediaArtifact, object]:
        if not self.available(self.ffmpeg_path) or not self.available(self.ffprobe_path):
            raise AssemblyBlocked(["ffmpeg and ffprobe must be installed or configured"])
        inputs: list[tuple[Path, Path | None, float]] = []
        blockers: list[str] = []
        for shot in sorted(project.shots, key=lambda item: item.order):
            video = self._latest(project, MediaKind.video, shot.id)
            audio = self._latest(project, MediaKind.audio, shot.id)
            approved = any(
                item.shot_id == shot.id and item.kind == MediaKind.keyframe and item.review_status == ArtifactReviewStatus.approved
                for item in project.media_artifacts
            )
            if video is None:
                blockers.append(f"{shot.id}: missing real video artifact")
            elif video.metadata.get("inspection") and not video.metadata["inspection"].get("hard_gate_passed", False):
                blockers.append(f"{shot.id}: video artifact failed hard media gates")
            if require_audio and audio is None:
                blockers.append(f"{shot.id}: missing real audio artifact")
            if require_approved_keyframes and not approved:
                blockers.append(f"{shot.id}: no approved keyframe")
            if video:
                inputs.append((artifacts.path_for(video), artifacts.path_for(audio) if audio else None, shot.duration_sec))
        subtitle = self._latest(project, MediaKind.subtitle, None)
        if subtitle is None:
            blockers.append("project: missing generated SRT subtitle artifact")
        if blockers:
            raise AssemblyBlocked(blockers)
        output = artifacts.output_path(project)
        command = self.build_command(inputs, artifacts.path_for(subtitle), output)
        result = self.runner(command, 1800)
        if result.returncode != 0:
            stderr = (result.stderr or "")[-4000:]
            output.unlink(missing_ok=True)
            raise AssemblyFailed(f"FFmpeg exited with {result.returncode}: {stderr}")
        try:
            inspection = self.analyzer.inspect(
                output, sum(item.duration_sec for item in project.shots), self.WIDTH, self.HEIGHT,
            )
            if not inspection.hard_gate_passed:
                raise AssemblyFailed(f"Assembled video failed hard media gates: {inspection.failures}")
            streams = self._probe(output)
            stream_types = {item.get("codec_type") for item in streams}
            if not {"video", "audio", "subtitle"}.issubset(stream_types):
                raise AssemblyFailed("Final MP4 must contain video, audio and subtitle streams")
        except Exception:
            output.unlink(missing_ok=True)
            raise
        artifact = artifacts.register_existing(
            project, MediaKind.final_video, output, None, "ffmpeg",
            {"inspection": inspection.model_dump(), "stream_types": sorted(stream_types)},
        )
        return artifact, inspection

    def build_command(self, inputs: list[tuple[Path, Path | None, float]], subtitle: Path, output: Path) -> list[str]:
        command = [self.ffmpeg_path, "-y"]
        filters, concat_inputs = [], []
        input_index = 0
        for position, (video, audio, duration) in enumerate(inputs):
            command.extend(["-i", str(video)])
            video_index = input_index
            input_index += 1
            if audio:
                command.extend(["-i", str(audio)])
            else:
                command.extend(["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            audio_index = input_index
            input_index += 1
            filters.append(
                f"[{video_index}:v]scale={self.WIDTH}:{self.HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={self.WIDTH}:{self.HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,fps={self.FPS},"
                f"tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration},setpts=PTS-STARTPTS[v{position}]"
            )
            filters.append(
                f"[{audio_index}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{position}]"
            )
            concat_inputs.append(f"[v{position}][a{position}]")
        command.extend(["-i", str(subtitle)])
        subtitle_index = input_index
        filters.append(f"{''.join(concat_inputs)}concat=n={len(inputs)}:v=1:a=1[vout][aout]")
        command.extend([
            "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
            "-map", f"{subtitle_index}:s:0", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-c:s", "mov_text", "-movflags", "+faststart",
            "-metadata:s:s:0", "language=zho", str(output),
        ])
        return command

    def _probe(self, output: Path) -> list[dict]:
        result = self.runner([
            self.ffprobe_path, "-v", "error", "-show_streams", "-of", "json", str(output),
        ], 60)
        if result.returncode != 0:
            raise AssemblyFailed(f"ffprobe failed: {(result.stderr or '')[-2000:]}")
        try:
            return json.loads(result.stdout or "{}").get("streams", [])
        except json.JSONDecodeError as exc:
            raise AssemblyFailed("ffprobe returned invalid JSON") from exc

    @staticmethod
    def _latest(project: Project, kind: MediaKind, shot_id: str | None) -> MediaArtifact | None:
        return next((
            item for item in reversed(project.media_artifacts)
            if item.kind == kind and item.shot_id == shot_id
        ), None)
