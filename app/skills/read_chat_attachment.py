# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import json

from pypdf.errors import PyPdfError

from app.services.pdf_navigation import read_pdf_page, search_pdf
from app.services.room_attachments import Attachments
from app.skills.base import Skill


class ReadChatAttachmentSkill(Skill):
    name = 'read_chat_attachment'
    read_only = True
    description = (
        'Navigates a PDF shared in this conversation or attached to the active project. '
        'Search locally by a short term or phrase to find relevant page numbers, then read those '
        'pages directly. A search with index_complete=false can be repeated to continue indexing. '
        'Use page offsets only to continue a dense page when next_offset is present. '
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
        if searching == ('page' in args) or (searching and 'offset' in args):
            return json.dumps({'status': 'invalid_arguments', 'message': 'Use query or page with an optional offset.'})
        attachments = Attachments(context.coordination)
        source = await attachments.shared_pdf(context, args['id'])
        if source is None:
            return json.dumps({'status': 'unavailable', 'message': 'PDF is unavailable in this turn.'})
        name, blob = source
        try:
            if searching:
                result = await search_pdf(attachments.sessions, context.user_id, args['id'], blob, args['query'])
            else:
                result = await asyncio.to_thread(read_pdf_page, blob, args['page'], args.get('offset', 0))
        except (ValueError, PyPdfError):
            return json.dumps({'status': 'unreadable', 'message': 'PDF could not be parsed.'})
        return json.dumps({**result, 'name': name, 'authority': 'quoted_attachment_not_owner_assertion'},
                          ensure_ascii=False)
