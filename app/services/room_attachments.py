import asyncio
import hashlib
import base64
import io
from pathlib import PurePath
from uuid import uuid4
from sqlalchemy import select, func
from app.models.client_compat import ClientProject, ClientProjectFile, HiddenMessage
from app.models.coordination import RoomAttachment, RoomEvent
from app.schemas.client_compat import MAX_CHAT_DOCUMENT_BYTES, MAX_CHAT_IMAGE_BYTES
from app.services.library_extract import extract_document_bytes, ExtractionError
from app.services.coordination_budget import CoordinationError
from app.services.pdf_navigation import preview_pdf, text_slice


PDF = 'application/pdf'
INLINE_DOCUMENT_BYTES = 32000
# Leave space for the preceding conversation in the room's history packet.
PDF_PREVIEW_BYTES = 12000
TURN_ATTACHMENT_BYTES = 48000


def extract_room_document(data, mime, max_bytes):
    if mime == PDF:
        try:
            return preview_pdf(data)
        except ValueError as exc:
            raise CoordinationError('attachment_extraction', str(exc), 422) from exc
        except Exception as exc:
            raise CoordinationError('attachment_extraction', 'PDF could not be parsed', 422) from exc
    max_characters = (2_000_000 if mime.endswith('presentationml.presentation') else INLINE_DOCUMENT_BYTES)
    try:
        parts = extract_document_bytes(data, mime, max_bytes=max_bytes, max_characters=max_characters)
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
        encoded = text.encode('utf-8')
        if len(encoded) > 32000:
            text = encoded[:32000].decode('utf-8', errors='ignore') + '\n\n[Additional slide text omitted.]'
        text = '[PowerPoint slide text. Images and diagrams were not inspected.]\n\n' + text
    return {'name': name, 'mime': mime, 'original': data, 'extracted': text}


class Attachments:
    def __init__(self, coordinator):
        self.coordinator, self.sessions = coordinator, coordinator.sessions

    async def store_client(self, owner, room_id, request_id, prepared):
        from uuid import uuid5, NAMESPACE_URL
        ids = []
        if sum(len(item['extracted'].encode()) for item in prepared if item['mime'] != PDF) > TURN_ATTACHMENT_BYTES:
            raise CoordinationError('attachment_context', 'Combined attachment text exceeds 48 KiB; use Hall retrieval', 422)
        async with self.sessions() as db, db.begin():
            await self.coordinator.owned_room(db, owner, room_id, lock=True)
            count = await db.scalar(select(func.count()).select_from(RoomAttachment).where(RoomAttachment.room_id == room_id))
            for index, item in enumerate(prepared):
                identity = uuid5(NAMESPACE_URL, f'{room_id}:{request_id}:{index}').hex
                sha = hashlib.sha256(item['original']).hexdigest()
                row = await db.get(RoomAttachment, identity)
                if row is None:
                    if count >= 100:
                        raise CoordinationError('attachment_limit', 'Room attachment limit reached; use Hall retrieval', 422)
                    db.add(RoomAttachment(id=identity, room_id=room_id, sha256=sha, **item))
                    count += 1
                elif row.sha256 != sha:
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
        rows = []
        for identity in dict.fromkeys(ids):
            row = await db.get(RoomAttachment, identity)
            if row is None or row.room_id != room_id:
                raise CoordinationError("attachment_scope", "Attachment is unavailable in this room", 404)
            rows.append(row)
        other_bytes = sum(len(row.extracted.encode()) for row in rows if row.mime != PDF)
        if other_bytes > TURN_ATTACHMENT_BYTES:
            raise CoordinationError("attachment_context", "Combined attachment text exceeds 48 KiB; use Hall retrieval", 422)
        pdf_count = sum(row.mime == PDF for row in rows)
        pdf_limit = min(PDF_PREVIEW_BYTES, TURN_ATTACHMENT_BYTES - other_bytes) // max(1, pdf_count)
        records = []
        for row in rows:
            text = text_slice(row.extracted, 0, pdf_limit) if row.mime == PDF else row.extracted
            record = {"id": row.id, "name": row.name, "sha256": row.sha256,
                "text": text, "authority": "quoted_attachment_not_owner_assertion", 'size': len(row.original)}
            if row.mime == PDF:
                record['coverage'] = ('PDF text preview. Use read_chat_attachment with this id to search or read pages. '
                    'Verify relevant pages before claiming coverage. Images and diagrams were not inspected.')
            if row.mime.startswith('image/'):
                record['image'] = {'type': 'image', 'source': {'type':'base64', 'media_type':row.mime,
                    'data':base64.b64encode(row.original).decode('ascii')}}
            records.append(record)
        return records

    async def shared_pdf(self, context, attachment_id):
        async with self.sessions() as db:
            room = await self.coordinator.owned_room(db, context.user_id, context.room_id)
            if attachment_id.startswith('project:'):
                project = context.extra.get('project_context') or {}
                if attachment_id not in {doc.get('pdf_id') for doc in project.get('documents', ())}:
                    return None
                return (await db.execute(select(ClientProjectFile.name, ClientProjectFile.original)
                    .join(ClientProject, ClientProjectFile.project_id == ClientProject.id)
                    .where(ClientProjectFile.id == int(attachment_id.removeprefix('project:')),
                        ClientProjectFile.mime == PDF, ClientProjectFile.removed.is_(False),
                        ClientProject.id == project.get('id'), ClientProject.native_id == room.project_id,
                        ClientProject.user_id == context.user_id, ClientProject.agent_id == context.agent_id)
                )).one_or_none()
            cutoff = await db.scalar(select(func.max(RoomEvent.sequence)).where(
                RoomEvent.room_id == room.id, RoomEvent.run_id == context.run_id,
                RoomEvent.kind.in_(('user_message', 'automation_input'))))
            events = list(await db.scalars(select(RoomEvent).where(
                RoomEvent.room_id == room.id, RoomEvent.kind == 'user_message', RoomEvent.sequence <= (cutoff or 0))
                .order_by(RoomEvent.sequence.desc()).limit(32)))
            hidden = set(await db.scalars(select(HiddenMessage.message_id).where(HiddenMessage.session_id == room.session_id)))
            shared = any(event.payload.get('message_id') not in hidden and any(
                attachment.get('id') == attachment_id for attachment in event.payload.get('attachments', []))
                for event in events)
            if not shared:
                return None
            return (await db.execute(select(RoomAttachment.name, RoomAttachment.original).where(
                RoomAttachment.id == attachment_id, RoomAttachment.room_id == room.id, RoomAttachment.mime == PDF))).one_or_none()
