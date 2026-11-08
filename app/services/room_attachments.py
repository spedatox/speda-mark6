import asyncio
import hashlib
import base64
import io
from pathlib import PurePath
from uuid import uuid4
from sqlalchemy import select, func
from app.models.coordination import RoomAttachment
from app.schemas.client_compat import MAX_CHAT_DOCUMENT_BYTES, MAX_CHAT_IMAGE_BYTES
from app.services.library_extract import extract_document_bytes, ExtractionError
from app.services.coordination_budget import CoordinationError


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
    mime = {'.pdf':'application/pdf', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.csv':'text/csv'}.get(suffix, 'text/plain')
    try:
        parts = extract_document_bytes(data, mime, max_bytes=max_bytes, max_pages=20,
            max_characters=2_000_000 if suffix == '.pptx' else 32000)
    except ExtractionError as exc:
        raise CoordinationError('attachment_extraction', str(exc), 422) from exc
    text = '\n\n'.join(p.content for p in parts)
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
        if sum(len(item['extracted'].encode()) for item in prepared) > 48000:
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
        data = await upload.read(2 * 1024**2 + 1)
        if not data or len(data) > 2 * 1024**2:
            raise CoordinationError("attachment_size", "Attachments must contain at most 2 MiB", 422)
        name = (upload.filename or "document").replace("\\", "/").split("/")[-1][:255]
        suffix = PurePath(name).suffix.lower()
        mime = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(suffix)
        if not mime:
            raise CoordinationError("attachment_type", "Use TXT, Markdown, CSV, text PDF or DOCX", 422)
        try:
            parts = await asyncio.to_thread(extract_document_bytes, data, mime, max_bytes=2 * 1024**2,
                max_pages=20, max_characters=32000)
        except ExtractionError as exc:
            raise CoordinationError("attachment_extraction", str(exc), 422) from exc
        text = "\n\n".join(p.content for p in parts)
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
        records, size = [], 0
        for identity in dict.fromkeys(ids):
            row = await db.get(RoomAttachment, identity)
            if row is None or row.room_id != room_id:
                raise CoordinationError("attachment_scope", "Attachment is unavailable in this room", 404)
            size += len(row.extracted.encode())
            if size > 48000:
                raise CoordinationError("attachment_context", "Combined attachment text exceeds 48 KiB; use Hall retrieval", 422)
            record = {"id": row.id, "name": row.name, "sha256": row.sha256,
                "text": row.extracted, "authority": "quoted_attachment_not_owner_assertion", 'size': len(row.original)}
            if row.mime.startswith('image/'):
                record['image'] = {'type': 'image', 'source': {'type':'base64', 'media_type':row.mime,
                    'data':base64.b64encode(row.original).decode('ascii')}}
            records.append(record)
        return records
