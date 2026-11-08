"""Immutable intake, local indexes and atomic publication of disposable views.

This is the renovation's serving store, not a replacement for final's canonical
domain writers. It exposes no agent-facing write capability.
"""
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import hashlib
import re
import unicodedata

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.memory_serving import MemoryChange, MemoryServingState, MemoryServingUnit
from app.models.iacon import IaconSourceRetirement
from app.schemas.memory_runtime import EvidenceSection
from app.services.postgres_search import PostgresMemorySearch, pin_search_snapshot


def fold(value: str) -> str:
    value = value.casefold().replace("ı", "i")
    if value.isascii():
        return value
    # Latin diacritics are search variants. Keep other scripts and punctuation;
    # avoid a Python callback for every character in large original sources.
    return re.sub(r"[\u0300-\u036f]", "", unicodedata.normalize("NFKD", value))


def source_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def init_indexes(conn) -> None:
    for table, target in (("memory_changes", "sources"), ("memory_serving_units", "units")):
        await conn.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_{target}_fts "
            f"USING fts5(routing_text, content='{table}', content_rowid='id')")
        await conn.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS memory_{target}_ai AFTER INSERT ON {table} BEGIN "
            f"INSERT INTO memory_{target}_fts(rowid,routing_text) VALUES(new.id,new.routing_text); END")
        await conn.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS memory_{target}_ad AFTER DELETE ON {table} BEGIN "
            f"INSERT INTO memory_{target}_fts(memory_{target}_fts,rowid,routing_text) "
            f"VALUES('delete',old.id,old.routing_text); END")
        await conn.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS memory_{target}_au AFTER UPDATE OF routing_text ON {table} BEGIN "
            f"INSERT INTO memory_{target}_fts(memory_{target}_fts,rowid,routing_text) "
            f"VALUES('delete',old.id,old.routing_text); "
            f"INSERT INTO memory_{target}_fts(rowid,routing_text) VALUES(new.id,new.routing_text); END")


async def bump_epoch(db, user_id: int) -> None:
    dialect = db.bind.dialect.name
    factory = sqlite_insert if dialect == "sqlite" else pg_insert
    await db.execute(factory(MemoryServingState).values(user_id=user_id, epoch=1)
                     .on_conflict_do_update(index_elements=["user_id"],
                         set_={"epoch": MemoryServingState.epoch + 1}))


async def capture_source(db, *, user_id: int, source_key: str, content: str,
                         source_role: str = "owner", session_id=None,
                         message_id=None, provenance=None) -> MemoryChange:
    """Append within the caller's transaction; never commit the original separately."""
    prior = (await db.execute(select(MemoryChange).where(
        MemoryChange.user_id == user_id, MemoryChange.source_key == source_key
    ))).scalar_one_or_none()
    if prior:
        if prior.content_hash != digest(content):
            raise ValueError("An immutable source key cannot acquire different content")
        return prior
    if source_role not in {"owner", "canonical", "quoted"}:
        raise ValueError("Unsupported source authority")
    row = MemoryChange(user_id=user_id, source_key=source_key, content=content,
        content_hash=digest(content), source_role=source_role, session_id=session_id,
        message_id=message_id, routing_text=fold(content + "\n" + ((provenance or {}).get("prior_context") or {}).get("text", "")), provenance=provenance or {},
        outcome="pending" if source_role == "owner" and content.strip() else "source_only")
    db.add(row)
    await db.flush()
    return row


class StalePublication(ValueError):
    pass


