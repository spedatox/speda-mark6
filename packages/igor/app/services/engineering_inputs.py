"""Retain engineering originals and restore authorized input references.

The worker receives disposable copies. The originals and these references
survive worker cleanup and chat reload; they are not temporary generated outputs.
Only this owner/agent's chats on the same bound desk contribute coding inputs.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from pathlib import Path, PurePosixPath

from sqlalchemy import or_, select

from app.core.context import AgentContext
from app.models.engineering_input import EngineeringInput
from app.models.session import Session
from app.services.workspaces import WorkspaceError


class EngineeringInputService:
    def __init__(self, root: str, workspaces):
        self.root = root
        self.workspaces = workspaces

    @staticmethod
    def uploads(images, documents):
        return [
            {"name": f"image-{i}.{image.media_type.rsplit('/', 1)[-1] or 'bin'}",
             "media_type": image.media_type, "data": image.data}
            for i, image in enumerate(images, 1)
        ] + [{"name": document.name, "media_type": document.media_type, "data": document.data}
             for document in documents]

    async def accept_chat(self, context: AgentContext, *, images, documents):
        uploads = self.uploads(images, documents)
        if uploads:
            await self.accept(context, uploads)
        else:
            await self.restore(context)

    @staticmethod
    def _decode(inputs: list[dict]):
        decoded = []
        total = 0
        for index, item in enumerate(inputs, 1):
            encoded = str(item.get("data") or "")
            if len(encoded) > 4 * ((64 * 1024 * 1024 + 2) // 3):
                raise WorkspaceError("Engineering input exceeds 64 MiB per file.", 413)
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise WorkspaceError("An engineering upload is not valid base64.", 422) from exc
            total += len(data)
            if len(data) > 64 * 1024 * 1024 or total > 128 * 1024 * 1024:
                raise WorkspaceError("Engineering inputs exceed 64 MiB per file or 128 MiB per upload.", 413)
            name = PurePosixPath(str(item.get("name") or f"input-{index}").replace("\\", "/")).name
            name = "".join(char for char in name if ord(char) >= 32)[:255]
            if name in {"", ".", ".."}:
                name = f"input-{index}"
            decoded.append((name, str(item.get("media_type") or "")[:128], data))
        return decoded

    async def accept(self, context: AgentContext, inputs: list[dict]):
        if not self.root or not context.workshop_project_id:
            # A chat without a proven engineering desk carries uploads live.
            # Explicit desk selection retains them during this turn. A local
            # deployment without a persistent root cannot advertise retention.
            context.extra["forge_inputs"] = inputs
            return
        await self.workspaces._owned(context)
        decoded = await asyncio.to_thread(self._decode, inputs)
        from forge.artifacts import ArtifactStore
        store = ArtifactStore(Path(self.root))
        try:
            for name, media_type, data in decoded:
                digest = await asyncio.to_thread(store.put, data)
                existing = await context.db.scalar(select(EngineeringInput.id).where(
                    EngineeringInput.session_id == context.session_id,
                    EngineeringInput.name == name, EngineeringInput.media_type == media_type,
                    EngineeringInput.content_hash == digest,
                ))
                if existing is None:
                    context.db.add(EngineeringInput(
                        session_id=context.session_id, name=name, media_type=media_type,
                        content_hash=digest, size_bytes=len(data),
                    ))
            await context.db.commit()
        except BaseException:
            await context.db.rollback()
            raise
        await self.restore(context)

    async def prepare(self, context: AgentContext):
        pending = [item for item in context.extra.get("forge_inputs", []) if "data" in item]
        if pending and context.workshop_project_id:
            await self.accept(context, pending)
        else:
            await self.restore(context)

    async def restore(self, context: AgentContext):
        if not self.root:
            return
        await self.workspaces._owned(context)
        scope = Session.id == context.session_id
        if context.workshop_project_id:
            scope = or_(scope, Session.workshop_project_id == context.workshop_project_id)
        rows = (await context.db.scalars(select(EngineeringInput).join(
            Session, Session.id == EngineeringInput.session_id,
        ).where(
            Session.user_id == context.user_id, Session.agent_id == context.agent_id, scope,
        ).order_by(EngineeringInput.created_at, EngineeringInput.id))).all()
        from forge.artifacts import ArtifactStore
        store = ArtifactStore(Path(self.root))
        inputs, seen = [], set()
        for row in rows:
            key = row.name, row.media_type, row.content_hash
            if key in seen:
                continue
            seen.add(key)
            try:
                await asyncio.to_thread(store.read, row.content_hash)
            except (OSError, ValueError) as exc:
                raise WorkspaceError("A retained original is missing or corrupt. Restore it before coding continues.", 409) from exc
            inputs.append({
                "name": row.name, "media_type": row.media_type,
                "artifact_hash": row.content_hash, "input_id": row.id, "size_bytes": row.size_bytes,
            })
        context.extra["forge_inputs"] = inputs
