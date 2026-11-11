"""Existing knowledge editor contracts, backed exclusively by Catalogue.apply."""
import json
from pathlib import PurePosixPath
from uuid import uuid4
from sqlalchemy import select
from app.models.client_memory import ClientMemoryPath
from app.models.iacon import IaconRecord, IaconRevision, IaconLink
from app.models.memory_serving import MemoryChange
from app.schemas.iacon import Command
from app.services.coordination_budget import CoordinationError, insert_for
from app.services.iacon_catalogue import Conflict, evidence_scope
from app.services.memory_store import capture_source


def checked_path(path):
    if (not path.startswith("/memories/") or len(path) > 256 or "\\" in path or
            any(p in {".", ".."} or p.startswith(".") for p in path.split("/")[2:]) or
            str(PurePosixPath(path)) != path or not path.endswith((".md", ".json"))):
        raise ValueError("Use a visible .md or .json path under /memories/")
    return path


def content(record):
    return record.payload["content"] if record.kind == "document" else json.dumps(
        record.payload, ensure_ascii=False, indent=2, sort_keys=True)


class ClientMemory:
    def __init__(self, catalogue):
        self.catalogue, self.sessions = catalogue, catalogue.sessions

    async def _bind(self, db, owner, record):
        old = await db.get(ClientMemoryPath, (owner, record.id))
        if old:
            return old
        fallback = f"/memories/iacon/{record.kind}/{record.id}." + ("md" if record.kind == "document" else "json")
        path = record.title if record.kind == "document" else fallback
        try:
            checked_path(path)
        except ValueError:
            path = fallback
        for candidate in dict.fromkeys([path, fallback]):
            await db.execute(insert_for(db, ClientMemoryPath).values(user_id=owner,
                record_id=record.id, path=candidate).on_conflict_do_nothing())
            bound = await db.get(ClientMemoryPath, (owner, record.id))
            if bound:
                return bound
        raise Conflict("Memory path is already in use")

    async def _sources(self, db, owner, record):
        return {s.id: s for s in (await db.execute(select(MemoryChange).where(
            MemoryChange.user_id == owner, MemoryChange.id.in_([e["source_id"] for e in record.evidence]),
            MemoryChange.revoked.is_(False)))).scalars()}

    async def _view(self, db, owner, record, binding=None):
        binding = binding or await self._bind(db, owner, record)
        sources = await self._sources(db, owner, record)
        scope = await self.catalogue.scope(db, owner, record, sources)
        active = len(sources) == len({e["source_id"] for e in record.evidence})
        # Imported state/ledger projections remain visible, never another state writer.
        editable = active and scope["eligible_for_context"] and not any(
            s.provenance.get("historical", False) for s in sources.values())
        return {"path": binding.path, "content": content(record),
                "display_title": None if record.title.startswith("/memories/") else record.title,
                "updated_at": record.updated_at.isoformat(), "editable": editable,
                "record_id": record.id, "kind": record.kind, "version": record.version,
                "authority": "iacon", "evidence_scope": scope}

    async def files(self, owner):
        async with self.sessions() as db, db.begin():
            records = (await db.execute(select(IaconRecord).where(IaconRecord.user_id == owner,
                IaconRecord.status == "accepted").order_by(IaconRecord.id).limit(10001))).scalars().all()
            if len(records) > 10000:
                raise CoordinationError("editor_limit", "Use the paginated Iacon catalogue for this collection", 413)
            result = []
            for record in records:
                if record.kind == "document" and any(p.startswith(".") for p in record.title.split("/")):
                    continue
                result.append(await self._view(db, owner, record))
            return sorted(result, key=lambda r: r["path"])

    async def _resolve(self, db, owner, path):
        binding = (await db.execute(select(ClientMemoryPath).where(
            ClientMemoryPath.user_id == owner, ClientMemoryPath.path == checked_path(path)))).scalar_one_or_none()
        if binding:
            return await db.get(IaconRecord, binding.record_id), binding
        # Paths can be used directly before the first editor listing after import.
        record = (await db.execute(select(IaconRecord).where(IaconRecord.user_id == owner,
            IaconRecord.kind == "document", IaconRecord.title == path).order_by(IaconRecord.id).limit(1))).scalar_one_or_none()
        if record:
            return record, await self._bind(db, owner, record)
        raise KeyError("Memory record not found")

    async def _write(self, db, owner, record, *, text=None, retract=False, title=None, restore=None):
        view = await self._view(db, owner, record)
        if not view["editable"]:
            raise ValueError("This retained source is read-only; correct its governed Iacon record instead")
        chosen = restore or {"kind": record.kind, "title": record.title, "payload": record.payload}
        payload = dict(chosen["payload"])
        if text is not None:
            payload = {**payload, "content": text} if record.kind == "document" else json.loads(text)
        links = [{"target_id": l.target_id, "relation": l.relation} for l in (await db.execute(
            select(IaconLink).where(IaconLink.source_id == record.id))).scalars()]
        if restore is not None:
            links = restore.get("links", links)
        assertion = json.dumps({"action": "owner_retract" if retract else "owner_correction",
            "record_id": record.id, "payload": payload, "title": title or chosen["title"]}, ensure_ascii=False)
        scope = {}
        if restore:
            original_ids = [e["source_id"] for e in restore["evidence"]]
            originals = {s.id: s for s in (await db.execute(select(MemoryChange).where(
                MemoryChange.user_id == owner, MemoryChange.id.in_(original_ids),
                MemoryChange.revoked.is_(False)))).scalars()}
            if set(originals) != set(original_ids):
                raise ValueError("The revision's evidence has been withdrawn")
            from types import SimpleNamespace
            # Explicit restoration is a new owner act, but preserves the selected
            # edition's original date/archive qualifiers. Serving retirement alone
            # does not prevent intentionally restoring that edition.
            scope = evidence_scope(SimpleNamespace(payload=payload), originals)
        if title is not None and text is None and restore is None and not retract:
            evidence = record.evidence  # Moving a label does not renew its knowledge.
        else:
            source = await capture_source(db, user_id=owner, source_key="owner-editor:" + uuid4().hex,
                content=assertion, source_role='quoted' if record.kind == 'document' else 'owner',
                provenance={"writer": "knowledge_editor", "record_id": record.id,
                    **scope})
            evidence = [{"source_id": source.id, "start": 0, "end": min(len(assertion), 8000),
                         "quote": assertion[:8000]}]
        await self.catalogue.apply(owner, Command(command_id="editor:" + uuid4().hex,
            record_id=record.id, expected_version=record.version, kind=record.kind,
            title=title or chosen["title"], payload=payload,
            evidence=evidence, links=links, retract=retract), db=db)
        await db.refresh(record)

    async def commit(self, owner, path, text, expected):
        async with self.sessions() as db, db.begin():
            try:
                record, binding = await self._resolve(db, owner, path)
            except KeyError:
                if expected is not None:
                    raise Conflict("Memory record disappeared; reload before saving")
                source = await capture_source(db, user_id=owner, source_key="owner-editor:" + uuid4().hex,
                    content=text, source_role='quoted', provenance={"writer": "knowledge_editor"})
                created = await self.catalogue.apply(owner, Command(command_id="editor:" + uuid4().hex,
                    kind="document", title=checked_path(path), payload={"content": text},
                    evidence=[{"source_id": source.id, "start": 0, "end": min(len(text), 8000), "quote": text[:8000]}]), db=db)
                record = await db.get(IaconRecord, created["id"])
                binding = await self._bind(db, owner, record)
                if binding.path != path:
                    raise Conflict("Memory path is already in use")
            else:
                current = await self._view(db, owner, record, binding)
                if record.status != "accepted" or expected != current["updated_at"]:
                    raise CoordinationError("memory_conflict", {"message": "Reload before saving.", "current": current}, 409)
                await self._write(db, owner, record, text=text)
            return await self._view(db, owner, record, binding)

    async def save_state(self, owner, body):
        async with self.sessions() as db, db.begin():
            links = []
            if body.record_id:
                record = await db.get(IaconRecord, body.record_id)
                if not record or record.user_id != owner or record.kind != "state" or record.status != "accepted":
                    raise KeyError("State not found")
                links = [{"target_id": row.target_id, "relation": row.relation} for row in await db.scalars(
                    select(IaconLink).where(IaconLink.source_id == record.id)
                    .order_by(IaconLink.target_id, IaconLink.relation))]
            payload = body.state.model_dump(mode="json")
            assertion = json.dumps({"title": body.title, "state": payload}, ensure_ascii=False)
            source = await capture_source(db, user_id=owner, source_key="owner-state:" + body.request_id,
                content=assertion, provenance={"writer": "state_editor"})
            source.outcome = "iacon_interpreted"
            result = await self.catalogue.apply(owner, Command(command_id="owner-state:" + body.request_id,
                record_id=body.record_id, expected_version=body.expected_version, kind="state",
                title=body.title, payload=payload, links=links,
                evidence=[{"source_id": source.id, "start": 0, "end": min(len(assertion), 8000),
                           "quote": assertion[:8000]}]), db=db)
            return await self._view(db, owner, await db.get(IaconRecord, result["id"]))

    async def retire(self, owner, path):
        async with self.sessions() as db, db.begin():
            record, _ = await self._resolve(db, owner, path)
            if record.status == "retracted":
                return {"deleted": path, "bytes": len(content(record)), "recoverable": True}
            size = len(content(record))
            await self._write(db, owner, record, retract=True)
            return {"deleted": path, "bytes": size, "recoverable": True}

    async def rename(self, owner, old, new):
        checked_path(new)
        async with self.sessions() as db, db.begin():
            record, binding = await self._resolve(db, owner, old)
            exists = await db.scalar(select(ClientMemoryPath.record_id).where(
                ClientMemoryPath.user_id == owner, ClientMemoryPath.path == new))
            if exists and exists != record.id:
                raise Conflict("Memory path is already in use")
            await self._write(db, owner, record, title=new)
            binding.path = new
            await db.flush()
            return await self._view(db, owner, record, binding)

    async def revisions(self, owner, path):
        async with self.sessions() as db, db.begin():
            record, _ = await self._resolve(db, owner, path)
            rows = (await db.execute(select(IaconRevision).where(IaconRevision.record_id == record.id)
                .order_by(IaconRevision.version).limit(10001))).scalars().all()
            if len(rows) > 10000:
                raise ValueError("Use the Iacon catalogue for this revision history")
            result, previous = [], None
            for rev in rows:
                data = rev.snapshot
                after = data["payload"]["content"] if data["kind"] == "document" else json.dumps(
                    data["payload"], ensure_ascii=False, indent=2, sort_keys=True)
                result.append({"id": rev.id, "path": path, "author": rev.actor,
                    "action": "delete" if data["status"] == "retracted" else "commit",
                    "created_at": rev.created_at.isoformat(), "before": previous,
                    "after": None if data["status"] == "retracted" else after})
                previous = after
            return list(reversed(result))

    async def restore(self, owner, revision_id):
        async with self.sessions() as db, db.begin():
            rev = (await db.execute(select(IaconRevision).join(IaconRecord,
                IaconRevision.record_id == IaconRecord.id).where(IaconRecord.user_id == owner,
                IaconRevision.id == revision_id))).scalar_one_or_none()
            if not rev:
                raise KeyError("Revision not found")
            record = await db.get(IaconRecord, rev.record_id)
            await self._write(db, owner, record, restore=rev.snapshot)
            return await self._view(db, owner, record)