class MemoryStore:
    def __init__(self, session_factory, *, cache_size=512):
        self.sessions = session_factory
        self.cache_size = cache_size
        self.cache: OrderedDict = OrderedDict()
        self.catalog: OrderedDict = OrderedDict()
        self.source_cache: OrderedDict = OrderedDict()
        self.source_cache_bytes = 0
        self.postgres_search = PostgresMemorySearch()

    @asynccontextmanager
    async def reader(self, deadline=None):
        async with self.sessions() as db:
            driver = None
            if db.bind.dialect.name == "postgresql":
                await pin_search_snapshot(db)
            elif deadline is not None and db.bind.dialect.name == "sqlite":
                connection = await db.connection()
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                await driver.set_progress_handler(lambda: int(deadline.remaining() <= 0), 1000)
            try:
                yield db
            finally:
                if driver is not None:
                    await driver.set_progress_handler(None, 0)

    async def warm_search(self):
        async with self.reader() as db:
            if db.bind.dialect.name == "postgresql":
                await self.postgres_search.rank(db, MemoryChange, "sources", 0, '"warm"', 1, False)
                await self.postgres_search.rank(db, MemoryServingUnit, "units", 0, '"warm"', 1, False)

    async def epoch(self, context, deadline=None) -> int:
        async with self.reader(deadline) as db:
            return (await db.execute(select(MemoryServingState.epoch).where(
                MemoryServingState.user_id == context.user_id))).scalar_one_or_none() or 0

    async def publish(self, context, *, stable_key: str, kind: str, title: str,
                      sections: list[EvidenceSection], aliases=(), expected_revision=0,
                      db=None, source_only_ids=()) -> int:
        if db is None:
            async with self.sessions() as own_db:
                async with own_db.begin():
                    return await self.publish(context, stable_key=stable_key, kind=kind,
                        title=title, sections=sections, aliases=aliases,
                        expected_revision=expected_revision, db=own_db, source_only_ids=source_only_ids)
        if not sections or kind not in {"core", "person", "situation", "project", "course", "episode"}:
            raise ValueError("A serving unit needs sourced sections and a known kind")
        if len(sections) > 64 or len(stable_key) > 256 or len(title) > 256:
            raise ValueError("Serving unit exceeds its bounded shape")
        ids = {section.source_id for section in sections}
        sources = {s.id: s for s in (await db.execute(select(MemoryChange).where(
            MemoryChange.user_id == context.user_id, MemoryChange.id.in_(ids),
            MemoryChange.revoked.is_(False)))).scalars()}
        dependencies = []
        retired = await db.scalar(select(IaconSourceRetirement.source_id).where(
            IaconSourceRetirement.user_id == context.user_id,
            IaconSourceRetirement.source_id.in_(ids)).limit(1))
        if retired is not None:
            raise ValueError("Superseded source cannot be published as active context")
        for section in sections:
            source = sources.get(section.source_id)
            if not source or source.content[section.start:section.end] != section.text:
                raise ValueError("Serving text must be an exact owner-scoped source span")
            expected = {"owner": "owner_statement", "canonical": "canonical_record",
                        "quoted": "quoted_material"}[source.source_role]
            if section.attribution != expected:
                raise ValueError("Source authority cannot be promoted by a projection")
            if not source.provenance.get("eligible_for_context", True) and not (
                source.id in source_only_ids and kind == "situation" and stable_key.startswith("iacon:")
                and source.source_role == "canonical" and "/states/" in source.provenance.get("original_path", "")
            ):
                raise ValueError("Excluded source cannot be published as active context")
            if source.provenance.get("historical") and not section.historical:
                raise ValueError("Historical source cannot be published as current evidence")
        for source_id, source in sorted(sources.items()):
            dependencies.append({"source_id": source_id, "hash": source.content_hash,
                                 **({"source_only_state": True} if source_id in source_only_ids else {})})
        serialized = [s.model_dump(mode="json") for s in sections]
        aliases = sorted(set(str(a).strip() for a in aliases if str(a).strip()))
        if len(aliases) > 32 or any(len(a) > 128 for a in aliases):
            raise ValueError("Alias list exceeds bounds")
        # Aliases are routing hints, not identity merges or new canonical entities.
        values = dict(kind=kind, title=title, sections=serialized, aliases=aliases,
            compiler_version=2,
            dependencies=dependencies, routing_text=fold("\n".join(
                [title, *aliases, *(s.text for s in sections)])),
            published_at=datetime.now(timezone.utc))
        old = (await db.execute(select(MemoryServingUnit).where(
            MemoryServingUnit.user_id == context.user_id,
            MemoryServingUnit.stable_key == stable_key))).scalar_one_or_none()
        if old:
            if old.sections == serialized and old.dependencies == dependencies and old.aliases == aliases and old.title == title and old.kind == kind and old.compiler_version == 2:
                return old.revision
            result = await db.execute(update(MemoryServingUnit).where(
                MemoryServingUnit.id == old.id,
                MemoryServingUnit.revision == expected_revision
            ).values(**values, revision=expected_revision + 1))
            if result.rowcount != 1:
                raise StalePublication("Serving inputs changed; reload the affected unit")
            revision = expected_revision + 1
        else:
            if expected_revision != 0:
                raise StalePublication("Serving unit no longer exists")
            db.add(MemoryServingUnit(user_id=context.user_id, stable_key=stable_key,
                                    revision=1, **values))
            revision = 1
        await db.flush()
        await bump_epoch(db, context.user_id)
        return revision

    async def revoke(self, context, source_id: int) -> None:
        async with self.sessions() as db, db.begin():
            result = await db.execute(update(MemoryChange).where(
                MemoryChange.user_id == context.user_id, MemoryChange.id == source_id
            ).values(revoked=True, routing_text=""))
            if result.rowcount:
                await bump_epoch(db, context.user_id)

    async def read(self, context, terms: list[str], *, limit=32, source_ids=(),
                   include_recent=True, deadline=None) -> dict:
        """Bounded indexed reads on an independent AsyncSession (aiosqlite thread)."""
        async with self.reader(deadline) as db:
            # Pin the epoch and evidence in one short read transaction.
            epoch, boundary = (await db.execute(select(
                select(MemoryServingState.epoch).where(MemoryServingState.user_id == context.user_id).scalar_subquery(),
                select(func.max(MemoryChange.id)).where(MemoryChange.user_id == context.user_id).scalar_subquery()
            ))).one()
            epoch, boundary = epoch or 0, boundary or 0
            core = (await db.execute(select(MemoryServingUnit).where(
                MemoryServingUnit.user_id == context.user_id, MemoryServingUnit.kind == "core"
            ).order_by(MemoryServingUnit.id).limit(4))).scalars().all()
            catalog_key = (context.user_id, epoch)
            if catalog_key not in self.catalog:
                from app.models.user import User
                from app.services.memory_query import owner_relations
                owner_name = await db.scalar(select(User.name).where(User.id == context.user_id)) or "Owner"
                # Legacy users.name can be the assistant's display name. The
                # owner dossier is the explicit identity authority for relative
                # family routing; never rewrite the preserved legacy user row.
                dossier_names = {match[1].strip() for unit in core for match in re.finditer(
                    r"(?m)^\s*#{1,6}\s*DOSSIER:\s*([^\n—–]+)",
                    "\n".join(s.get("text", "") for s in unit.sections))}
                if len(dossier_names) == 1:
                    owner_name = next(iter(dossier_names))
                people = (await db.execute(select(
                    MemoryServingUnit.id, MemoryServingUnit.title, MemoryServingUnit.aliases, MemoryServingUnit.sections
                ).where(MemoryServingUnit.user_id == context.user_id,
                        MemoryServingUnit.kind == "person").limit(10000))).all()
                self.catalog[catalog_key] = [(pid, title, aliases, owner_relations(sections, owner_name))
                                            for pid, title, aliases, sections in people]
                while len(self.catalog) > 16:
                    self.catalog.popitem(last=False)
            from app.services.memory_query import relations
            requested = relations(" ".join(terms))
            # Specific sister/mother requests must not expand to every sibling/parent.
            if len(requested.intersection({"sister", "brother"})) == 1:
                requested.discard("sibling")
            if len(requested.intersection({"mother", "father"})) == 1:
                requested.discard("parent")
            person_ids, name_terms, relation_ids = set(), set(), set()
            for person_id, title, aliases, family in self.catalog[catalog_key]:
                words = set(re.findall(r"\w+", fold(title + " " + " ".join(aliases))))
                matches = words.intersection(terms)
                if matches:
                    person_ids.add(person_id)
                    name_terms.update(matches)
                if requested.intersection(family):
                    relation_ids.add(person_id)
            if name_terms:
                # A named person's sister is not automatically the owner's sister.
                relation_ids.clear()
            person_ids.update(relation_ids)
            units, hits = list(core), []
            if include_recent:
                from app.models.iacon import IaconRecord
                from sqlalchemy import literal, case
                units += (await db.execute(select(MemoryServingUnit).join(IaconRecord,
                    MemoryServingUnit.stable_key == literal("iacon:") + IaconRecord.id).where(
                    MemoryServingUnit.user_id == context.user_id, IaconRecord.user_id == context.user_id,
                    IaconRecord.kind == "state", IaconRecord.status == "accepted",
                    IaconRecord.payload["closed_on"].as_string().is_(None)
                ).order_by(case((IaconRecord.payload["salience"].as_string() == "high", 0), else_=1),
                           IaconRecord.payload["review_on"].as_string().nulls_first(),
                           MemoryServingUnit.id).limit(3))).scalars().all()
            recent_ids = set()
            if person_ids:
                units += (await db.execute(select(MemoryServingUnit).where(
                    MemoryServingUnit.user_id == context.user_id,
                    MemoryServingUnit.id.in_(sorted(person_ids)[:limit])
                ))).scalars().all()
            if terms:
                focused_terms = sorted(name_terms) if name_terms else terms
                units += await self._search(db, MemoryServingUnit, "units", context.user_id, focused_terms, limit)
                hits += await self._search(db, MemoryChange, "sources", context.user_id, focused_terms, limit)
                if name_terms:
                    hits += await self._search(db, MemoryChange, "sources", context.user_id,
                                               focused_terms, 12, recent=True)
            if source_ids:
                hits += (await db.execute(select(MemoryChange).where(
                    MemoryChange.user_id == context.user_id, MemoryChange.id.in_(source_ids),
                    MemoryChange.revoked.is_(False)
                ).limit(limit))).scalars().all()
            if include_recent:
                recent = (await db.execute(select(MemoryChange).where(
                    MemoryChange.user_id == context.user_id,
                    MemoryChange.source_role == "owner", MemoryChange.revoked.is_(False)
                ).order_by(MemoryChange.id.desc()).limit(4))).scalars().all()
                recent_ids = {s.id for s in recent}
                hits += recent
            units = list({u.id: u for u in units}.values())
            dep_ids = {d["source_id"] for u in units for d in u.dependencies}
            deps = {s.id: s for s in (await db.execute(select(MemoryChange).where(
                MemoryChange.user_id == context.user_id, MemoryChange.id.in_(dep_ids),
                MemoryChange.revoked.is_(False)))).scalars()}
            retired_ids = set((await db.execute(select(IaconSourceRetirement.source_id).where(
                IaconSourceRetirement.user_id == context.user_id,
                IaconSourceRetirement.source_id.in_(dep_ids | {s.id for s in hits})))).scalars())
            valid_units = []
            for unit in units:
                if any(d["source_id"] not in deps or deps[d["source_id"]].content_hash != d["hash"]
                       or d["source_id"] in retired_ids
                       or (not deps[d["source_id"]].provenance.get("eligible_for_context", True) and not (
                           d.get("source_only_state") and unit.kind == "situation" and unit.stable_key.startswith("iacon:")))
                       for d in unit.dependencies):
                    continue
                key = (context.user_id, epoch, unit.id, unit.revision)
                if key not in self.cache:
                    sections = scoped_sections(unit, deps)
                    identities = {deps[d["source_id"]].provenance.get("entity_id")
                                  for d in unit.dependencies}
                    identity = ("entity:" + next(iter(identities))) if len(identities) == 1 and None not in identities else unit.stable_key.split(":part:")[0]
                    self.cache[key] = {"key": unit.stable_key, "kind": unit.kind,
                        "identity": identity,
                        "title": unit.title, "aliases": tuple(unit.aliases),
                        "revision": unit.revision, "sections": sections,
                        "folded_title": fold(unit.title),
                        "title_words": set(re.findall(r"\w+", fold(unit.title))),
                        "section_search": [fold(s["text"]) for s in sections],
                        "section_dates": [parsed_days(s["text"]) for s in sections]}
                self.cache.move_to_end(key)
                valid_units.append(self.cache[key])
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            source_rows = {s.id: s for s in hits if
                (s.id not in retired_ids or s.id in source_ids) and
                (s.provenance.get("eligible_for_context", True) or s.id in source_ids) and
                not (s.source_role == "canonical" and s.id in dep_ids and s.id not in source_ids)}
            sources = []
            for s in source_rows.values():
                key = (context.user_id, s.id, s.content_hash)
                if key not in self.source_cache:
                    spans = exact_sections(s)
                    self.source_cache[key] = {"text": s.content, "folded_text": fold(s.content),
                        "spans": spans, "span_search": [fold(span.text) for span in spans],
                        "size": max(1, len(s.content.encode("utf-8"))*3)}
                    self.source_cache_bytes += self.source_cache[key]["size"]
                cached = self.source_cache[key]
                self.source_cache.move_to_end(key)
                provenance = {**s.provenance, **({"historical": True, "eligible_for_context": False,
                    "superseded_evidence": True} if s.id in retired_ids else {})}
                sources.append({**cached, "id": s.id, "key": s.source_key, "role": s.source_role,
                    "created_at": s.created_at.isoformat(), "message_id": s.message_id, "session_id": s.session_id,
                    "outcome": s.outcome, "recent": s.id in recent_ids, "provenance": provenance})
            while self.source_cache and (len(self.source_cache)>128 or self.source_cache_bytes>8000000):
                _, removed = self.source_cache.popitem(last=False)
                self.source_cache_bytes -= removed["size"]
            return {"epoch": epoch, "boundary": boundary, "units": valid_units,
                    "entity_terms": sorted(name_terms),
                    "relation_keys": {u.stable_key for u in units if u.id in relation_ids},
                    "contexts": {s.id: s.provenance.get("prior_context")
                                 for s in [*deps.values(), *source_rows.values()]},
                    "sources": sources, "candidate_limit_reached": len(hits) >= limit}

    async def _search(self, db, model, target, user_id, terms, limit, recent=False):
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:20])
        if db.bind.dialect.name == "sqlite":
            statement = select(model).from_statement(text(
                f"SELECT t.* FROM memory_{target}_fts f JOIN {model.__tablename__} t ON t.id=f.rowid "
                f"WHERE memory_{target}_fts MATCH :query AND t.user_id=:user_id "
                + ("AND t.revoked=0 AND COALESCE(json_extract(t.provenance,'$.eligible_for_context'),1)=1 " if target == "sources" else "") +
                ("AND t.source_role='owner' " if recent else "") +
                ("ORDER BY t.created_at DESC,t.id DESC LIMIT :limit" if recent else f"ORDER BY bm25(memory_{target}_fts),t.id DESC LIMIT :limit")))
            return (await db.execute(statement, {"query": query, "user_id": user_id,
                                                 "limit": limit})).scalars().all()
        ranked = await self.postgres_search.rank(db, model, target, user_id, query, limit, recent)
        if not ranked:
            return []
        rows = await db.scalars(select(model).where(model.user_id == user_id, model.id.in_(ranked)))
        by_id = {row.id: row for row in rows}
        return [by_id[identity] for identity in ranked]



