"""The only canonical Iacon writer. One transaction publishes a complete revision."""
from __future__ import annotations
import json
from datetime import datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from app.models.iacon import (
    IaconEdge, IaconEntityAlias, IaconFollowup, IaconLink, IaconNarrative,
    IaconRecord, IaconRevision, IaconSourceRetirement, IaconWork,
)
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
        # Mirror links into bi-temporal associative graph edges and keep entity aliases in sync.
        await self._sync_record_edges_and_aliases(db, user_id, row, command, sources)
        result = snapshot(row)
        result["links"] = [x.model_dump() for x in command.links]
        db.add(IaconRevision(record_id=row.id, version=row.version, command_id=command.command_id,
                             actor=actor, snapshot=result))
        db.add(IaconWork(user_id=user_id, work_key="command:" + command.command_id, kind="receipt",
                         payload={"hash": fingerprint}, status="done", result=result))
        if command.retract:
            await self._cascade_delete_record(db, user_id, row.id, retire_sources=False)
        elif actor == "migration" and row.kind == "document":
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
        old_unit_ids = list((await db.execute(select(MemoryServingUnit.id).where(
            MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == key
        ))).scalars())
        await db.execute(delete(MemoryServingUnit).where(MemoryServingUnit.user_id == user_id,
                                                       MemoryServingUnit.stable_key == key))
        if old_unit_ids and getattr(self.store, "semantic", None) and self.store.semantic.enabled:
            for uid in old_unit_ids:
                await self.store.semantic.delete_unit(db, user_id, uid)
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

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    async def _sync_record_edges_and_aliases(self, db, user_id, row, command, sources):
        """Sync alias index and bi-temporal edges derived from canonical command links."""
        now_ts = datetime.now(timezone.utc)
        await db.execute(delete(IaconEntityAlias).where(
            IaconEntityAlias.user_id == user_id, IaconEntityAlias.record_id == row.id))
        if row.status == "accepted":
            raw_aliases = [row.title, *(row.payload.get("aliases") or [])]
            seen_folded = set()
            for raw in raw_aliases:
                cleaned = str(raw or "").strip()[:256]
                folded_val = fold(cleaned).strip()
                if len(folded_val) >= 2 and folded_val not in seen_folded:
                    seen_folded.add(folded_val)
                    db.add(IaconEntityAlias(
                        user_id=user_id, record_id=row.id,
                        alias_folded=folded_val, alias_raw=cleaned, source="extracted",
                    ))
        scope = evidence_scope(row, sources)
        vf = self._parse_dt(scope.get("valid_from")) or self._parse_dt(row.payload.get("starts_on"))
        vu = self._parse_dt(scope.get("valid_until")) or self._parse_dt(row.payload.get("closed_on"))
        if command.links and row.status == "accepted":
            existing_edges = (await db.execute(select(IaconEdge).where(
                IaconEdge.user_id == user_id, IaconEdge.source_id == row.id,
                IaconEdge.status == "accepted",
            ))).scalars().all()
            existing_by_key = {(e.target_id, e.relation): e for e in existing_edges}
            desired = {(x.target_id, x.relation) for x in command.links}
            for key, old_edge in existing_by_key.items():
                if key not in desired:
                    old_edge.status = "superseded"
                    old_edge.superseded_at = now_ts
                    old_edge.updated_at = now_ts
            for target_id, relation in desired:
                if (target_id, relation) not in existing_by_key:
                    db.add(IaconEdge(
                        id=uuid4().hex, user_id=user_id, source_id=row.id,
                        target_id=target_id, relation=relation,
                        description=f"{row.title} ({relation})",
                        epistemic_mode="explicit", confidence=1.0,
                        valid_from=vf, valid_until=vu, recorded_at=now_ts,
                        evidence=[e.model_dump() for e in command.evidence],
                        status="accepted", version=1, updated_at=now_ts,
                    ))

    async def upsert_edge(
        self,
        user_id: int,
        *,
        source_id: str,
        target_id: str,
        relation: str,
        description: str = "",
        epistemic_mode: str = "explicit",
        confidence: float = 1.0,
        valid_from=None,
        valid_until=None,
        evidence: list | None = None,
        actor: str = "orion_review",
        db=None,
    ) -> dict:
        """Canonical writer for bi-temporal associative edges."""
        if actor not in {"owner", "orion_review", "telegram_owner", "migration"}:
            raise ValueError("Unreviewed proposals cannot write canonical edges")
        if epistemic_mode not in {"explicit", "inferred"}:
            raise ValueError("epistemic_mode must be 'explicit' or 'inferred'")
        if db is None:
            async with write_session(self.sessions) as own:
                return await self.upsert_edge(
                    user_id, source_id=source_id, target_id=target_id, relation=relation,
                    description=description, epistemic_mode=epistemic_mode, confidence=confidence,
                    valid_from=valid_from, valid_until=valid_until, evidence=evidence, actor=actor, db=own,
                )
        found = set((await db.execute(select(IaconRecord.id).where(
            IaconRecord.user_id == user_id,
            IaconRecord.id.in_([source_id, target_id]),
            IaconRecord.status == "accepted",
        ))).scalars())
        if found != {source_id, target_id}:
            raise ValueError("Edges require two existing active records belonging to this owner")
        now_ts = datetime.now(timezone.utc)
        vf = self._parse_dt(valid_from)
        vu = self._parse_dt(valid_until)
        existing = (await db.execute(select(IaconEdge).where(
            IaconEdge.user_id == user_id,
            IaconEdge.source_id == source_id,
            IaconEdge.target_id == target_id,
            IaconEdge.relation == relation,
            IaconEdge.status == "accepted",
        ))).scalar_one_or_none()
        if existing:
            existing.description = description or existing.description
            existing.epistemic_mode = epistemic_mode
            existing.confidence = max(0.0, min(1.0, float(confidence)))
            if vf is not None:
                existing.valid_from = vf
            if vu is not None:
                existing.valid_until = vu
            if evidence:
                existing.evidence = evidence
            existing.version += 1
            existing.updated_at = now_ts
            edge = existing
        else:
            edge = IaconEdge(
                id=uuid4().hex, user_id=user_id, source_id=source_id, target_id=target_id,
                relation=relation[:48], description=description[:1000],
                epistemic_mode=epistemic_mode, confidence=max(0.0, min(1.0, float(confidence))),
                valid_from=vf, valid_until=vu, recorded_at=now_ts,
                evidence=list(evidence or []), status="accepted", version=1, updated_at=now_ts,
            )
            db.add(edge)
        await db.flush()
        if hasattr(self.store, "graph"):
            self.store.graph.invalidate_user(user_id)
        await bump_epoch(db, user_id)
        return {
            "id": edge.id, "source_id": edge.source_id, "target_id": edge.target_id,
            "relation": edge.relation, "epistemic_mode": edge.epistemic_mode,
            "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
            "valid_until": edge.valid_until.isoformat() if edge.valid_until else None,
            "status": edge.status, "version": edge.version,
        }

    async def supersede_edge(self, user_id: int, edge_id: str, *, valid_until=None, replaced_by: str | None = None, db=None) -> dict:
        """Close an outdated edge's validity without destroying historical evidence."""
        if db is None:
            async with write_session(self.sessions) as own:
                return await self.supersede_edge(user_id, edge_id, valid_until=valid_until, replaced_by=replaced_by, db=own)
        edge = await db.get(IaconEdge, edge_id)
        if not edge or edge.user_id != user_id:
            raise KeyError("Edge not found")
        now_ts = datetime.now(timezone.utc)
        edge.valid_until = self._parse_dt(valid_until) or now_ts
        edge.superseded_at = now_ts
        edge.superseded_by = replaced_by
        edge.status = "superseded"
        edge.version += 1
        edge.updated_at = now_ts
        await db.flush()
        if hasattr(self.store, "graph"):
            self.store.graph.invalidate_user(user_id)
        await bump_epoch(db, user_id)
        return {"id": edge.id, "status": edge.status, "valid_until": edge.valid_until.isoformat()}

    async def upsert_narrative(
        self,
        user_id: int,
        *,
        tier: str,
        slug: str,
        title: str,
        summary_markdown: str,
        explicit_facts: list | None = None,
        inferred_links: list | None = None,
        source_record_ids: list[str] | None = None,
        source_edge_ids: list[str] | None = None,
        source_digest: str = "",
        time_span_start=None,
        time_span_end=None,
        priority_weight: float = 1.0,
        owner_locked: bool | None = None,
        actor: str = "orion_review",
        db=None,
    ) -> dict:
        """Canonical writer for Orion Dreaming autobiographical narrative cards."""
        if actor not in {"owner", "orion_review", "migration"}:
            raise ValueError("Unreviewed proposals cannot write canonical narratives")
        if tier not in {"core_identity", "active_life", "chapter", "entity_dossier", "preferences"}:
            raise ValueError(f"Unsupported narrative tier: {tier}")
        if db is None:
            async with write_session(self.sessions) as own:
                return await self.upsert_narrative(
                    user_id, tier=tier, slug=slug, title=title, summary_markdown=summary_markdown,
                    explicit_facts=explicit_facts, inferred_links=inferred_links,
                    source_record_ids=source_record_ids, source_edge_ids=source_edge_ids,
                    source_digest=source_digest, time_span_start=time_span_start,
                    time_span_end=time_span_end, priority_weight=priority_weight,
                    owner_locked=owner_locked, actor=actor, db=own,
                )
        now_ts = datetime.now(timezone.utc)
        existing = (await db.execute(select(IaconNarrative).where(
            IaconNarrative.user_id == user_id, IaconNarrative.slug == slug,
        ))).scalar_one_or_none()
        if existing and existing.owner_locked and actor != "owner":
            return {"id": existing.id, "slug": existing.slug, "status": "owner_locked_skipped", "version": existing.version}

        rec_ids = sorted(set(str(r) for r in (source_record_ids or [])))
        edge_ids = sorted(set(str(e) for e in (source_edge_ids or [])))
        computed_digest = source_digest or digest(json.dumps({"records": rec_ids, "edges": edge_ids, "body": summary_markdown}, sort_keys=True))
        ts_start = self._parse_dt(time_span_start)
        ts_end = self._parse_dt(time_span_end)

        if existing:
            existing.tier = tier
            existing.title = title[:256]
            existing.summary_markdown = summary_markdown
            existing.explicit_facts = list(explicit_facts or [])
            existing.inferred_links = list(inferred_links or [])
            existing.source_record_ids = rec_ids
            existing.source_edge_ids = edge_ids
            existing.source_digest = computed_digest[:64]
            existing.time_span_start = ts_start
            existing.time_span_end = ts_end
            existing.priority_weight = float(priority_weight)
            if owner_locked is not None and actor == "owner":
                existing.owner_locked = bool(owner_locked)
            existing.status = "accepted"
            existing.version += 1
            existing.updated_at = now_ts
            row = existing
        else:
            row = IaconNarrative(
                id=uuid4().hex, user_id=user_id, tier=tier, slug=slug[:128], title=title[:256],
                summary_markdown=summary_markdown,
                explicit_facts=list(explicit_facts or []),
                inferred_links=list(inferred_links or []),
                source_record_ids=rec_ids, source_edge_ids=edge_ids,
                source_digest=computed_digest[:64],
                time_span_start=ts_start, time_span_end=ts_end,
                priority_weight=float(priority_weight),
                owner_locked=bool(owner_locked) if actor == "owner" else False,
                status="accepted", version=1, updated_at=now_ts,
            )
            db.add(row)
        await db.flush()
        # Project narrative into MemoryServingUnit + MemoryEmbedding so semantic search finds chapters too.
        await self._project_narrative(db, user_id, row)
        await bump_epoch(db, user_id)
        return {"id": row.id, "slug": row.slug, "tier": row.tier, "status": row.status, "version": row.version}

    async def _project_narrative(self, db, user_id: int, row: IaconNarrative) -> None:
        key = "narrative:" + row.slug
        old_unit_ids = list((await db.execute(select(MemoryServingUnit.id).where(
            MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == key,
        ))).scalars())
        await db.execute(delete(MemoryServingUnit).where(
            MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == key,
        ))
        if old_unit_ids and getattr(self.store, "semantic", None) and self.store.semantic.enabled:
            for uid in old_unit_ids:
                await self.store.semantic.delete_unit(db, user_id, uid)
        if row.status != "accepted":
            return
        body = f"# {row.title}\n{row.summary_markdown}".strip()
        src = await capture_source(
            db, user_id=user_id,
            source_key=f"{key}:v{row.version}:{digest(body)[:12]}",
            content=body, source_role="canonical",
            provenance={"iacon_narrative": row.id, "tier": row.tier, "slug": row.slug,
                        "source_record_ids": row.source_record_ids, "eligible_for_context": True},
        )
        src.revoked = False
        src.routing_text = fold(body)
        section = EvidenceSection(
            source_id=src.id, start=0, end=len(body), text=body,
            attribution="canonical_record",
            valid_from=row.time_span_start.date() if row.time_span_start else None,
            valid_until=None, historical=False,
        )
        await self.store.publish(
            SimpleNamespace(user_id=user_id), stable_key=key,
            kind="narrative", title=row.title, sections=[section], db=db,
        )

    async def _cascade_delete_record(self, db, user_id: int, record_id: str, *, retire_sources: bool = True) -> dict:
        """Propagate record deletion/retraction across all layers, embeddings, narratives, caches, and queues."""
        now_ts = datetime.now(timezone.utc)
        row = await db.get(IaconRecord, record_id)
        if not row or row.user_id != user_id:
            return {"deleted": False}

        # 1. Retire source MemoryChange rows if requested (keeps originals immutable, excludes from context)
        retired_source_ids = []
        if retire_sources and row.evidence:
            from app.services.coordination_budget import insert_for
            for ev in row.evidence:
                sid = ev.get("source_id")
                if sid is not None:
                    retired_source_ids.append(sid)
                    await db.execute(insert_for(db, IaconSourceRetirement).values(
                        user_id=user_id, source_id=sid, record_id=record_id, version=row.version,
                    ).on_conflict_do_nothing())
                    if getattr(self.store, "semantic", None) and self.store.semantic.enabled:
                        await self.store.semantic.delete_source(db, user_id, sid)

        # 2. Delete/invalidate all graph links, bi-temporal edges, and aliases
        await db.execute(delete(IaconLink).where(
            IaconLink.user_id == user_id,
            or_(IaconLink.source_id == record_id, IaconLink.target_id == record_id),
        ))
        await db.execute(update(IaconEdge).where(
            IaconEdge.user_id == user_id,
            or_(IaconEdge.source_id == record_id, IaconEdge.target_id == record_id),
            IaconEdge.status != "deleted",
        ).values(status="deleted", superseded_at=now_ts, updated_at=now_ts))
        await db.execute(delete(IaconEntityAlias).where(
            IaconEntityAlias.user_id == user_id, IaconEntityAlias.record_id == record_id,
        ))

        # 3. Purge serving unit & embeddings for this record
        key = "iacon:" + record_id
        await db.execute(update(MemoryChange).where(
            MemoryChange.user_id == user_id, MemoryChange.source_key.like(key + ":%"),
            MemoryChange.revoked.is_(False),
        ).values(revoked=True, routing_text=""))
        old_unit_ids = list((await db.execute(select(MemoryServingUnit.id).where(
            MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == key,
        ))).scalars())
        await db.execute(delete(MemoryServingUnit).where(
            MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == key,
        ))
        if old_unit_ids and getattr(self.store, "semantic", None) and self.store.semantic.enabled:
            for uid in old_unit_ids:
                await self.store.semantic.delete_unit(db, user_id, uid)

        # 4. Immediately invalidate every IaconNarrative referencing this record + purge its serving unit/embeddings
        narratives = (await db.execute(select(IaconNarrative).where(
            IaconNarrative.user_id == user_id, IaconNarrative.status == "accepted",
        ))).scalars().all()
        invalidated_narratives = []
        for nar in narratives:
            if record_id in (nar.source_record_ids or []):
                nar.status = "stale_deleted"
                nar.source_record_ids = [r for r in (nar.source_record_ids or []) if r != record_id]
                nar.version += 1
                nar.updated_at = now_ts
                invalidated_narratives.append(nar.slug)
                n_key = "narrative:" + nar.slug
                n_unit_ids = list((await db.execute(select(MemoryServingUnit.id).where(
                    MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == n_key,
                ))).scalars())
                await db.execute(delete(MemoryServingUnit).where(
                    MemoryServingUnit.user_id == user_id, MemoryServingUnit.stable_key == n_key,
                ))
                await db.execute(update(MemoryChange).where(
                    MemoryChange.user_id == user_id, MemoryChange.source_key.like(n_key + ":%"),
                    MemoryChange.revoked.is_(False),
                ).values(revoked=True, routing_text=""))
                if n_unit_ids and getattr(self.store, "semantic", None) and self.store.semantic.enabled:
                    for uid in n_unit_ids:
                        await self.store.semantic.delete_unit(db, user_id, uid)
                if nar.source_record_ids:
                    from app.services.coordination_budget import insert_for
                    await db.execute(insert_for(db, IaconWork).values(
                        id=uuid4().hex, user_id=user_id,
                        work_key=f"dream_rebuild:{nar.slug}:v{nar.version}",
                        kind="dream_rebuild",
                        payload={"narrative_slug": nar.slug, "tier": nar.tier},
                        status="pending", attempts=0, available_at=now_ts, updated_at=now_ts,
                    ).on_conflict_do_nothing())

        # 5. Cancel/invalidate pending IaconWork queue items referencing this record or retired sources
        pending_work = (await db.execute(select(IaconWork).where(
            IaconWork.user_id == user_id,
            IaconWork.status.in_(("pending", "held", "claimed")),
        ))).scalars().all()
        cancelled_work = 0
        retired_set = set(retired_source_ids)
        for work in pending_work:
            p = work.payload or {}
            if (p.get("record_id") == record_id
                    or (retired_set and p.get("source_id") in retired_set)
                    or (retired_set and any(sid in retired_set for sid in (p.get("source_ids") or [])))):
                work.status = "cancelled"
                work.error = f"invalidated_by_delete:{record_id}"
                work.updated_at = now_ts
                cancelled_work += 1

        # 6. Supersede active followups for this record
        await db.execute(update(IaconFollowup).where(
            IaconFollowup.user_id == user_id, IaconFollowup.state_id == record_id,
            IaconFollowup.status.in_(["scheduled", "sending", "sent", "unknown", "snoozed", "muted", "reply_pending", "needs_review"]),
        ).values(status="superseded"))

        # 7. Evict all in-memory caches (catalog, source_cache, semantic buckets, sparse PPR graph) & bump epoch
        if hasattr(self.store, "invalidate_user_caches"):
            self.store.invalidate_user_caches(user_id)
        await bump_epoch(db, user_id)
        return {
            "deleted": True,
            "record_id": record_id,
            "retired_sources": retired_source_ids,
            "invalidated_narratives": invalidated_narratives,
            "cancelled_work": cancelled_work,
        }

    async def delete_record(self, user_id: int, record_id: str, *, actor: str = "owner", retire_sources: bool = True, db=None) -> dict:
        """Explicit owner deletion of a canonical record with full cross-layer propagation."""
        if actor not in {"owner", "telegram_owner"}:
            raise ValueError("Only the owner can permanently delete canonical records")
        if db is None:
            async with write_session(self.sessions) as own:
                return await self.delete_record(user_id, record_id, actor=actor, retire_sources=retire_sources, db=own)
        row = await db.get(IaconRecord, record_id)
        if not row or row.user_id != user_id:
            raise KeyError("Record not found")
        now_ts = datetime.now(timezone.utc)
        row.status = "deleted"
        row.version += 1
        row.updated_at = now_ts
        db.add(IaconRevision(
            record_id=row.id, version=row.version,
            command_id=f"delete:{row.id}:{row.version}", actor=actor,
            snapshot=snapshot(row),
        ))
        await db.flush()
        return await self._cascade_delete_record(db, user_id, record_id, retire_sources=retire_sources)

    async def list_narratives(self, user_id: int, *, tiers: tuple[str, ...] | None = None, db=None) -> list[dict]:
        if db is None:
            async with self.sessions() as own:
                return await self.list_narratives(user_id, tiers=tiers, db=own)
        query = select(IaconNarrative).where(
            IaconNarrative.user_id == user_id,
            IaconNarrative.status == "accepted",
        )
        if tiers:
            query = query.where(IaconNarrative.tier.in_(tiers))
        rows = (await db.execute(query.order_by(
            IaconNarrative.priority_weight.desc(), IaconNarrative.updated_at.desc()
        ))).scalars().all()
        return [{
            "id": r.id, "tier": r.tier, "slug": r.slug, "title": r.title,
            "summary_markdown": r.summary_markdown,
            "explicit_facts": r.explicit_facts, "inferred_links": r.inferred_links,
            "source_record_ids": r.source_record_ids, "source_edge_ids": r.source_edge_ids,
            "time_span_start": r.time_span_start.isoformat() if r.time_span_start else None,
            "time_span_end": r.time_span_end.isoformat() if r.time_span_end else None,
            "priority_weight": r.priority_weight, "owner_locked": r.owner_locked,
            "version": r.version,
        } for r in rows]

    async def backfill_links_and_aliases(self, user_id: int) -> dict:
        """Idempotently migrate existing IaconRecord aliases and IaconLink rows into IaconEntityAlias and IaconEdge."""
        now_ts = datetime.now(timezone.utc)
        edges_created = 0
        aliases_created = 0
        async with write_session(self.sessions) as db:
            records = (await db.execute(select(IaconRecord).where(
                IaconRecord.user_id == user_id, IaconRecord.status == "accepted",
            ))).scalars().all()
            rec_by_id = {r.id: r for r in records}
            existing_aliases = set((await db.execute(select(
                IaconEntityAlias.record_id, IaconEntityAlias.alias_folded
            ).where(IaconEntityAlias.user_id == user_id))).all())
            for r in records:
                raw_aliases = [r.title, *((r.payload or {}).get("aliases") or [])]
                for raw in raw_aliases:
                    cleaned = str(raw or "").strip()[:256]
                    folded_val = fold(cleaned).strip()
                    if len(folded_val) >= 2 and (r.id, folded_val) not in existing_aliases:
                        existing_aliases.add((r.id, folded_val))
                        db.add(IaconEntityAlias(
                            user_id=user_id, record_id=r.id,
                            alias_folded=folded_val, alias_raw=cleaned, source="migration",
                        ))
                        aliases_created += 1

            links = (await db.execute(select(IaconLink).where(
                IaconLink.user_id == user_id,
            ))).scalars().all()
            existing_edges = set((await db.execute(select(
                IaconEdge.source_id, IaconEdge.target_id, IaconEdge.relation
            ).where(
                IaconEdge.user_id == user_id,
                IaconEdge.status.in_(("accepted", "superseded", "contested")),
            ))).all())
            for lnk in links:
                if lnk.source_id not in rec_by_id or lnk.target_id not in rec_by_id:
                    continue
                sig = (lnk.source_id, lnk.target_id, lnk.relation)
                if sig in existing_edges:
                    continue
                existing_edges.add(sig)
                src_rec = rec_by_id[lnk.source_id]
                vf = self._parse_dt((src_rec.payload or {}).get("valid_from")) or self._parse_dt((src_rec.payload or {}).get("starts_on"))
                vu = self._parse_dt((src_rec.payload or {}).get("valid_until")) or self._parse_dt((src_rec.payload or {}).get("closed_on"))
                db.add(IaconEdge(
                    id=uuid4().hex, user_id=user_id, source_id=lnk.source_id,
                    target_id=lnk.target_id, relation=lnk.relation[:48],
                    description=f"{src_rec.title} ({lnk.relation})",
                    epistemic_mode="explicit", confidence=1.0,
                    valid_from=vf, valid_until=vu, recorded_at=now_ts,
                    evidence=list(src_rec.evidence or []),
                    status="accepted", version=1, updated_at=now_ts,
                ))
                edges_created += 1
            if edges_created or aliases_created:
                if hasattr(self.store, "graph"):
                    self.store.graph.invalidate_user(user_id)
                await bump_epoch(db, user_id)
        return {"user_id": user_id, "records_scanned": len(records), "aliases_created": aliases_created, "edges_created": edges_created}
