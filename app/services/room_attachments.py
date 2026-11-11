import asyncio
import hashlib
import base64
import io
from pathlib import PurePath
from uuid import uuid4
from sqlalchemy import Text, case, cast, select, func
from app.models.client_compat import ClientProject, ClientProjectFile, HiddenMessage
from app.models.coordination import RoomAttachment, RoomEvent
from app.schemas.client_compat import MAX_CHAT_DOCUMENT_BYTES, MAX_CHAT_IMAGE_BYTES
from app.services.library_extract import decode_document_text, extract_document_bytes, ExtractionError
from app.services.coordination_budget import CoordinationError
from app.services.pdf_navigation import PAGE_RESULT_BYTES, preview_pdf, text_slice


PDF = 'application/pdf'
INLINE_DOCUMENT_BYTES = 32000
# Leave space for the preceding conversation in the room's history packet.
DOCUMENT_PREVIEW_BYTES = 12000
TURN_ATTACHMENT_BYTES = 16000


def extract_room_document(data, mime, max_bytes):
    if mime == PDF:
        try:
            return preview_pdf(data)
        except ValueError as exc:
            raise CoordinationError('attachment_extraction', str(exc), 422) from exc
        except Exception as exc:
            raise CoordinationError('attachment_extraction', 'PDF could not be parsed', 422) from exc
    try:
        if mime.startswith('text/'):
            text = decode_document_text(data)
            if not text.strip():
                raise ExtractionError('Original contains no extractable text')
            return text
        parts = extract_document_bytes(data, mime, max_bytes=max_bytes)
    except ExtractionError as exc:
        raise CoordinationError('attachment_extraction', str(exc), 422) from exc
    return '\n\n'.join(part.content for part in parts)


def decode_attachment(encoded, max_bytes):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise CoordinationError('attachment_encoding', 'Attachment is not valid base64', 422) from exc
    if not data:
        raise CoordinationError('attachment_size', 'The attachment is empty', 422)
    if len(data) > max_bytes:
        raise CoordinationError('attachment_size', f'Attachment exceeds {max_bytes // 1024**2} MiB', 422)
    return data


def prepare_client_attachment(attachment, *, image=False):
    max_bytes = MAX_CHAT_IMAGE_BYTES if image else MAX_CHAT_DOCUMENT_BYTES
    data = decode_attachment(attachment.data, max_bytes)
    if image:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > 16000000 or img.width < 1 or img.height < 1:
                    raise ValueError('Image dimensions exceed the limit')
                detected = Image.MIME.get(img.format)
                if detected != attachment.media_type:
                    raise ValueError('Image content does not match its media type')
                img.verify()
        except Exception as exc:
            raise CoordinationError('attachment_image', 'Image is invalid or exceeds the 16 megapixel limit', 422) from exc
        return {'name': 'image', 'mime': attachment.media_type, 'original': data, 'extracted': ''}
    name = attachment.name.replace('\\', '/').split('/')[-1]
    suffix = PurePath(name).suffix.lower()
    mime = {'.pdf':PDF, '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.csv':'text/csv'}.get(suffix, 'text/plain')
    text = extract_room_document(data, mime, max_bytes)
    if suffix == '.pptx':
        text = '[PowerPoint slide text. Images and diagrams were not inspected.]\n\n' + text
    return {'name': name, 'mime': mime, 'original': data, 'extracted': text}