def scoped_sections(unit, dependencies):
    """Also fence older read views built before scope inheritance was introduced."""
    sources = [dependencies[d["source_id"]] for d in unit.dependencies]
    historical = any(s.provenance.get("historical") for s in sources)
    lower = [s.provenance["valid_from"][:10] for s in sources if s.provenance.get("valid_from")]
    upper = [s.provenance["valid_until"][:10] for s in sources if s.provenance.get("valid_until")]
    result = []
    for section in unit.sections:
        source = dependencies[section["source_id"]]
        generated = unit.stable_key.startswith("iacon:") and source.provenance.get("iacon_record")
        scoped = dict(section)
        scoped["historical"] = bool(section.get("historical") or source.provenance.get("historical")
                                    or (generated and historical))
        starts = ([section["valid_from"]] if section.get("valid_from") else [])
        ends = ([section["valid_until"]] if section.get("valid_until") else [])
        if source.provenance.get("valid_from"):
            starts.append(source.provenance["valid_from"][:10])
        if source.provenance.get("valid_until"):
            ends.append(source.provenance["valid_until"][:10])
        scoped["valid_from"] = max([*starts, *(lower if generated else [])], default=None)
        scoped["valid_until"] = min([*ends, *(upper if generated else [])], default=None)
        if scoped["valid_from"] and scoped["valid_until"] and scoped["valid_from"] > scoped["valid_until"]:
            continue
        result.append(scoped)
    return result


