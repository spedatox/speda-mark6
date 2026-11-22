"""Immutable intake, local indexes and atomic publication of disposable views.

This is the renovation's serving store, not a replacement for final's canonical
domain writers. It exposes no agent-facing write capability.
"""
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import hashlib
import os
import re
import unicodedata
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.memory_serving import MemoryChange, MemoryServingState, MemoryServingUnit
from app.models.iacon import IaconRecord, IaconSourceRetirement
from app.schemas.memory_runtime import EvidenceSection
from app.services.postgres_search import PostgresMemorySearch, pin_search_snapshot
from app.services.semantic_index import SemanticIndex


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
    await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages (created_at)")
    await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages (session_id, created_at)")
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
        self.semantic = SemanticIndex(session_factory)
        from app.services.associative_graph import AssociativeGraphIndex
        self.graph = AssociativeGraphIndex()

    def invalidate_user_caches(self, user_id: int) -> None:
        """Deterministically evict all in-memory caches for user_id across layers."""
        for key in [k for k in self.catalog if k[0] == user_id]:
            self.catalog.pop(key, None)
        for key in [k for k in self.cache if k[0] == user_id]:
            self.cache.pop(key, None)
        self.source_cache.clear()
        self.source_cache_bytes = 0
        self.semantic.evict_user(user_id)
        self.graph.invalidate_user(user_id)

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
        await self.semantic.warm()
        if self.semantic.enabled:
            try:
                async with self.sessions() as db, db.begin():
                    await self.semantic.sync_units_for_user(db, 1, max_new=512)
                    await self.semantic.sync_recent_sources(db, 1, scan_limit=250, max_new=128)
            except Exception:
                pass

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
        if not sections or kind not in {"core", "person", "situation", "project", "course", "episode", "narrative"}:
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
            await db.flush()
            await db.refresh(old)
            target_unit = old
        else:
            if expected_revision != 0:
                raise StalePublication("Serving unit no longer exists")
            target_unit = MemoryServingUnit(user_id=context.user_id, stable_key=stable_key,
                                            revision=1, **values)
            db.add(target_unit)
            revision = 1
            await db.flush()
        if self.semantic.enabled:
            try:
                await self.semantic.sync_unit_sections(db, context.user_id, target_unit)
            except Exception:
                pass
        await bump_epoch(db, context.user_id)
        return revision

    async def revoke(self, context, source_id: int) -> None:
        async with self.sessions() as db, db.begin():
            result = await db.execute(update(MemoryChange).where(
                MemoryChange.user_id == context.user_id, MemoryChange.id == source_id
            ).values(revoked=True, routing_text=""))
            if result.rowcount:
                if self.semantic.enabled:
                    try:
                        await self.semantic.delete_source(db, context.user_id, source_id)
                    except Exception:
                        pass
                self.invalidate_user_caches(context.user_id)
                await bump_epoch(db, context.user_id)

    async def read(self, context, terms: list[str], *, limit=32, source_ids=(),
                   include_recent=True, deadline=None, raw_query: str = "") -> dict:
        """Bounded indexed reads on an independent AsyncSession (aiosqlite thread)."""
        query_vec = None
        sem_unit_hits: list[tuple[int, int, float]] = []
        sem_source_hits: list[tuple[int, float]] = []
        semantic_query = (raw_query or " ".join(terms)).strip()
        if self.semantic.enabled and semantic_query:
            query_vec = await self.semantic.embed_query(semantic_query)
            if query_vec is not None:
                try:
                    async with self.sessions() as sdb, sdb.begin():
                        sem_unit_hits = await self.semantic.search_units(sdb, context.user_id, query_vec, limit=32)
                        sem_source_hits = await self.semantic.search_sources(sdb, context.user_id, query_vec, limit=24)
                except Exception:
                    sem_unit_hits, sem_source_hits = [], []
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
            # Specific sister/mother requests must not expand to every sibling/parent,
            # except when no catalog entry carries the gendered tag (e.g. Turkish "kardeşi").
            excluded_opposite = None
            if len(requested.intersection({"sister", "brother"})) == 1:
                specific = next(iter(requested.intersection({"sister", "brother"})))
                if any(specific in family for _, _, _, family in self.catalog[catalog_key]):
                    requested.discard("sibling")
                else:
                    excluded_opposite = "brother" if specific == "sister" else "sister"
            if len(requested.intersection({"mother", "father"})) == 1:
                specific = next(iter(requested.intersection({"mother", "father"})))
                if any(specific in family for _, _, _, family in self.catalog[catalog_key]):
                    requested.discard("parent")
                else:
                    excluded_opposite = "father" if specific == "mother" else "mother"
            person_ids, name_terms, relation_ids = set(), set(), set()
            named_candidates = []
            for person_id, title, aliases, family in self.catalog[catalog_key]:
                words = set(re.findall(r"\w+", fold(title + " " + " ".join(aliases))))
                matches = words.intersection(terms)
                if matches:
                    named_candidates.append((person_id, matches))
                if requested.intersection(family) and (not excluded_opposite or excluded_opposite not in family):
                    relation_ids.add(person_id)
            if named_candidates:
                # A shared surname must not flood a named person's dossier
                # retrieval with every family member. Keep the most specific
                # matches; a surname-only query remains explicitly ambiguous.
                specificity = max(len(matches) for _, matches in named_candidates)
                specific = [(pid, matches) for pid, matches in named_candidates if len(matches) == specificity]
                person_ids.update(pid for pid, _ in specific)
                frequencies = {term: sum(term in matches for _, matches in named_candidates)
                               for _, matches in specific for term in matches}
                rarest = min(frequencies.values())
                name_terms.update(term for term, count in frequencies.items() if count == rarest)
            if name_terms:
                # A named person's sister is not automatically the owner's sister.
                relation_ids.clear()
            person_ids.update(relation_ids)
            units, hits = list(core), []
            state_candidates = []
            work_rows = []
            if include_recent:
                from app.models.iacon import IaconWork
                from app.services.memory_query import planning_window, turn_query
                local_day = datetime.now(ZoneInfo(context.timezone)).date()
                today = local_day.isoformat()
                query = turn_query(context)
                start_day, end_day = planning_window(query, local_day)
                primary = fold(query.partition("\n")[0])
                planning = (start_day != local_day or end_day != local_day or bool(re.search(
                    r"\b(plan\w*|schedule|calendar|travel\w*|trip|vacation|holiday|midterm\w*|exam\w*|"
                    r"study|bus|flight|takvim\w*|tatil\w*|seyahat\w*|gezi\w*|vize\w*|sinav\w*|"
                    r"agenda|briefing|good morning|gunaydin|what should i focus on|haftalik plan)\b", primary))
                    or bool(context.trigger_payload.get("include_attention_digest") or context.trigger_payload.get("attention_only")))
                payload = IaconRecord.payload
                status = payload["status"].as_string()
                high = payload["salience"].as_string() == "high"
                review = payload["review_on"].as_string()
                verified = payload["verified_on"].as_string()
                starts, ends = payload["starts_on"].as_string(), payload["ends_on"].as_string()
                state_order = []
                if planning:
                    dated = or_(starts.is_not(None), ends.is_not(None))
                    # Promote matching titles before the database candidate cutoff.
                    # A dated commitment can matter even when its title shares no word with the question.
                    from app.services.memory_query import query_terms
                    generic_terms = {"next", "week", "tomorrow", "today", "should", "plan", "calendar",
                                     "schedule", "hangi", "hafta", "gelecek", "onumuzdeki", "yarin"}
                    title_terms = {word for word in query_terms(primary) if len(word) >= 4} - generic_terms
                    for cue, labels in (
                        (r"\b(?:study|studying|midterm\w*|exam\w*|vize\w*|sinav\w*)", ("exam", "midterm", "vize", "sınav")),
                        (r"\b(?:trip|travel\w*|flight|bus|seyahat\w*|yolculuk\w*)", ("trip", "travel", "seyahat", "yolculuk")),
                        (r"\b(?:vacation|holiday|tatil\w*)", ("vacation", "holiday", "tatil")),
                    ):
                        if re.search(cue, primary):
                            title_terms.update(labels)
                    if title_terms:
                        title_matches = or_(*(func.lower(IaconRecord.title).contains(word, autoescape=True)
                                              for word in sorted(title_terms)))
                        state_order.append(case((title_matches, 0), else_=1))
                    state_order.append(case((and_(starts.is_not(None), ends.is_not(None)), 0),
                                            (dated, 1), else_=2))
                state_candidates = (await db.execute(select(MemoryServingUnit, IaconRecord).join(IaconRecord,
                    MemoryServingUnit.stable_key == literal("iacon:") + IaconRecord.id).where(
                    MemoryServingUnit.user_id == context.user_id, IaconRecord.user_id == context.user_id,
                    IaconRecord.kind == "state", IaconRecord.status == "accepted",
                    status.in_(("active", "waiting", "planned")), payload["closed_on"].as_string().is_(None),
                    verified <= today, or_(high, review > today),
                    or_(starts.is_(None), starts <= end_day.isoformat()), or_(ends.is_(None), ends >= start_day.isoformat()),
                ).order_by(*state_order, case((and_(high, status == "active"), 0), (high, 1),
                               (payload["salience"].as_string() == "low", 3), else_=2),
                           verified.desc(),
                           case((status == "active", 0), (status == "waiting", 1), else_=2),
                           IaconRecord.updated_at.desc(),
                           MemoryServingUnit.id).limit(12))).all()
                units.extend(unit for unit, _ in state_candidates)
                work_rows = (await db.execute(select(IaconWork).where(
                    IaconWork.user_id == context.user_id,
                    IaconWork.kind.in_(["review", "observed_state"]),
                    IaconWork.status.in_(["pending", "observed"]),
                ).order_by(IaconWork.updated_at.desc(), IaconWork.id).limit(12))).scalars().all()
            recent_ids = set()
            if person_ids:
                units += (await db.execute(select(MemoryServingUnit).where(
                    MemoryServingUnit.user_id == context.user_id,
                    MemoryServingUnit.id.in_(sorted(person_ids)[:limit])
                ))).scalars().all()
            if sem_unit_hits:
                sem_uids = list(dict.fromkeys(uid for uid, _, _ in sem_unit_hits))[:limit]
                units += (await db.execute(select(MemoryServingUnit).where(
                    MemoryServingUnit.user_id == context.user_id,
                    MemoryServingUnit.id.in_(sem_uids)
                ))).scalars().all()
            if terms:
                focused_terms = sorted(name_terms) if name_terms else terms
                units += await self._search(db, MemoryServingUnit, "units", context.user_id, focused_terms, limit)
                hits += await self._search(db, MemoryChange, "sources", context.user_id, focused_terms, limit)
                if name_terms:
                    hits += await self._search(db, MemoryChange, "sources", context.user_id,
                                               focused_terms, 12, recent=True)
            if sem_source_hits:
                sem_sids = list(dict.fromkeys(sid for sid, _ in sem_source_hits))[:limit]
                hits += (await db.execute(select(MemoryChange).where(
                    MemoryChange.user_id == context.user_id, MemoryChange.id.in_(sem_sids),
                    MemoryChange.revoked.is_(False)
                ).limit(limit))).scalars().all()
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
            valid_by_key = {unit["key"]: unit for unit in valid_units}
            current_states = []
            canonical_state_ids = set()
            canonical_state_titles = set()
            for unit, state in state_candidates:
                valid = valid_by_key.get(unit.stable_key)
                if valid is None:
                    continue
                for section in valid["sections"]:
                    provenance = deps[section["source_id"]].provenance
                    if provenance.get("iacon_record") == state.id and provenance.get("version") == state.version:
                        canonical_state_ids.add(state.id)
                        canonical_state_titles.add(fold(state.title))
                        current_states.append({"record_id": state.id, "version": state.version, "title": state.title,
                            "payload": state.payload, "section": section, "revision": unit.revision})
                        break
            provisional_states = []
            seen_provisional_titles = set()
            for work in work_rows:
                raw_payload = work.payload if isinstance(work.payload, dict) else {}
                if work.kind == "review":
                    cmd = raw_payload.get("command")
                    if not isinstance(cmd, dict) or cmd.get("kind") != "state":
                        continue
                    state_payload = cmd.get("payload")
                    title = str(cmd.get("title") or "").strip()
                    record_id = cmd.get("record_id")
                    authority = "pending_review"
                    basis = "owner_statement"
                    evidence_note = None
                else:
                    state_payload = raw_payload.get("state")
                    title = str(raw_payload.get("title") or "").strip()
                    record_id = raw_payload.get("record_id")
                    authority = "observed_awaiting_owner_confirmation"
                    basis = str(raw_payload.get("basis") or "tool_observation")
                    evidence_note = raw_payload.get("evidence_note")
                if not title or not isinstance(state_payload, dict) or state_payload.get("closed_on"):
                    continue
                folded_title = fold(title)
                if (record_id and record_id in canonical_state_ids) or folded_title in canonical_state_titles or folded_title in seen_provisional_titles:
                    continue
                seen_provisional_titles.add(folded_title)
                provisional_states.append({
                    "work_id": work.id,
                    "title": title,
                    "authority": authority,
                    "basis": basis,
                    "evidence_note": evidence_note,
                    "payload": state_payload,
                })
            source_rows = {s.id: s for s in hits if
                (s.id not in retired_ids or s.id in source_ids) and
                (s.provenance.get("eligible_for_context", True) or s.id in source_ids) and
                not (s.source_role == "canonical" and s.id in dep_ids and s.id not in source_ids)}
            if context.run_id:
                # Prepared personal knowledge is owner-scoped. Raw chat excerpts retain their
                # original chat visibility, including when the indexed source lane selects them.
                from app.models.client_compat import ClientSession, HiddenMessage
                from app.models.coordination import Room, RoomEvent
                from app.models.message import Message
                from app.models.session import Session
                mids = {source.message_id for source in source_rows.values() if source.message_id}
                if mids:
                    aids = {context.agent_id}
                    if context.coordination:
                        aids.add(context.coordination.profiles.require(context.agent_id).stable_id)
                    if context.agent_id == "speda":
                        aids.add("warroom")
                    admitted = await db.scalar(select(RoomEvent).where(
                        RoomEvent.room_id == context.room_id, RoomEvent.run_id == context.run_id,
                        RoomEvent.kind.in_(["user_message", "automation_input"]))
                        .order_by(RoomEvent.sequence).limit(1))
                    visible = set()
                    if admitted:
                        visible = set(await db.scalars(select(Message.id).join(Session).outerjoin(
                            Room, Room.session_id == Session.id).outerjoin(
                            ClientSession, ClientSession.session_id == Session.id).where(
                                Message.id.in_(mids), Message.id < admitted.payload["message_id"],
                                Session.user_id == context.user_id, Session.agent_id.in_(aids),
                                Room.project_id == context.project_id,
                                or_(ClientSession.hidden.is_(None), ClientSession.hidden.is_(False)),
                                Message.id.not_in(select(HiddenMessage.message_id)),
                                Message.role == "user", Session.triggered_by == "user")))
                    source_rows = {sid: source for sid, source in source_rows.items()
                                   if not source.message_id or source.message_id in visible}
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
            unit_id_by_key = {u.stable_key: u.id for u in units}
            key_by_unit_id = {u.id: u.stable_key for u in units}
            semantic_unit_scores = {
                (key_by_unit_id[uid], sidx): sc
                for uid, sidx, sc in sem_unit_hits
                if uid in key_by_unit_id
            }
            semantic_source_scores = {sid: sc for sid, sc in sem_source_hits}

            # --- Operation Iacon: Sparse Bi-Temporal PPR + Autobiographical Dreaming Narratives ---
            ppr_record_scores: dict[str, float] = {}
            associative_chains: list[list[dict]] = []
            autobiographical_narratives: list[dict] = []
            dreaming_enabled = os.getenv("SPEDA_DREAMING_CONTEXT", "true").strip().lower() not in {"0", "false", "no", "off"}

            if self.graph.enabled and semantic_query:
                try:
                    from app.services.associative_graph import parse_structural_temporal_scope
                    graph = await self.graph.load(db, context.user_id, epoch)
                    if graph.num_nodes > 0 and graph.num_edges > 0:
                        seed_weights: dict[str, float] = {}
                        for (ukey, _), sc in semantic_unit_scores.items():
                            if ukey.startswith("iacon:"):
                                rid = ukey[6:]
                                seed_weights[rid] = max(seed_weights.get(rid, 0.0), float(sc))
                        for rid in graph.match_aliases(semantic_query):
                            seed_weights[rid] = max(seed_weights.get(rid, 0.0), 0.90)
                        for u in units:
                            if u.stable_key.startswith("iacon:") and u.kind != "core":
                                rid = u.stable_key[6:]
                                if rid not in seed_weights and any(t in u.routing_text for t in terms):
                                    seed_weights[rid] = 0.50
                        if seed_weights:
                            local_today = datetime.now(ZoneInfo(context.timezone)).date()
                            t_scope = parse_structural_temporal_scope(semantic_query, today=local_today, default_mode="biographical")
                            ppr_hits = graph.personalized_pagerank(seed_weights, scope=t_scope, alpha=0.25, iterations=15, top_k=16)
                            ppr_record_scores = {rid: sc for rid, sc in ppr_hits}
                            top_seeds = [rid for rid, _ in sorted(seed_weights.items(), key=lambda kv: -kv[1])[:6]]
                            top_targets = [rid for rid, _ in ppr_hits[:10]]
                            raw_chains = graph.extract_chains(top_seeds, top_targets, scope=t_scope, max_hops=3, max_chains=6)
                            for ch in raw_chains:
                                associative_chains.append([{
                                    "edge_id": e.edge_id,
                                    "source_id": e.source_id,
                                    "source": graph.node_titles.get(e.source_id, e.source_id),
                                    "relation": e.relation,
                                    "target_id": e.target_id,
                                    "target": graph.node_titles.get(e.target_id, e.target_id),
                                    "description": e.description,
                                    "epistemic_mode": e.epistemic_mode,
                                    "valid_from": e.valid_from.date().isoformat() if e.valid_from else None,
                                    "valid_until": e.valid_until.date().isoformat() if e.valid_until else None,
                                } for e in ch])
                            # Ensure PPR-reached iacon units are present in valid_units
                            existing_keys = {u["key"] for u in valid_units}
                            missing_keys = ["iacon:" + rid for rid in ppr_record_scores if ("iacon:" + rid) not in existing_keys][:12]
                            if missing_keys:
                                extra_units = (await db.execute(select(MemoryServingUnit).where(
                                    MemoryServingUnit.user_id == context.user_id,
                                    MemoryServingUnit.stable_key.in_(missing_keys),
                                ))).scalars().all()
                                for eu in extra_units:
                                    unit_id_by_key[eu.stable_key] = eu.id
                                    valid_units.append({
                                        "key": eu.stable_key, "kind": eu.kind, "title": eu.title,
                                        "folded_title": fold(eu.title), "aliases": eu.aliases,
                                        "title_words": set(re.findall(r"\w+", fold(eu.title))),
                                        "identity": eu.stable_key, "revision": eu.revision,
                                        "sections": eu.sections,
                                        "section_search": [fold(s.get("text", "")) for s in eu.sections],
                                        "section_dates": [parsed_days(s.get("text", "")) for s in eu.sections],
                                    })
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("associative_ppr_failed: %s", exc, exc_info=True)
                    ppr_record_scores, associative_chains = {}, []

            if dreaming_enabled:
                try:
                    from app.models.iacon import IaconNarrative
                    nar_rows = (await db.execute(select(IaconNarrative).where(
                        IaconNarrative.user_id == context.user_id,
                        IaconNarrative.status == "accepted",
                    ).order_by(IaconNarrative.priority_weight.desc(), IaconNarrative.updated_at.desc()).limit(24))).scalars().all()
                    ppr_node_set = set(ppr_record_scores.keys())
                    for nar in nar_rows:
                        n_key = "narrative:" + nar.slug
                        sem_sc = float(semantic_unit_scores.get((n_key, 0), 0.0))
                        # Also check if narrative unit had a semantic hit even if not in units list
                        if sem_sc == 0.0 and sem_unit_hits:
                            n_uid = await db.scalar(select(MemoryServingUnit.id).where(
                                MemoryServingUnit.user_id == context.user_id,
                                MemoryServingUnit.stable_key == n_key,
                            ))
                            if n_uid is not None:
                                for uid, sidx, sc in sem_unit_hits:
                                    if uid == n_uid and sidx == 0:
                                        sem_sc = float(sc)
                                        break
                        overlap = len(ppr_node_set.intersection(nar.source_record_ids or []))
                        autobiographical_narratives.append({
                            "id": nar.id, "tier": nar.tier, "slug": nar.slug, "title": nar.title,
                            "summary_markdown": nar.summary_markdown,
                            "explicit_facts": nar.explicit_facts, "inferred_links": nar.inferred_links,
                            "source_record_ids": nar.source_record_ids,
                            "time_span_start": nar.time_span_start.date().isoformat() if nar.time_span_start else None,
                            "time_span_end": nar.time_span_end.date().isoformat() if nar.time_span_end else None,
                            "priority_weight": nar.priority_weight,
                            "semantic_score": sem_sc,
                            "ppr_overlap": overlap,
                            "version": nar.version,
                        })
                except Exception:
                    autobiographical_narratives = []

            return {"epoch": epoch, "boundary": boundary, "units": valid_units, "current_states": current_states,
                    "provisional_states": provisional_states,
                    "semantic_unit_scores": semantic_unit_scores,
                    "semantic_source_scores": semantic_source_scores,
                    "ppr_record_scores": ppr_record_scores,
                    "associative_chains": associative_chains,
                    "autobiographical_narratives": autobiographical_narratives,
                    "unit_id_by_key": unit_id_by_key,
                    "entity_terms": sorted(name_terms),
                    "relation_keys": {u.stable_key for u in units if u.id in relation_ids},
                    "contexts": {s.id: s.provenance.get("prior_context")
                                 for s in [*deps.values(), *source_rows.values()]},
                    "sources": sources, "candidate_limit_reached": len(hits) >= limit}

    async def matching_sessions(self, db, owner, terms):
        hits = await self._search(db, MemoryChange, "sources", owner, terms, 12)
        return list(dict.fromkeys(hit.session_id for hit in hits if hit.message_id and hit.session_id))[:4]

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
