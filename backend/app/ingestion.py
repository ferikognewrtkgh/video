from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from .domain import SourceDocument, SourceDocumentRequest, SourceSegment


class DocumentIngestionError(ValueError):
    pass


class DocumentIngestor:
    SUPPORTED = {".txt", ".md", ".markdown", ".docx", ".json"}
    MAX_BYTES = 5 * 1024 * 1024

    def ingest(self, request: SourceDocumentRequest) -> SourceDocument:
        suffix = Path(request.filename).suffix.lower()
        if suffix not in self.SUPPORTED:
            raise DocumentIngestionError(f"Unsupported document format: {suffix or 'none'}")
        raw = self._decode(request)
        if len(raw) > self.MAX_BYTES:
            raise DocumentIngestionError("Source document exceeds the 5 MiB limit")
        if suffix == ".docx":
            text = self._docx_text(raw)
        else:
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DocumentIngestionError("Text documents must be UTF-8 encoded") from exc
            text = self._structured_json_text(decoded) if suffix == ".json" else decoded
        text = self._normalize(text)
        if len(text) < 20:
            raise DocumentIngestionError("Document contains too little readable text")
        segments = self._segment(text, request.max_segment_chars, request.overlap_chars)
        return SourceDocument(
            filename=Path(request.filename).name,
            format=suffix.removeprefix("."),
            text=text,
            segments=segments,
            checksum_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _decode(self, request: SourceDocumentRequest) -> bytes:
        if bool(request.content_text) == bool(request.content_base64):
            raise DocumentIngestionError("Provide exactly one of content_text or content_base64")
        if request.content_text is not None:
            return request.content_text.encode("utf-8")
        try:
            return base64.b64decode(request.content_base64 or "", validate=True)
        except ValueError as exc:
            raise DocumentIngestionError("content_base64 is invalid") from exc

    def _docx_text(self, raw: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > self.MAX_BYTES * 4:
                    raise DocumentIngestionError("DOCX expanded content is too large")
                root = ElementTree.fromstring(archive.read(info))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise DocumentIngestionError("Invalid DOCX document") from exc
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(f"{namespace}p"):
            value = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if value.strip():
                paragraphs.append(value.strip())
        return "\n\n".join(paragraphs)

    def _structured_json_text(self, text: str) -> str:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DocumentIngestionError("Structured screenplay JSON is invalid") from exc
        if not isinstance(payload, (dict, list)):
            raise DocumentIngestionError("Structured screenplay JSON must be an object or array")
        lines: list[str] = []

        def walk(value, key=""):
            if isinstance(value, dict):
                for child_key, child in value.items():
                    walk(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    walk(child, key)
            elif isinstance(value, (str, int, float)) and str(value).strip():
                label = key.replace("_", " ").strip()
                lines.append(f"{label}: {value}" if label else str(value))

        walk(payload)
        return "\n".join(lines)

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _segment(self, text: str, maximum: int, overlap: int) -> list[SourceSegment]:
        if overlap >= maximum:
            raise DocumentIngestionError("overlap_chars must be smaller than max_segment_chars")
        segments: list[SourceSegment] = []
        start = 0
        while start < len(text):
            hard_end = min(start + maximum, len(text))
            end = hard_end
            if hard_end < len(text):
                candidates = [
                    text.rfind("\n\n", start + maximum // 2, hard_end),
                    text.rfind("。", start + maximum // 2, hard_end),
                    text.rfind(".", start + maximum // 2, hard_end),
                ]
                boundary = max(candidates)
                if boundary > start:
                    end = boundary + (2 if text[boundary:boundary + 2] == "\n\n" else 1)
            chunk = text[start:end].strip()
            if chunk:
                segments.append(SourceSegment(
                    index=len(segments), text=chunk, start_char=start, end_char=end,
                    checksum_sha256=hashlib.sha256(chunk.encode()).hexdigest(),
                ))
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
        return segments
