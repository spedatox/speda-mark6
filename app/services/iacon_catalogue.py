"""The only canonical Iacon writer. One transaction publishes a complete revision."""
import json
from datetime import datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, select, update
from app.models.iacon import IaconRecord, IaconRevision, IaconLink, IaconWork, IaconFollowup, IaconSourceRetirement
from app.models.memory_serving import MemoryChange, MemoryServingUnit
from app.schemas.iacon import Command
from app.schemas.memory_runtime import EvidenceSection
from app.services.memory_store import capture_source, digest, bump_epoch, fold
from app.database import write_session


class Conflict(ValueError):
    pass


def snapshot(row):
    return {"id": row.id, "kind": row.kind, "title": row.title, "payload": row.payload,
            "evidence": row.evidence, "status": row.status, "version": row.version}


def evidence_scope(row, sources):
    """A new projection date never establishes the currency of its evidence."""
    lower = [row.payload.get("valid_from")]
    upper = [row.payload.get("valid_until")]
    for source in sources.values():
        lower.append((source.provenance.get("valid_from") or "")[:10] or None)
        upper.append((source.provenance.get("valid_until") or "")[:10] or None)
    starts = max(filter(None, lower), default=None)
    ends = min(filter(None, upper), default=None)
    conflict = bool(starts and ends and starts > ends)
    return {"historical": bool(row.payload.get("closed_on")) or any(
                s.provenance.get("historical", False) for s in sources.values()),
            "eligible_for_context": not conflict and all(projection_source_allowed(row, s)
                                                         for s in sources.values()),
            "valid_from": starts, "valid_until": ends,
            "interval_conflict": conflict}


def projection_source_allowed(row, source):
    # A successfully parsed legacy state is a governed typed record. Its raw
    # markdown was excluded precisely to prevent a second, competing state view.
    return source.provenance.get("eligible_for_context", True) or (
        getattr(row, "kind", None) == "state" and source.source_role == "canonical"
        and "/states/" in source.provenance.get("original_path", "")
        and not source.provenance.get("historical", False))


