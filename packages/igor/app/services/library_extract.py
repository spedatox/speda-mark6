"""Bounded deterministic extraction for Iacon Hall of Records.

Original text is evidence. Parser errors and unsupported OCR are never passages.
"""
import csv
import io
from dataclasses import dataclass


class ExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedPassage:
    content: str
    page: int | None = None
    section: str | None = None
    needs_review: bool = False


def chunk_text(text: str, max_length: int = 2000) -> list[str]:
    """Keep paragraph/line boundaries where possible; enforce the hard bound."""
    if max_length < 64:
        raise ValueError("Chunk limit must be at least 64 characters")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = []
    while text:
        end = min(len(text), max_length)
        if end < len(text):
            boundary = max(text.rfind("\n", 0, end), text.rfind(" ", 0, end))
            if boundary >= end // 2:
                end = boundary
        chunk = text[:end].strip()
        if chunk:
            chunks.append(chunk)
        text = text[end:].lstrip()
    return chunks


def extract_document_bytes(blob: bytes, mime_type: str, *, max_bytes: int = 25 * 1024**2,
                           max_pages: int = 200, max_characters: int = 2_000_000) -> list[ExtractedPassage]:
    if not blob or len(blob) > max_bytes:
        raise ExtractionError("Original is empty or exceeds the extraction size limit")
    passages = []
    if mime_type == "application/pdf":
        from pypdf import PdfReader
        try:
            if not blob.startswith(b"%PDF-"):
                raise ExtractionError("Original is not a PDF")
            reader = PdfReader(io.BytesIO(blob))
            if reader.is_encrypted:
                raise ExtractionError("Encrypted PDFs cannot be indexed")
            if len(reader.pages) > max_pages:
                raise ExtractionError("PDF exceeds the page limit")
            total = 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > max_characters:
                    raise ExtractionError("Extracted text exceeds the character limit")
                if not text.strip():
                    raise ExtractionError(f"Page {number} has no extractable text; OCR or owner review is required")
                passages.extend(ExtractedPassage(c, page=number) for c in chunk_text(text))
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("PDF could not be parsed") from exc
    elif mime_type in {"text/plain", "text/markdown", "text/csv"}:
        try:
            text = blob.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ExtractionError("Text must be UTF-8 encoded") from exc
        if "\x00" in text:
            raise ExtractionError("Binary content cannot be indexed as text")
        if len(text) > max_characters:
            raise ExtractionError("Extracted text exceeds the character limit")
        if mime_type == "text/csv":
            rows = csv.reader(io.StringIO(text))
            header = next(rows, [])
            for number, row in enumerate(rows, 2):
                line = " | ".join(f"{header[i] if i < len(header) else i + 1}: {v}" for i, v in enumerate(row))
                passages.extend(ExtractedPassage(c, section=f"Row {number}") for c in chunk_text(line))
        else:
            passages.extend(ExtractedPassage(c) for c in chunk_text(text))
    else:
        raise ExtractionError(f"Extraction unavailable for {mime_type}; original retained")
    if not passages:
        raise ExtractionError("Original contains no extractable text")
    if sum(len(p.content) for p in passages) > max_characters:
        raise ExtractionError("Extracted text exceeds the character limit")
    return passages