# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import json
import re
from itertools import islice

from pypdf.errors import PyPdfError

from app.services.pdf_navigation import PAGE_RESULT_BYTES, read_pdf_page, search_pdf, text_slice
from app.services.room_attachments import PDF, Attachments
from app.skills.base import Skill


class ReadChatAttachmentSkill(Skill):
    name = 'read_chat_attachment'
    read_only = True
    description = (
        'Navigates a document shared in this conversation or attached to the active project. '
        'Search locally by a short term or phrase, then read from the returned page or offset. '
        'For PDFs, pass a page number and optionally continue that page using next_offset. '
        'For other files, omit page and start with an offset, or omit offset to start at the beginning. '
        'A PDF search with index_complete=false can be repeated to continue indexing. '
        'Returns bounded excerpts and coverage, with no model-based indexing or OCR. '
        'Use targeted searches for long documents and be explicit about what you reviewed.'
    )
    input_schema = {'type': 'object', 'properties': {
        'id': {'type': 'string', 'pattern': r'^(?:[a-f0-9]{32}|project:[1-9][0-9]{0,9})$'},
        'query': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'page': {'type': 'integer', 'minimum': 1},
        'offset': {'type': 'integer', 'minimum': 0},
    }, 'required': ['id'], 'additionalProperties': False}

    def available(self, context):
        return bool(context.coordination and context.room_id and context.run_id and context.task_role == 'interactive')

    async def execute(self, args, context):
        searching = 'query' in args
        if searching and ('page' in args or 'offset' in args):
            return json.dumps({'status': 'invalid_arguments', 'message': 'Use query by itself or read a page or offset.'})
        attachments = Attachments(context.coordination)
        offset = args.get('offset', 0)
        source = await attachments.shared_document(context, args['id'], None if searching else offset)
        if source is None:
            return json.dumps({'status': 'unavailable', 'message': 'Document is unavailable in this turn.'})
        name, mime, blob, extracted, characters = source
        if mime != PDF:
            if 'page' in args:
                return json.dumps({'status': 'invalid_arguments', 'message': 'Use an offset for this document.'})
            result = await asyncio.to_thread(read_document_text, extracted, characters, args.get('query'), offset)
        elif searching or 'page' in args:
            try:
                if searching:
                    result = await search_pdf(attachments.sessions, context.user_id, args['id'], blob, args['query'])
                else:
                    result = await asyncio.to_thread(read_pdf_page, blob, args['page'], offset)
            except (ValueError, PyPdfError):
                return json.dumps({'status': 'unreadable', 'message': 'PDF could not be parsed.'})
        else:
            return json.dumps({'status': 'invalid_arguments', 'message': 'Use query or page with an optional offset for PDFs.'})
        return json.dumps({**result, 'name': name, 'authority': 'quoted_attachment_not_owner_assertion'},
                          ensure_ascii=False)


def read_document_text(text, characters, query, offset):
    if query is not None:
        term = query.strip()
        if not term:
            return {'status': 'invalid_query', 'message': 'Use a term or phrase from the document.'}
        matches = re.finditer(re.escape(term), text, flags=re.IGNORECASE)
        return {'status': 'ok', 'text_characters': characters, 'results': [
            {'offset': hit.start(), 'snippet': text_slice(text, max(0, hit.start() - 40), 600)}
            for hit in islice(matches, 6)]}
    if offset > characters:
        return {'status': 'invalid_offset', 'text_characters': characters}
    excerpt = text_slice(text, 0, PAGE_RESULT_BYTES)
    end = offset + len(excerpt)
    return {'status': 'ok', 'text': excerpt, 'offset': offset, 'text_characters': characters,
            'next_offset': end if end < characters else None}