class Catalogue:
    def __init__(self, store):
        self.store, self.sessions = store, store.sessions

    async def intake_source(self, user_id, *, key, content):
        """Reserve the writer before the immutable-key lookup and source insert."""
        async with write_session(self.sessions) as db:
            row = await capture_source(db, user_id=user_id, source_key='owner-intake:' + key, content=content)
            return {'source_id': row.id, 'content_hash': row.content_hash}

    async def scope(self, db, user_id, row, sources):
        result = evidence_scope(row, sources)
        retired = await db.scalar(select(IaconSourceRetirement.source_id).where(
            IaconSourceRetirement.user_id == user_id, IaconSourceRetirement.source_id.in_(sources)).limit(1))
        if retired is not None:
            result.update(historical=True, eligible_for_context=False, superseded_evidence=True)
        return result

    async def apply(self, user_id, command: Command, *, actor="owner", db=None):
        if db is None:
            async with write_session(self.sessions) as own:
                return await self.apply(user_id, command, actor=actor, db=own)
        if actor not in {"owner", "orion_review", "telegram_owner", "migration"}:
            raise ValueError("Unreviewed proposals cannot write canonical records")
        fingerprint = digest(command.model_dump_json())
        receipt = (await db.execute(select(IaconWork).where(IaconWork.user_id == user_id,
            IaconWork.work_key == "command:" + command.command_id))).scalar_one_or_none()
        if receipt:
            if receipt.payload["hash"] != fingerprint:
                raise Conflict("Idempotency key was used for another command")
            return receipt.result
        sources = {s.id: s for s in (await db.execute(select(MemoryChange).where(
            MemoryChange.user_id == user_id, MemoryChange.id.in_([e.source_id for e in command.evidence]),
            MemoryChange.revoked.is_(False)))).scalars()}
        for evidence in command.evidence:
            source = sources.get(evidence.source_id)
            if (not source or evidence.end > len(source.content) or evidence.start >= evidence.end
                    or source.content[evidence.start:evidence.end] != evidence.quote):
                raise ValueError("Evidence must be an exact, active owner-scoped source span")
        # Quoted material is evidence of a quotation, never the owner's own statement.
        if command.kind in {"claim", "preference"} and command.payload["mode"] == "owner_report":
            if any(s.source_role != "owner" for s in sources.values()):
                raise ValueError("Owner reports require owner evidence")
        if command.kind == "document" and actor == "orion_review":
            raise ValueError("Native documents require their owner's explicit writer")
        if command.kind == "state" and actor != "migration" and any(s.source_role != "owner" for s in sources.values()):
            raise ValueError("State transitions require owner evidence")
        targets = {link.target_id for link in command.links}
        if targets:
            found = set((await db.execute(select(IaconRecord.id).where(
                IaconRecord.user_id == user_id, IaconRecord.id.in_(targets),
                IaconRecord.status == "accepted"))).scalars())
            if found != targets:
                raise ValueError("Links require existing active records belonging to this owner")
        old = await db.get(IaconRecord, command.record_id) if command.record_id else None
        if command.record_id and (not old or old.user_id != user_id or old.kind != command.kind
                                   or old.version != command.expected_version):
            raise Conflict("Record version changed; reload before correcting it")
        values = dict(title=command.title, payload=command.payload,
                      evidence=[e.model_dump() for e in command.evidence],
                      status="retracted" if command.retract else "accepted")
        if old:
            previous_sources = {e["source_id"] for e in old.evidence} - set(sources)
            if previous_sources and old.kind == "document" and actor == "owner":
                # Correcting a native/imported document retires its earlier source
                # from automatic context, not from the immutable evidence archive.
                from app.services.coordination_budget import insert_for
                for source_id in previous_sources:
                    await db.execute(insert_for(db, IaconSourceRetirement).values(user_id=user_id,
                        source_id=source_id, record_id=old.id, version=command.expected_version + 1)
                        .on_conflict_do_nothing())
            result = await db.execute(update(IaconRecord).where(IaconRecord.id == old.id,
                IaconRecord.user_id == user_id, IaconRecord.version == command.expected_version
            ).values(**values, version=command.expected_version + 1, updated_at=datetime.now(timezone.utc)))
            if result.rowcount != 1:
                raise Conflict("Concurrent record correction")
            await db.refresh(old)
            row = old
            await db.execute(delete(IaconLink).where(IaconLink.source_id == row.id))
            await db.execute(update(IaconFollowup).where(IaconFollowup.state_id == row.id,
                IaconFollowup.status.in_(["scheduled", "sending", "sent", "unknown", "snoozed", "muted", "reply_pending", "needs_review"])
            ).values(status="superseded"))
        else:
            row = IaconRecord(id=uuid4().hex, user_id=user_id, kind=command.kind, version=1, **values)
            db.add(row)
            await db.flush()
        for link in {(x.target_id, x.relation) for x in command.links}:
            db.add(IaconLink(user_id=user_id, source_id=row.id, target_id=link[0], relation=link[1]))
        result = snapshot(row)
        result["links"] = [x.model_dump() for x in command.links]
        db.add(IaconRevision(record_id=row.id, version=row.version, command_id=command.command_id,
                             actor=actor, snapshot=result))
        db.add(IaconWork(user_id=user_id, work_key="command:" + command.command_id, kind="receipt",
                         payload={"hash": fingerprint}, status="done", result=result))
        if actor == "migration" and row.kind == "document":
            # Keep existing date/edition-qualified imported views; archival documents
            # must not become fresh assertions just because they gained a catalogue ID.
            await bump_epoch(db, user_id)
        else:
            await self._project(db, user_id, row, sources)
        # Entity corrections and competing claims invalidate only their dependent views.
        dependents = set((await db.execute(select(IaconLink.source_id).where(
            IaconLink.user_id == user_id, IaconLink.target_id == row.id))).scalars())
        group = row.payload.get("conflict_group")
        if group:
            dependents.update((await db.execute(select(IaconRecord.id).where(IaconRecord.user_id == user_id,
                IaconRecord.payload["conflict_group"].as_string() == group,
                IaconRecord.id != row.id))).scalars())
        for dependent in dependents:
            db.add(IaconWork(user_id=user_id, work_key=f"refresh:{row.id}:{row.version}:{dependent}",
                            kind="refresh", payload={"record_id": dependent}))
        if (row.kind == "state" and row.status == "accepted" and not row.payload.get("closed_on")
                and (await self.scope(db, user_id, row, sources))["eligible_for_context"]):
            db.add(IaconFollowup(user_id=user_id, state_id=row.id, state_version=row.version,
                question=row.payload["question"], due_at=datetime.combine(
                    datetime.fromisoformat(row.payload["review_on"]).date(), time.min, timezone.utc)))
        await db.flush()
        return result

    async def _project(self, db, user_id, row, sources):
        # Views are disposable. Original sources and revision history are never rewritten.
        key = "iacon:" + row.id
        await db.execute(update(MemoryChange).where(MemoryChange.user_id == user_id,
            MemoryChange.source_key.like(key + ":%"), MemoryChange.revoked.is_(False)
        ).values(revoked=True, routing_text=""))
        await db.execute(delete(MemoryServingUnit).where(MemoryServingUnit.user_id == user_id,
                                                       MemoryServingUnit.stable_key == key))
        await bump_epoch(db, user_id)
        if row.status != "accepted":
            return
        links = (await db.execute(select(IaconLink, IaconRecord.title).join(IaconRecord,
            IaconRecord.id == IaconLink.target_id).where(IaconLink.source_id == row.id))).all()
        references = [{"id": link.target_id, "relation": link.relation, "title": title} for link, title in links]
        conflicts = []
        if row.payload.get("conflict_group"):
            conflicts = list((await db.execute(select(IaconRecord.id).where(IaconRecord.user_id == user_id,
                IaconRecord.payload["conflict_group"].as_string() == row.payload["conflict_group"],
                IaconRecord.status == "accepted", IaconRecord.id != row.id))).scalars())
        body = json.dumps({"record_id": row.id, "version": row.version, "kind": row.kind,
                           "title": row.title, "payload": row.payload, "references": references,
                           "unresolved_conflicts": conflicts}, ensure_ascii=False)
        # Large native documents stay intact in the catalogue; inject a small locator.
        if len(body) > 4000:
            body = json.dumps({"record_id": row.id, "version": row.version, "kind": row.kind,
                "title": row.title, "lifecycle": {k: v for k, v in row.payload.items() if k in
                    {"status", "review_on", "verified_on", "closed_on", "starts_on", "ends_on", "salience"}},
                "detail": "Read the catalogue record for the complete native content; this is a locator."})
        scope = await self.scope(db, user_id, row, sources)
        provenance = {"iacon_record": row.id, "version": row.version, "dependencies": list(sources), **scope}
        identities = {s.provenance.get("entity_id") for s in sources.values()}
        if row.kind == "entity" and len(identities) == 1 and None not in identities:
            provenance["entity_id"] = next(iter(identities))
        source = await capture_source(db, user_id=user_id,
            source_key=f"{key}:{row.version}:scope2:{digest(body + json.dumps(scope, sort_keys=True))[:16]}",
            content=body, source_role="canonical", provenance=provenance)
        source.revoked, source.routing_text = False, fold(body) if scope["eligible_for_context"] else ""
        if not scope["eligible_for_context"] or scope["interval_conflict"]:
            return  # Retain the catalogue and originals without creating an active read view.
        sections = [EvidenceSection(source_id=source.id, start=0, end=len(body), text=body,
            attribution="canonical_record", review_on=row.payload.get("review_on"),
            valid_from=scope["valid_from"], valid_until=scope["valid_until"],
            historical=scope["historical"])]
        for e in row.evidence:
            original = sources[e["source_id"]]
            sections.append(EvidenceSection(source_id=e["source_id"], start=e["start"], end=e["end"],
                text=e["quote"], attribution={"owner": "owner_statement", "quoted": "quoted_material",
                                            "canonical": "canonical_record"}[original.source_role],
                historical=bool(original.provenance.get("historical")),
                valid_from=(original.provenance.get("valid_from") or "")[:10] or None,
                valid_until=(original.provenance.get("valid_until") or "")[:10] or None))
        kind = {"entity": {"person": "person", "project": "project", "course": "course"}.get(
            row.payload.get("entity_type"), "episode"), "state": "situation",
            "preference": "core"}.get(row.kind, "episode")
        await self.store.publish(SimpleNamespace(user_id=user_id), stable_key=key, kind=kind,
            title=row.title, sections=sections, aliases=row.payload.get("aliases", []), db=db,
            source_only_ids={s.id for s in sources.values() if not s.provenance.get("eligible_for_context", True)})

    async def refresh(self, user_id, record_id):
        async with write_session(self.sessions) as db:
            row = await db.get(IaconRecord, record_id)
            if not row or row.user_id != user_id:
                raise ValueError("Unknown projection owner")
            sources = {s.id: s for s in (await db.execute(select(MemoryChange).where(
                MemoryChange.user_id == user_id, MemoryChange.id.in_([e["source_id"] for e in row.evidence]),
                MemoryChange.revoked.is_(False)))).scalars()}
            if any(e["source_id"] not in sources for e in row.evidence):
                raise ValueError("Projection evidence withdrawn")
            await self._project(db, user_id, row, sources)

    async def get(self, user_id, record_id, *, history=False):
        async with self.sessions() as db:
            row = await db.get(IaconRecord, record_id)
            if not row or row.user_id != user_id:
                raise KeyError("Record not found")
            result = snapshot(row)
            result["links"] = [{"target_id": x.target_id, "relation": x.relation} for x in (
                await db.execute(select(IaconLink).where(IaconLink.source_id == row.id))).scalars()]
            if history:
                result["revisions"] = [r.snapshot for r in (await db.execute(select(IaconRevision).where(
                    IaconRevision.record_id == row.id).order_by(IaconRevision.version.desc()).limit(20))).scalars()]
            sources = {s.id: s for s in (await db.execute(select(MemoryChange).where(MemoryChange.user_id == user_id,
                MemoryChange.id.in_([e["source_id"] for e in row.evidence]), MemoryChange.revoked.is_(False)))).scalars()}
            result["evidence_status"] = "active" if all(e["source_id"] in sources for e in row.evidence) else "withdrawn"
            result["evidence_scope"] = await self.scope(db, user_id, row, sources)
            return result

    async def list(self, user_id, *, kind=None, offset=0, limit=50):
        async with self.sessions() as db:
            query = select(IaconRecord).where(IaconRecord.user_id == user_id)
            if kind:
                query = query.where(IaconRecord.kind == kind)
            return [snapshot(r) for r in (await db.execute(query.order_by(IaconRecord.updated_at.desc(),
                IaconRecord.id).offset(offset).limit(min(limit, 100)))).scalars()]
