"""One provider-free serving path for initial context and deliberate lookup."""
import asyncio
from datetime import date, datetime, timedelta
import json
import logging
import re
from time import monotonic
from zoneinfo import ZoneInfo
from pydantic import ValidationError

from app.schemas.memory_runtime import Deadline, EvidenceSection, MemoryPacket, token_upper_bound
from app.services.memory_store import fold
from app.services.memory_query import (
    SCHOOL_TERM, conversation_content, education_fact_query, query_terms, relations, turn_query, withdrawal_reply,
)

logger = logging.getLogger(__name__)

_ENROLLMENT = re.compile(
    r"\b(?:enroll(?:ed|s|ing)? (?:at|in)|attends?|student at|stud(?:y|ies|ying) .*?\bat|okuyor\w*|ogrencisi)\b"
)
_PLACEMENT = re.compile(r"\b(?:placement\s*:|yerlestir\w*|yerlesti\w*)")
_FORMER_ENROLLMENT = re.compile(r"\b(?:old|former|previous|used to|graduated|completed|eski|mezun)\b")
_PROGRAM_LINE = re.compile(r"(?im)^[ \t]*\*{0,2}(?:program|bölüm)\*{0,2}:\*{0,2}[ \t]+[^\r\n]+")


def _parse_iso_day(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def classify_state_phase(payload: dict, today: date) -> tuple[str, int]:
    """Classify any situational state's temporal phase relative to today."""
    starts_on = _parse_iso_day(payload.get("starts_on"))
    ends_on = _parse_iso_day(payload.get("ends_on"))
    status = str(payload.get("status") or "open")
    soon_cutoff = today + timedelta(days=7)
    if starts_on and ends_on:
        if starts_on <= today <= ends_on:
            return ("active_now", 0)
        if today < starts_on <= soon_cutoff:
            return ("upcoming_soon", 1)
        if today > ends_on:
            return ("ended_unconfirmed", 4)
        return ("planned_later", 5)
    if starts_on:
        if starts_on <= today:
            return ("active_now", 0)
        if starts_on <= soon_cutoff:
            return ("upcoming_soon", 1)
        return ("planned_later", 5)
    if ends_on:
        if today <= ends_on:
            return ("active_now", 0)
        return ("ended_unconfirmed", 4)
    if status == "active":
        return ("active_now", 2)
    return ("open_situation", 3)


def _salience_rank(value) -> int:
    return {"high": 0, "normal": 1, "low": 2}.get(str(value or "normal"), 1)


def education_section(unit, section, today):
    """Use exact spans from the owner profile and transcript for enrollment recall."""
    if (section.attribution != "canonical_record" or section.historical
            or (section.valid_from and section.valid_from > today)
            or (section.valid_until and section.valid_until <= today)):
        return None
    if unit["kind"] == "core":
        statements = [line for line in re.split(r"\n|(?<=[.!?])\s+", fold(section.text))
                      if SCHOOL_TERM.search(line) and not _FORMER_ENROLLMENT.search(line)]
        if any(_ENROLLMENT.search(line) for line in statements):
            return "institution", 2, section
        if any(_PLACEMENT.search(line) for line in statements):
            return "institution", 1, section
    if re.search(r"\b(?:transcript|transkript)\b", unit["folded_title"]):
        if match := _PROGRAM_LINE.search(section.text):
            value = match[0].strip()
            start = section.start + match.start() + len(match[0]) - len(match[0].lstrip())
            return "program", 1, section.model_copy(update={
                "start": start, "end": start + len(value), "text": value,
            })
    return None


def section_block(section, title, *, today: date, prior_context=None, supports=None) -> tuple[str, str]:
    data = section if isinstance(section, EvidenceSection) else EvidenceSection.model_validate(section)
    handle = f"source:{data.source_id}:{data.start}:{data.end}"
    warnings = []
    if data.fragment:
        warnings.append("Source fragment; surrounding context/qualifiers may be omitted")
    if data.historical or (data.valid_until and data.valid_until <= today):
        warnings.append("historical; not evidence of current status")
    if data.review_on and data.review_on <= today:
        warnings.append("review overdue; current status unverified")
    return handle, json.dumps({"source": handle, "topic": title, **({"supports": supports} if supports else {}),
        "attribution": data.attribution, "text": data.text,
        "valid_from": str(data.valid_from) if data.valid_from else None,
        "valid_until": str(data.valid_until) if data.valid_until else None,
        "prior_context_only": prior_context,
        "warnings": warnings}, ensure_ascii=False, separators=(",", ":"))


class MemoryRuntime:
    def __init__(self, store, *, prepare_timeout=0.8, lookup_timeout=2.5):
        self.store = store
        self.prepare_timeout = prepare_timeout
        self.lookup_timeout = lookup_timeout
        self._ready = False

    async def prepare(self, context) -> MemoryPacket:
        if withdrawal_reply(context):
            return MemoryPacket(request_id=context.request_id, user_id=context.user_id)
        query = turn_query(context)
        owner_chat = context.triggered_by == "user"
        personal_job = (context.triggered_by == "n8n" and context.trigger_payload.get("automation_key")
                        and (context.trigger_payload.get("include_attention_digest")
                             or context.trigger_payload.get("attention_only")))
        if not (context.run_id and (owner_chat or personal_job)
                and "memory_lookup" in (context.allowed_tools or ())):
            return await self._read(context, query, automatic=True)
        started = monotonic()
        budget = context.memory_budget
        already_elapsed = budget.elapsed_seconds
        packet, recent_read = await asyncio.gather(
            self._read(context, query, automatic=True, reserved_bytes=min(2200, budget.automatic_limit // 3)),
            self._recent(context, query, min(1.5, max(self.prepare_timeout, 1.2), 5 - already_elapsed)),
        )
        recent, packet.recent_coverage, packet.recent_elapsed_ms = recent_read
        byte_ceiling = min(2800, budget.automatic_limit - packet.token_upper_bound, budget.turn_limit - budget.used)
        # Preserve both speakers when long replies would crowd the owner turn out.
        for length in (600, 320, 160):
            if token_upper_bound(json.dumps(recent, ensure_ascii=False)) <= byte_ceiling:
                break
            for item in recent:
                if len(item["text"]) > length:
                    item["text"] = item["text"][:length]
                    item["partial_message"] = True
        while recent and token_upper_bound(json.dumps(recent, ensure_ascii=False)) > byte_ceiling:
            recent.pop()
        if not recent and packet.recent_coverage in {"recent_episodes", "indexed_episode"}:
            packet.recent_coverage = "packing_limit"
        if recent:
            packet.recent_conversation = recent
            packet.recent_reference_count = len(recent)
            budget.used += token_upper_bound(json.dumps(recent, ensure_ascii=False))
        elapsed = monotonic() - started
        budget.elapsed_seconds = already_elapsed + elapsed
        packet.elapsed_ms = elapsed * 1000
        logger.info("foreground_context", extra={"request_id": context.request_id, **packet.diagnostics()})
        return packet

    async def _recent(self, context, query, seconds, *, reference_ids=()):
        from app.services.history_lookup import search_archive
        deadline = Deadline.after(seconds)
        started = monotonic()
        def finish(rows, coverage):
            return rows, coverage, (monotonic() - started) * 1000

        try:
            async with asyncio.timeout(deadline.remaining()):
                async with self.store.reader(deadline) as db:
                    result = await search_archive(db, context, query, limit=4, recent=True, continuity=True,
                                                  reference_ids=reference_ids)
                    rows = result.get("results", [])
                    coverage = result.get("coverage") or ("matched" if rows else "no_match")
                    if rows or coverage in {"greeting", "private_conversation_withheld"} or reference_ids or not query_terms(query):
                        return finish(rows, coverage)
                    # Indexed source hits can suggest older sessions. Hydration enforces
                    # owner, audience, project and original input boundary again.
                    sessions = await self.store.matching_sessions(db, context.user_id, query_terms(query))
                    if not sessions:
                        return finish([], "no_match")
                    indexed = await search_archive(db, context, query, limit=4, recent=True, continuity=True,
                                                   session_ids=sessions)
                    rows = indexed.get("results", [])
                    return finish(rows, "indexed_episode" if rows else "no_match")
        except Exception as exc:
            logger.info("recent_context_unavailable", extra={"request_id": context.request_id,
                "error_type": type(exc).__name__})
            return finish([], "deadline" if isinstance(exc, TimeoutError) else "unavailable")

    async def restore_recent(self, context, ids):
        if context.triggered_by not in {"user", "n8n"} or not ids or "memory_lookup" not in (context.allowed_tools or ()):
            return []
        seconds = min(.5, self.prepare_timeout, max(0, 5 - context.memory_budget.elapsed_seconds))
        rows, _, _ = await self._recent(context, turn_query(context), seconds, reference_ids=ids)
        return rows

    async def lookup(self, context, query: str, *, source_ids=(), as_of=None) -> MemoryPacket:
        if context.memory_budget.lookup_batches >= 1:
            packet = MemoryPacket(request_id=context.request_id, user_id=context.user_id)
            packet.omissions = ["Routine memory lookup budget exhausted; use the evidence already supplied."]
            return packet
        context.memory_budget.lookup_batches += 1
        return await self._read(context, turn_query(context, query), automatic=False, source_ids=source_ids, as_of=as_of)

    async def _read(self, context, query, *, automatic, source_ids=(), as_of=None, reserved_bytes=0):
        started = monotonic()
        packet = MemoryPacket(request_id=context.request_id, user_id=context.user_id)
        seconds = (self.prepare_timeout if self._ready else max(self.prepare_timeout, 0.5)) if automatic else self.lookup_timeout
        seconds = min(seconds, max(0, 5 - context.memory_budget.elapsed_seconds))
        deadline = Deadline.after(seconds)
        stage = "store_read"
        try:
            async with asyncio.timeout(deadline.remaining()):
                terms = query_terms(query)
                if education_fact_query(query) and not source_ids:
                    terms = list(dict.fromkeys(["transcript", "transkript", "university", "universitesi", *terms]))
                result = await self.store.read(context, terms, source_ids=source_ids,
                                               include_recent=automatic, deadline=deadline)
            self._ready = True
            packet.publication_epoch = result["epoch"]
            packet.source_boundary = result["boundary"]
            stage = "selection"
            self._select(context, packet, result, query, automatic=automatic,
                         as_of=as_of, deadline=deadline, requested_sources=set(source_ids), reserved_bytes=reserved_bytes)
            stage = "epoch_check"
            async with asyncio.timeout(deadline.remaining()):
                final_epoch = await self.store.epoch(context, deadline=deadline)
            if final_epoch != packet.publication_epoch:
                self._discard(context, packet)
                packet.status = "unavailable"
                packet.coverage = "partial"
                packet.omissions.append("Memory changed during assembly; cached evidence withheld")
        except TimeoutError:
            self._discard(context, packet)
            packet.status = "deadline"
            packet.omissions.append("Memory deadline reached; no claim that absent evidence does not exist.")
        except Exception:
            self._discard(context, packet)
            if deadline.remaining() <= 0:
                packet.status = "deadline"
                packet.omissions.append("Local memory deadline reached")
            else:
                packet.status = "unavailable"
                packet.omissions.append("Local memory unavailable; do not invent recalled information.")
                logger.exception("memory_read_failed", extra={"request_id": context.request_id})
        packet.elapsed_ms = (monotonic() - started) * 1000
        context.memory_budget.elapsed_seconds += packet.elapsed_ms / 1000
        # Reserve covers metadata/header even on failures and empty results.
        logger.info("memory_read", extra={"request_id": context.request_id, "stage": stage, **packet.diagnostics()})
        return packet

    @staticmethod
    def _discard(context, packet):
        for handle in packet.evidence_handles:
            context.memory_budget.seen.discard(handle)
        packet.blocks.clear()
        packet.evidence_handles.clear()
        packet.unit_revisions.clear()
        packet.catalogue_records.clear()

    @staticmethod
    def _select_current_states(packet, states, *, budget, today, available, provisional_states=()):
        selected = 0
        state_count = 0
        for state in states:
            try:
                data = EvidenceSection.model_validate(state["section"])
            except (ValidationError, ValueError, TypeError):
                packet.omissions.append("Invalid evidence in current state")
                continue
            if (data.historical or (data.valid_from and data.valid_from > today)
                    or (data.valid_until and data.valid_until <= today)):
                continue
            handle = f"source:{data.source_id}:{data.start}:{data.end}"
            if handle in budget.seen:
                continue
            payload = state["payload"]
            phase, _ = classify_state_phase(payload, today)
            review_due = payload["review_on"] <= today.isoformat()
            summary = payload["summary"].encode("utf-8")
            brief = {
                "record_id": state["record_id"], "version": state["version"], "title": state["title"],
                "source": handle, "authority": "canonical",
                "status": payload["status"], "phase": phase,
                "salience": payload.get("salience", "normal"),
                "verified_on": payload["verified_on"], "review_on": payload["review_on"],
                "freshness": "review_due" if review_due else "confirmed",
                "summary": summary[:600].decode("utf-8", errors="ignore") + ("…" if len(summary) > 600 else ""),
            }
            for field in ("starts_on", "ends_on"):
                if payload.get(field):
                    brief[field] = payload[field]
            if payload.get("starts_on") and payload["starts_on"] > today.isoformat():
                brief["timing"] = "upcoming"
            if payload.get("question") and (review_due or phase == "ended_unconfirmed" or (phase in {"active_now", "upcoming_soon"} and not payload.get("ends_on"))):
                brief["question"] = str(payload["question"])[:240]
            # Keep the existing persisted packet shape so saved room runs can be restored across releases.
            block = json.dumps({"current_state": brief}, ensure_ascii=False, separators=(",", ":"))
            cost = token_upper_bound(block) + 1
            if selected + cost > min(2400, available):
                packet.omissions.append("Current state context reached its budget")
                continue
            packet.blocks.append(block)
            packet.evidence_handles.append(handle)
            packet.unit_revisions["iacon:" + state["record_id"]] = state["revision"]
            budget.seen.add(handle)
            selected += cost
            state_count += 1
            if state_count == 3:
                break
        provisional_ranked = []
        for item in provisional_states:
            payload = item.get("payload") or {}
            phase, phase_rank = classify_state_phase(payload, today)
            if phase == "planned_later":
                continue
            handle = f"provisional:{item['work_id']}"
            if handle in budget.seen:
                continue
            provisional_ranked.append((
                (phase_rank, _salience_rank(payload.get("salience")), item["work_id"]),
                item, handle, phase,
            ))
        provisional_ranked.sort(key=lambda entry: entry[0])
        provisional_count = 0
        for _, item, handle, phase in provisional_ranked:
            payload = item["payload"]
            summary = str(payload.get("summary", "")).encode("utf-8")
            brief = {
                "work_id": item["work_id"], "title": item["title"],
                "authority": item["authority"], "basis": item["basis"],
                "status": payload.get("status", "planned"), "phase": phase,
                "salience": payload.get("salience", "high"),
                "freshness": "unconfirmed",
                "summary": summary[:450].decode("utf-8", errors="ignore") + ("…" if len(summary) > 450 else ""),
            }
            for field in ("starts_on", "ends_on"):
                if payload.get(field):
                    brief[field] = payload[field]
            if payload.get("starts_on") and payload["starts_on"] > today.isoformat():
                brief["timing"] = "upcoming"
            if payload.get("question"):
                brief["question"] = str(payload["question"])[:240]
            if item.get("evidence_note"):
                brief["evidence_note"] = str(item["evidence_note"])[:200]
            block = json.dumps({"current_state": brief}, ensure_ascii=False, separators=(",", ":"))
            cost = token_upper_bound(block) + 1
            if selected + cost > min(3400, available):
                packet.omissions.append("Current state context reached its budget")
                continue
            packet.blocks.append(block)
            packet.evidence_handles.append(handle)
            budget.seen.add(handle)
            selected += cost
            provisional_count += 1
            if provisional_count == 2:
                break
        return selected

    def _select(self, context, packet, result, query, *, automatic, as_of, deadline, requested_sources=(), reserved_bytes=0):
        today = as_of or datetime.now(ZoneInfo(context.timezone)).date()
        terms = query_terms(query)
        education_query = education_fact_query(query) and not requested_sources
        family_query = bool(relations(query))
        names_only = family_query and bool(re.search(r"\b(names?|adlari|isimleri|adi)\b", fold(query))) and not bool(
            re.search(r"\b(where|when|why|happened|relationship|nerede|ne zaman|neden)\b", fold(query)))
        personal_fact = not terms and bool(re.search(r"\b(fact|facts|about me|hakkimda)\b", fold(query)))
        folded_query = " " + fold(query) + " "
        budget = context.memory_budget
        ceiling = min(budget.automatic_limit - reserved_bytes if automatic else budget.lookup_limit,
                      budget.turn_limit - budget.used)
        available = max(0, ceiling - 512)
        education_candidates = []
        candidates = []
        matched_aliases = {}
        future_spans = []
        name_specificity = max((len(set(terms).intersection(unit["title_words"]))
                                for unit in result["units"] if unit["kind"] == "person"), default=0)
        for unit in result["units"]:
            if deadline.remaining() <= 0:
                raise TimeoutError
            exact = [a for a in unit["aliases"] if
                     re.search(r"(?<!\w)" + re.escape(fold(a)) + r"(?!\w)", folded_query)]
            name_matches = set(terms).intersection(unit["title_words"])
            if unit["kind"] == "person" and name_matches and len(name_matches) == name_specificity:
                exact.append(unit["title"])
                for term in name_matches:
                    matched_aliases.setdefault(term, set()).add(unit["identity"])
            for alias in exact:
                matched_aliases.setdefault(fold(alias), set()).add(unit["identity"])
            score = (100 if unit["kind"] == "core" else 0) + (200 if exact else 0)
            dossier = unit["kind"] == "person" and any(data_is_identity(s["text"]) for s in unit["sections"])
            if dossier and exact:
                score += 220
            family_match = unit["key"] in result.get("relation_keys", ())
            if names_only and result.get("relation_keys") and unit["kind"] in {"person", "situation"} and not family_match and not exact:
                continue
            if family_match:
                score += 350
            if unit["key"].startswith("iacon:"):
                score += 150
            title_relevance = sum(term in unit["folded_title"] for term in terms)
            for section, body, section_dates in zip(unit["sections"], unit["section_search"], unit["section_dates"]):
                if deadline.remaining() <= 0:
                    raise TimeoutError
                try:
                    checked = EvidenceSection.model_validate(section)
                except (ValidationError, ValueError, TypeError):
                    packet.omissions.append(f"Invalid evidence in {unit['key']}: invalid source span or temporal interval withheld")
                    continue
                if checked.valid_from and checked.valid_from > today:
                    future_spans.append((section["source_id"], section["start"], section["end"]))
                relevance = sum(t in body for t in terms)
                if education_query and (match := education_section(unit, checked, today)):
                    field, rank, evidence = match
                    education_candidates.append((field, rank, evidence, unit["key"], unit["title"], unit["revision"]))
                if names_only and family_match and not data_is_identity(section["text"]):
                    continue
                identity_bonus = 50 if unit["kind"] == "person" and (exact or family_match) and data_is_identity(section["text"]) else 0
                try:
                    entity = json.loads(section["text"])
                except (ValueError, TypeError):
                    entity = None
                if (isinstance(entity, dict) and entity.get("kind") == "entity"
                        and isinstance(entity.get('payload'), dict) and not entity['payload'].get("description")):
                    # Preserve the ID/version as evidence, but do not let an
                    # empty identity index crowd out the person's native dossier.
                    identity_bonus -= 250
                if (exact and checked.attribution == 'canonical_record' and isinstance(entity, dict)
                        and unit['key'] == 'iacon:' + str(entity.get('record_id'))
                        and type(entity.get('version')) is int and len(packet.catalogue_records) < 3):
                    metadata = {'record_id':entity['record_id'], 'version':entity['version'],
                        'title':unit['title'], 'kind':entity.get('kind'),
                        'source':f"source:{checked.source_id}:{checked.start}:{checked.end}"}
                    if metadata not in packet.catalogue_records:
                        packet.catalogue_records.append(metadata)
                if family_match and data_is_identity(section["text"]):
                    identity_bonus += 150
                if unit["kind"] == "person" and (exact or family_match) and re.search(r"\b(stance|boundar\w*|reconciliation|sibling bond|relationship status)\b", body):
                    identity_bonus += 30
                dated = [day for day in section_dates if day <= today]
                recency_bonus = max(0, 20 - (today - max(dated)).days / 2) if dated else 0
                living = (automatic and not family_query and not personal_fact and unit["kind"] == "situation"
                          and not unit["key"].startswith("iacon:") and not section.get("historical"))
                if unit["kind"] != "core" and not living and not family_match and not exact and not relevance and not title_relevance:
                    continue
                candidates.append((score + min(relevance, 10) + 10 * title_relevance + identity_bonus + recency_bonus, unit["key"],
                    unit["title"], section, unit["revision"]))
        for alias, keys in matched_aliases.items():
            if len(keys) > 1:
                packet.ambiguities.append(f"Alias {alias!r} has multiple candidate identities; do not merge them.")
        known_message_ids = set(context.extra.get("history_message_ids", []))
        known_texts = {text for role, text in conversation_content(context) if role == "user"}
        for source in result["sources"]:
            if source["provenance"].get("iacon_record"):
                continue  # Derived snapshots require their dependency-checked serving unit.
            if deadline.remaining() <= 0:
                raise TimeoutError
            if source["id"] == context.memory_source_id or source["message_id"] in known_message_ids:
                continue
            if source.get("session_id") == context.session_id and source["text"] in known_texts:
                continue
            body = source["folded_text"]
            prior_body = fold((source["provenance"].get("prior_context") or {}).get("text", ""))
            context_match = any(t in prior_body for t in terms)
            relevant = sum(t in body or t in prior_body for t in terms)
            fresh_pending = source["role"] == "owner" and source.get("recent") and source["outcome"] not in {"accepted", "valid_empty", "source_only"}
            if not relevant and terms and source["id"] not in requested_sources and not fresh_pending:
                continue
            if not relevant and source["role"] != "owner" and source["id"] not in requested_sources:
                continue
            # Recent raw material remains owner-attributed, never a fresh canonical fact.
            score = (130 if relevant else 5) if fresh_pending else (60 if source["role"] == "owner" else 20)
            score += 20 * min(2, sum(t in body for t in result.get("entity_terms", [])))
            source_day = date.fromisoformat(source["created_at"][:10])
            score += max(0, 20 - max(0, (today - source_day).days) / 2)
            for span, span_search in zip(source["spans"], source["span_search"]):
                updates = {}
                for field in ("valid_from","valid_until"):
                    value = source["provenance"].get(field)
                    if value:
                        updates[field] = value[:10]
                if updates:
                    try:
                        span = EvidenceSection.model_validate({**span.model_dump(), **updates})
                    except (ValidationError, ValueError, TypeError):
                        packet.omissions.append(f"Invalid evidence source:{source['id']}: invalid temporal interval withheld; original retained")
                        continue
                if source["provenance"].get("historical"):
                    span = span.model_copy(update={"historical": True})
                if any(sid == span.source_id and span.start < stop and span.end > start
                       for sid, start, stop in future_spans):
                    continue
                if terms and not any(t in span_search for t in terms) and not context_match and source["id"] not in requested_sources and not fresh_pending:
                    continue
                candidates.append((score + relevant, f"raw:{source['id']}",
                                   f"{source['role']} source ({source['created_at']}; event date may differ; claim level {source['provenance'].get('observation_level','source')})",
                                   span.model_dump(mode="json"), 0))
        candidates.sort(key=lambda c: (-c[0], c[1], c[3]["start"]))
        selected_core = 0
        selected_focus = 0
        has_supporting = any(candidate[1].startswith("raw:") for candidate in candidates)
        core_keys = {u["key"] for u in result["units"] if u["kind"] == "core"}
        covered_spans = {}
        selected = 0

        def pack(data, title, *, supports=None):
            handle, block = section_block(data, title, today=today,
                prior_context=result.get("contexts", {}).get(data.source_id), supports=supports)
            cost = token_upper_bound(block) + 1
            return handle, block, cost

        def append(data, key, revision, handle, block):
            packet.blocks.append(block)
            packet.evidence_handles.append(handle)
            if revision:
                packet.unit_revisions[key] = revision
            covered_spans.setdefault(data.source_id, []).append((data.start, data.end))
            budget.seen.add(handle)

        # Give the best exact span for each requested field room before standing states.
        packed_fields = set()
        education_candidates.sort(key=lambda c: (c[0], -c[1], c[2].start))
        for field, _, data, key, title, revision in education_candidates:
            if field in packed_fields:
                continue
            handle, block, cost = pack(data, title, supports=field)
            if handle in budget.seen:
                packed_fields.add(field)
            elif selected + cost <= min(1800, available):
                append(data, key, revision, handle, block)
                packed_fields.add(field)
                selected += cost
        selected += self._select_current_states(
            packet, result.get("current_states", ()), budget=budget,
            today=today, available=available - selected,
            provisional_states=result.get("provisional_states", ()),
        )
        for _, key, title, section, revision in candidates:
            if deadline.remaining() <= 0:
                packet.status = "deadline"
                packet.omissions.append("Selection deadline reached")
                break
            try:
                data = EvidenceSection.model_validate(section)
            except (ValidationError, ValueError, TypeError):
                packet.omissions.append(f"Invalid evidence in {key}: invalid source span or temporal interval withheld")
                continue
            if re.fullmatch(r"\s*(?:#{1,6}\s+[^\n]+|\*\*[^*]+\*\*:?)\s*", data.text):
                continue
            if data.valid_from and data.valid_from > today:
                packet.omissions.append(f"{key}: not valid at requested date")
                continue
            handle, block, cost = pack(data, title)
            # Overlapping views of the same source are not independent corroboration.
            if handle in budget.seen or any(
                data.start < end and data.end > start
                for start, end in covered_spans.get(data.source_id, [])):
                continue
            if has_supporting and key not in core_keys and not key.startswith("raw:") and selected_focus + cost > min(3600, available * .60):
                packet.omissions.append(f"{key}: supporting-source share reserved")
                continue
            if key in core_keys and selected_core + cost > (min(900, available * .15) if has_supporting else 1800):
                packet.omissions.append(f"{key}: standing context share reached; focused evidence preserved")
                continue
            if selected + cost > available:
                packet.omissions.append(f"{key}: complete source section exceeds remaining budget")
                continue
            append(data, key, revision, handle, block)
            selected += cost
            if key in core_keys:
                selected_core += cost
            elif not key.startswith("raw:"):
                selected_focus += cost
        if result["candidate_limit_reached"]:
            packet.omissions.append("Indexed candidate limit reached; coverage may be incomplete")
        if packet.status != "deadline":
            packet.status = "ambiguous" if packet.ambiguities else "partial"
        # A bounded search can attest its evidence; it cannot attest exhaustive memory.
        packet.coverage = "bounded_candidates"
        # Check the serialized result, including metadata; no character/token mix.
        while packet.blocks and token_upper_bound(packet_json(packet)) + 64 > ceiling:
            removed = packet.evidence_handles.pop()
            block = json.loads(packet.blocks.pop())
            if (state := block.get("current_state")) and state.get("record_id"):
                packet.unit_revisions.pop("iacon:" + state["record_id"], None)
            budget.seen.discard(removed)
            packet.omissions.append("Serialized packet budget reached")
        while packet.catalogue_records and token_upper_bound(packet_json(packet)) + 64 > ceiling:
            packet.catalogue_records.pop()
        packet.token_upper_bound = token_upper_bound(packet_json(packet)) + 64
        budget.used += min(packet.token_upper_bound, max(0, budget.turn_limit - budget.used))


def packet_json(packet: MemoryPacket) -> str:
    important = [x for x in packet.omissions if x.startswith("Invalid evidence") or
                 any(t in x for t in ("unavailable", "deadline", "changed during assembly", "exhausted"))]
    limitations = list(dict.fromkeys([*important, *packet.omissions]))[:2]
    blocks = [json.loads(block) for block in packet.blocks]
    return json.dumps({"status": packet.status, "coverage": packet.coverage,
        "freshness": packet.freshness, "source_boundary": packet.source_boundary,
        "current_states": [block["current_state"] for block in blocks if "current_state" in block],
        "evidence": [block for block in blocks if "current_state" not in block],
        "catalogue_records": packet.catalogue_records,
        "omissions_count": len(packet.omissions), "limitations": [x[:160] for x in limitations],
        "ambiguities": packet.ambiguities[:4],
        "notice": "Bounded source evidence; absence is not proof of absence."},
        ensure_ascii=False, separators=(",", ":"))


def data_is_identity(text: str) -> bool:
    return bool(re.match(r"\s*(?:\*\*)?(?:Who|Identity)(?:\*\*)?\s*:", text, re.I))