def exact_sections(source: MemoryChange, *, max_chars=1400) -> list[EvidenceSection]:
    """Paragraph spans; oversized paragraphs remain clearly labeled source fragments."""
    attribution = {"owner": "owner_statement", "canonical": "canonical_record",
                   "quoted": "quoted_material"}[source.source_role]
    result = []
    boundaries = [0, *(match.end() for match in re.finditer(r"\r?\n[ \t]*\r?\n", source.content)), len(source.content)]
    for left, right in zip(boundaries, boundaries[1:]):
        chunk = source.content[left:right]
        trimmed = chunk.strip()
        if not trimmed:
            continue
        start = left + len(chunk) - len(chunk.lstrip())
        end = start + len(trimmed)
        # Preserve native bullet/event boundaries and their continuation lines.
        anchors = [start]
        anchors += [start + m.start() for m in re.finditer(r"(?m)^(?:[-*] |\d+[.)] |#{1,6} )", trimmed) if m.start()]
        anchors.append(end)
        for left, right in zip(anchors, anchors[1:]):
            for offset in range(left, right, max_chars):
                stop = min(right, offset + max_chars)
                result.append(EvidenceSection(source_id=source.id, start=offset, end=stop,
                    text=source.content[offset:stop], attribution=attribution,
                    fragment=right - left > max_chars))
    return result


def parsed_days(text: str) -> list[date]:
    result = []
    for value in re.findall(r"\[(\d{4}-\d{2}-\d{2})\]", text):
        try:
            result.append(date.fromisoformat(value))
        except ValueError:
            pass
    return result
