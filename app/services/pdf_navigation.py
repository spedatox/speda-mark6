# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local PDF navigation. Original bytes stay in the attachment store."""
import asyncio
import io
import re
import time

from pypdf import PdfReader
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import write_session
from app.models.coordination import PdfSearchIndex
from app.services.lexical import fold


# Keep page reads and search responses small even when the source is a book.
PAGE_RESULT_BYTES = 8000
INDEX_BYTES = 12 * 1024**2
INDEX_SLICE_SECONDS = 8


def _reader(blob):
    if not blob.startswith(b'%PDF-'):
        raise ValueError('Original is not a PDF')
    reader = PdfReader(io.BytesIO(blob))
    if reader.is_encrypted:
        raise ValueError('Upload an unlocked copy of the PDF')
    return reader


def text_slice(text, offset, byte_limit):
    """Offsets count characters. The output limit counts UTF-8 bytes."""
    return text[offset:offset + byte_limit].encode('utf-8')[:byte_limit].decode('utf-8', errors='ignore')


def _page_text(page):
    try:
        text = (page.extract_text() or '').strip()
    except Exception:
        return 'unreadable', ''
    return ('text', text) if text else ('no_extractable_text', '')


def preview_pdf(blob):
    reader = _reader(blob)
    count = len(reader.pages)
    if not count:
        raise ValueError('PDF contains no pages')
    preview = [f'[PDF with {count} pages. Preview of the first {min(2, count)} pages. Images and diagrams were not inspected.]']
    for index in range(min(2, count)):
        status, text = _page_text(reader.pages[index])
        excerpt = text_slice(text, 0, 1800)
        if len(excerpt) < len(text):
            excerpt += '\n[More text on this page.]'
        preview.append(f'--- Page {index + 1} ---\n' + (excerpt or f'[{status.replace("_", " ")}]'))
    return '\n\n'.join(preview)


def read_pdf_page(blob, page, offset):
    reader = _reader(blob)
    total = len(reader.pages)
    if not 1 <= page <= total:
        return {'status': 'invalid_page', 'total_pages': total}
    status, content = _page_text(reader.pages[page - 1])
    if offset > len(content):
        return {'status': 'invalid_offset', 'page': page, 'text_characters': len(content)}
    text = text_slice(content, offset, PAGE_RESULT_BYTES)
    end = offset + len(text)
    return {'status': status, 'page': page, 'total_pages': total, 'text': text,
        'next_page': page + 1 if page < total else None,
        'next_offset': end if end < len(content) else None}


def _extend_index(blob, pages):
    deadline = time.monotonic() + INDEX_SLICE_SECONDS
    reader = _reader(blob)
    total = len(reader.pages)
    page_bytes = min(PAGE_RESULT_BYTES, INDEX_BYTES // max(1, total))
    start = len(pages)
    for index in range(start, total):
        if index > start and time.monotonic() >= deadline:
            break
        status, content = _page_text(reader.pages[index])
        text = text_slice(content, 0, page_bytes)
        pages.append(('partial_text' if len(text) < len(content) else status, text))
    return total


def _find_pages(pages, total, query):
    phrase = ' '.join(fold(query).split())
    words = set(re.findall(r'[^\W_]+', phrase))
    if not words:
        return {'status': 'invalid_query', 'message': 'Use a term or phrase from the document.'}
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(word) for word in sorted(words)) + r')(?!\w)')
    hits = []
    for number, (_, raw) in enumerate(pages, 1):
        text = ' '.join(raw.split())
        normalized = fold(text)
        matches = {match[0] for match in pattern.finditer(normalized)}
        if not matches:
            continue
        exact = normalized.find(phrase)
        start = exact if exact >= 0 else pattern.search(normalized).start()
        hits.append((exact >= 0, len(matches), number, text[max(0, start - 100):start + 240]))
    hits.sort(key=lambda hit: (-hit[0], -hit[1], hit[2]))
    return {'status': 'ok', 'total_pages': total, 'indexed_through_page': len(pages),
        'index_complete': len(pages) == total, 'matched_pages': len(hits),
        'pages_without_indexed_text': sum(status in ('unreadable', 'no_extractable_text') for status, _ in pages),
        'pages_with_partial_indexed_text': sum(status == 'partial_text' for status, _ in pages),
        'results': [{'page': number, 'snippet': snippet} for _, _, number, snippet in hits[:6]]}


async def search_pdf(sessions, owner, source_key, blob, query):
    async with sessions() as db:
        cached = await db.get(PdfSearchIndex, (owner, source_key))
    pages = list(cached.pages) if cached else []
    total = cached.page_count if cached else 0
    if not cached or len(pages) < total:
        total = await asyncio.to_thread(_extend_index, blob, pages)
        async with write_session(sessions) as db:
            insert = sqlite_insert if db.bind.dialect.name == 'sqlite' else postgres_insert
            statement = insert(PdfSearchIndex).values(user_id=owner, source_key=source_key,
                page_count=total, indexed_pages=len(pages), pages=pages)
            await db.execute(statement.on_conflict_do_update(index_elements=['user_id', 'source_key'],
                set_={'page_count': total, 'indexed_pages': len(pages), 'pages': pages},
                where=PdfSearchIndex.indexed_pages < len(pages)))
    return await asyncio.to_thread(_find_pages, pages, total, query)