class Attachments:
    def __init__(self, coordinator):
        self.coordinator, self.sessions = coordinator, coordinator.sessions

    async def store_client(self, owner, room_id, request_id, prepared):
        from uuid import uuid5, NAMESPACE_URL
        ids = []
        async with self.sessions() as db, db.begin():
            await self.coordinator.owned_room(db, owner, room_id, lock=True)
            count = await db.scalar(select(func.count()).select_from(RoomAttachment).where(RoomAttachment.room_id == room_id))
            for index, item in enumerate(prepared):
                identity = uuid5(NAMESPACE_URL, f'{room_id}:{request_id}:{index}').hex
                sha = hashlib.sha256(item['original']).hexdigest()
                previous_sha = await db.scalar(select(RoomAttachment.sha256).where(RoomAttachment.id == identity))
                if previous_sha is None:
                    if count >= 100:
                        raise CoordinationError('attachment_limit', 'Room attachment limit reached; use Hall retrieval', 422)
                    db.add(RoomAttachment(id=identity, room_id=room_id, sha256=sha, **item))
                    count += 1
                elif previous_sha != sha:
                    raise CoordinationError('attachment_conflict', 'Attachment identity already has different content', 409)
                ids.append(identity)
        return ids

    async def upload(self, owner, room_id, upload):
        async with self.sessions() as db:
            await self.coordinator.owned_room(db, owner, room_id)
        name = (upload.filename or "document").replace("\\", "/").split("/")[-1][:255]
        suffix = PurePath(name).suffix.lower()
        mime = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".pdf": PDF,
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(suffix)
        if not mime:
            raise CoordinationError("attachment_type", "Use TXT, Markdown, CSV, text PDF or DOCX", 422)
        max_bytes = MAX_CHAT_DOCUMENT_BYTES if mime == PDF else 2 * 1024**2
        data = await upload.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            raise CoordinationError("attachment_size", f"Attachments must contain at most {max_bytes // 1024**2} MiB", 422)
        text = await asyncio.to_thread(extract_room_document, data, mime, max_bytes)
        if not text.strip():
            raise CoordinationError("empty_attachment", "No extractable text", 422)
        row = RoomAttachment(id=uuid4().hex, room_id=room_id, name=name, mime=mime,
            sha256=hashlib.sha256(data).hexdigest(), original=data, extracted=text)
        async with self.sessions() as db, db.begin():
            await self.coordinator.owned_room(db, owner, room_id, lock=True)
            count = await db.scalar(select(func.count()).select_from(RoomAttachment).where(RoomAttachment.room_id == room_id))
            if count >= 100:
                raise CoordinationError("attachment_limit", "Room attachment limit reached; use the Hall of Records for larger libraries")
            db.add(row)
        return {"id": row.id, "name": name, "sha256": row.sha256, "characters": len(text)}

    async def resolve(self, db, room_id, ids):
        identities = list(dict.fromkeys(ids))
        if not identities:
            return []
        attachments = (await db.execute(select(RoomAttachment.id, RoomAttachment.name, RoomAttachment.mime,
            RoomAttachment.sha256, func.length(RoomAttachment.original).label('size'),
            func.length(RoomAttachment.extracted).label('characters'),
            func.substr(RoomAttachment.extracted, 1, INLINE_DOCUMENT_BYTES).label('preview'),
            case((RoomAttachment.mime.startswith('image/'), RoomAttachment.original)).label('image'))
            .where(RoomAttachment.room_id == room_id, RoomAttachment.id.in_(identities)))).all()
        by_id = {row.id: row for row in attachments}
        if len(by_id) != len(identities):
            raise CoordinationError("attachment_scope", "Attachment is unavailable in this room", 404)
        rows = [by_id[identity] for identity in identities]
        previews = []
        for row in rows:
            limit = DOCUMENT_PREVIEW_BYTES if row.mime == PDF else INLINE_DOCUMENT_BYTES
            text = text_slice(row.preview, 0, limit)
            if len(text) < row.characters:
                text = text_slice(text, 0, DOCUMENT_PREVIEW_BYTES)
            previews.append(text)
        if sum(len(text.encode()) for text in previews) > TURN_ATTACHMENT_BYTES:
            share = TURN_ATTACHMENT_BYTES // sum(not row.mime.startswith('image/') for row in rows)
            previews = [text_slice(text, 0, share) for text in previews]
        records = []
        for row, text in zip(rows, previews):
            record = {"id": row.id, "name": row.name, "sha256": row.sha256,
                "text": text, "authority": "quoted_attachment_not_owner_assertion", 'size': row.size}
            if row.mime == PDF:
                record['coverage'] = ('PDF text preview. Use read_chat_attachment with this id to search or read pages. '
                    'Verify relevant pages before claiming coverage. Images and diagrams were not inspected.')
            elif len(text) < row.characters:
                record['coverage'] = ('Text preview. Use read_chat_attachment with this id to search '
                    'or read from an offset in the full extracted text.')
            if row.mime.startswith('image/'):
                record['image'] = {'type': 'image', 'source': {'type':'base64', 'media_type':row.mime,
                    'data':base64.b64encode(row.image).decode('ascii')}}
            records.append(record)
        return records

    async def shared_document(self, context, attachment_id, offset=None):
        async with self.sessions() as db:
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id)
            if attachment_id.startswith('project:'):
                project = context.extra.get('project_context') or {}
                if not any(attachment_id in (doc.get('document_id'), doc.get('pdf_id'))
                           for doc in project.get('documents', ())):
                    return None
                return (await db.execute(select(*document_source(ClientProjectFile, offset))
                    .join(ClientProject, ClientProjectFile.project_id == ClientProject.id)
                    .where(ClientProjectFile.id == int(attachment_id.removeprefix('project:')),
                        ClientProjectFile.removed.is_(False),
                        ClientProject.id == project.get('id'), ClientProject.native_id == room.project_id,
                        ClientProject.user_id == context.user_id, ClientProject.agent_id == context.agent_id)
                )).one_or_none()
            cutoff = await db.scalar(select(func.max(RoomEvent.sequence)).where(
                RoomEvent.room_id == room.id, RoomEvent.run_id == context.run_id,
                RoomEvent.kind.in_(('user_message', 'automation_input'))))
            events = list(await db.scalars(select(RoomEvent).where(
                RoomEvent.room_id == room.id, RoomEvent.kind == 'user_message', RoomEvent.sequence <= (cutoff or 0),
                cast(RoomEvent.payload, Text).contains(attachment_id))))
            hidden = set(await db.scalars(select(HiddenMessage.message_id).where(HiddenMessage.session_id == room.session_id)))
            shared = any(event.payload.get('message_id') not in hidden and any(
                attachment.get('id') == attachment_id for attachment in event.payload.get('attachments', []))
                for event in events)
            if not shared:
                return None
            return (await db.execute(select(*document_source(RoomAttachment, offset)).where(
                RoomAttachment.id == attachment_id, RoomAttachment.room_id == room.id,
                ~RoomAttachment.mime.startswith('image/')))).one_or_none()


def document_source(model, offset):
    """Fetch PDF bytes, a text window, or full text for local search."""
    text = model.extracted
    if offset is not None:
        text = func.substr(text, min(offset, MAX_CHAT_DOCUMENT_BYTES + 1) + 1, PAGE_RESULT_BYTES)
    return (model.name, model.mime, case((model.mime == PDF, model.original)),
            case((model.mime != PDF, text)), func.length(model.extracted))
