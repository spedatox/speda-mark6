"""Bounded deterministic extraction for Iacon Hall of Records.

Original text is evidence. OCR is labeled transcription; parser failures never become passages.
"""
import csv
import io
import zipfile
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
                           max_pages: int = 200, max_characters: int = 2_000_000,
                           ocr=None) -> list[ExtractedPassage]:
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
                    if ocr is None:
                        raise ExtractionError(f"Page {number} has no extractable text; OCR or owner review is required")
                    text = ocr.pdf_page(blob, number)
                    total += len(text)
                    if total > max_characters or not text.strip():
                        raise ExtractionError(f"Page {number} OCR is empty or exceeds the text limit")
                    passages.extend(ExtractedPassage(c, page=number, section="OCR transcription", needs_review=True)
                                    for c in chunk_text(text))
                    continue
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
    elif mime_type in {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}:
        passages = extract_office(blob, mime_type, max_characters=max_characters)
    elif mime_type in {"image/png", "image/jpeg"} and ocr is not None:
        text = ocr.image(blob, mime_type)
        if len(text) > max_characters:
            raise ExtractionError("OCR text exceeds the character limit")
        passages.extend(ExtractedPassage(c, section="OCR transcription", needs_review=True) for c in chunk_text(text))
    else:
        raise ExtractionError(f"Extraction unavailable for {mime_type}; original retained")
    if not passages:
        raise ExtractionError("Original contains no extractable text")
    if sum(len(p.content) for p in passages) > max_characters:
        raise ExtractionError("Extracted text exceeds the character limit")
    return passages


def extract_office(blob, mime_type, *, max_characters):
    """Bound the ZIP before parsing; keep structural locators, never calculate formulas."""
    passages, characters = [], 0

    def add(value, section):
        nonlocal characters
        characters += len(value)
        if characters > max_characters:
            raise ExtractionError("Extracted text exceeds the character limit")
        passages.extend(ExtractedPassage(c, section=section) for c in chunk_text(value))

    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            members = archive.infolist()
            if (len(members) > 2000 or len({m.filename for m in members}) != len(members)
                    or sum(m.file_size for m in members) > 100 * 1024**2
                    or any(m.file_size > 50 * 1024**2 or m.flag_bits & 1 for m in members)):
                raise ExtractionError("Office archive exceeds safe extraction limits")
            for member in members:
                if member.filename.endswith(".xml"):
                    xml = archive.read(member)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ExtractionError("Office XML entity declarations are unsupported")
                if member.filename.startswith(("word/media/", "word/charts/", "word/embeddings/",
                                               "xl/media/", "xl/charts/", "xl/embeddings/", "xl/drawings/")):
                    raise ExtractionError("Office document contains visual or embedded evidence; owner review is required")
            if "word/document.xml" in archive.namelist():
                xml = archive.read("word/document.xml")
                from lxml import etree
                root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
                if any(etree.QName(node).localname in {"txbxContent", "ins", "del", "altChunk", "sdt", "fldSimple"}
                       for node in root.iter() if isinstance(node.tag, str)):
                    raise ExtractionError("Word document contains unsupported text boxes or revision markup")
            if any(m.filename in {"word/footnotes.xml", "word/endnotes.xml"} for m in members):
                raise ExtractionError("Word notes require owner review")
        if mime_type.endswith("wordprocessingml.document"):
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            document = Document(io.BytesIO(blob))
            heading = "Document body"
            table_number = 0
            # iter_inner_content preserves paragraph/table order, including hyperlinks.
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    if block.style and block.style.name.startswith("Heading") and block.text.strip():
                        heading = block.text.strip()[:200]
                    add(block.text, heading)
                elif isinstance(block, Table):
                    table_number += 1
                    for number, row in enumerate(block.rows, 1):
                        if any(cell.tables for cell in row.cells):
                            raise ExtractionError("Nested Word tables require owner review")
                        add(" | ".join(cell.text for cell in row.cells), f"{heading} / Table {table_number}, row {number}")
            for number, section in enumerate(document.sections, 1):
                for name in ("header", "footer", "first_page_header", "first_page_footer", "even_page_header", "even_page_footer"):
                    part = getattr(section, name)
                    if part.is_linked_to_previous:
                        continue
                    for paragraph in part.paragraphs:
                        add(paragraph.text, f"Section {number} {name}")
                    if part.tables:
                        raise ExtractionError("Word header/footer tables require owner review")
        else:
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=False, keep_links=False)
            try:
                if len(workbook.worksheets) > 100:
                    raise ExtractionError("Workbook exceeds 100 sheets")
                for sheet in workbook.worksheets:
                    if (sheet.max_row or 0) > 100000 or (sheet.max_column or 0) > 256:
                        raise ExtractionError("Worksheet exceeds 100000 rows or 256 columns")
                    sheet.reset_dimensions()  # Declared dimensions may understate the actual cells.
                    context = None
                    for number, row in enumerate(sheet.iter_rows(), 1):
                        if number > 100000 or len(row) > 256:
                            raise ExtractionError("Worksheet exceeds 100000 rows or 256 columns")
                        cells = [f"{cell.coordinate}{' formula' if cell.data_type == 'f' else ''}: {cell.value}"
                                 for cell in row if cell.value is not None]
                        if not cells:
                            continue
                        value = " | ".join(cells)
                        first = context is None
                        if first:
                            context = value
                        else:
                            value = f"First populated row: {context}\n{value}"
                        add(value, f"Sheet {sheet.title} ({sheet.sheet_state}), row {number}")
            finally:
                workbook.close()
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("Office original could not be parsed") from exc
    return passages
