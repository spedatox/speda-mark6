"""One provider-free serving path for initial context and deliberate lookup."""
import asyncio
from datetime import date, datetime
import json
import logging
import re
from time import monotonic
from zoneinfo import ZoneInfo
from pydantic import ValidationError

from app.schemas.memory_runtime import Deadline, EvidenceSection, MemoryPacket, token_upper_bound
from app.services.memory_store import fold
from app.services.memory_query import query_terms, turn_query, relations, conversation_content

logger = logging.getLogger(__name__)


def section_block(section, title, *, today: date, prior_context=None) -> tuple[str, str]:
    data = section if isinstance(section, EvidenceSection) else EvidenceSection.model_validate(section)
    handle = f"source:{data.source_id}:{data.start}:{data.end}"
    warnings = []
    if data.fragment:
        warnings.append("Source fragment; surrounding context/qualifiers may be omitted")
    if data.historical or (data.valid_until and data.valid_until <= today):
        warnings.append("historical; not evidence of current status")
    if data.review_on and data.review_on <= today:
        warnings.append("review overdue; current status unverified")
    return handle, json.dumps({"source": handle, "topic": title,
        "attribution": data.attribution, "text": data.text,
        "valid_from": str(data.valid_from) if data.valid_from else None,
        "valid_until": str(data.valid_until) if data.valid_until else None,
        "prior_context_only": prior_context,
        "warnings": warnings}, ensure_ascii=False, separators=(",", ":"))


class MemoryRuntime:
    def __init__(self, store, *, prepare_timeout=0.25, lookup_timeout=2.5):
        self.store = store
        self.prepare_timeout = prepare_timeout
        self.lookup_timeout = lookup_timeout
        self._ready = False

    async def prepare(self, context) -> MemoryPacket:
        return await self._read(context, turn_query(context), automatic=True)

    async def lookup(self, context, query: str, *, source_ids=(), as_of=None) -> MemoryPacket:
        if context.memory_budget.lookup_batches >= 1:
            packet = MemoryPacket(request_id=context.request_id, user_id=context.user_id)
            packet.omissions = ["Routine memory lookup budget exhausted; use the evidence already supplied."]
            return packet
        context.memory_budget.lookup_batches += 1
        return await self._read(context, turn_query(context, query), automatic=False, source_ids=source_ids, as_of=as_of)

    async def _read(self, context, query, *, automatic, source_ids=(), as_of=None):
        started = monotonic()
        packet = MemoryPacket(request_id=context.request_id, user_id=context.user_id)
        seconds = (self.prepare_timeout if self._ready else max(self.prepare_timeout, 0.5)) if automatic else self.lookup_timeout
        seconds = min(seconds, max(0, 5 - context.memory_budget.elapsed_seconds))
        deadline = Deadline.after(seconds)
        try:
            async with asyncio.timeout(deadline.remaining()):
                result = await self.store.read(context, query_terms(query), source_ids=source_ids,
                                               include_recent=automatic, deadline=deadline)
            self._ready = True
            packet.publication_epoch = result["epoch"]
            packet.source_boundary = result["boundary"]
            self._select(context, packet, result, query, automatic=automatic,
                         as_of=as_of, deadline=deadline, requested_sources=set(source_ids))
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
        logger.info("memory_read", extra={"request_id": context.request_id, **packet.diagnostics()})
        return packet

    @staticmethod
    def _discard(context, packet):
        for handle in packet.evidence_handles:
            context.memory_budget.seen.discard(handle)
        packet.blocks.clear()
        packet.evidence_handles.clear()
        packet.unit_revisions.clear()

    def _select(self, context, packet, result, query, *, automatic, as_of, deadline, requested_sources=()):
        today = as_of or datetime.now(ZoneInfo(context.timezone)).date()
        terms = query_terms(query)
        family_query = bool(relations(query))
        names_only = family_query and bool(re.search(r"\b(names?|adlari|isimleri|adi)\b", fold(query))) and not bool(
            re.search(r"\b(where|when|why|happened|relationship|nerede|ne zaman|neden)\b", fold(query)))
        personal_fact = not terms and bool(re.search(r"\b(fact|facts|about me|hakkimda)\b", fold(query)))
        folded_query = " " + fold(query) + " "
        budget = context.memory_budget
        ceiling = min(budget.automatic_limit if automatic else budget.lookup_limit,
                      budget.turn_limit - budget.used)
        available = max(0, ceiling - 512)
        selected = 0
        state_count = 0
        for state in result.get("current_states", ()):
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
            summary = payload["summary"].encode("utf-8")
            brief = {
                "record_id": state["record_id"], "version": state["version"], "title": state["title"],
                "source": handle, "status": payload["status"], "salience": payload.get("salience", "normal"),
                "verified_on": payload["verified_on"], "review_on": payload["review_on"],
                "freshness": "review_due" if payload["review_on"] <= today.isoformat() else "confirmed",
                "summary": summary[:600].decode("utf-8", errors="ignore") + ("…" if len(summary) > 600 else ""),
            }
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
        candidates = []
        matched_aliases = {}
        future_spans = []
        for unit in result["units"]:
            if deadline.remaining() <= 0:
                raise TimeoutError
            exact = [a for a in unit["aliases"] if
                     re.search(r"(?<!\w)" + re.escape(fold(a)) + r"(?!\w)", folded_query)]
            if unit["kind"] == "person" and any(
                t in unit["title_words"] for t in terms):
                exact.append(unit["title"])
                for term in set(terms).intersection(unit["title_words"]):
                    matched_aliases.setdefault(term, set()).add(unit["identity"])
            for alias in exact:
                matched_aliases.setdefault(fold(alias), set()).add(unit["identity"])
            score = (100 if unit["kind"] == "core" else 0) + (200 if exact else 0)
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
                if names_only and family_match and not data_is_identity(section["text"]):
                    continue
                identity_bonus = 50 if unit["kind"] == "person" and data_is_identity(section["text"]) else 0
                if family_match and data_is_identity(section["text"]):
                    identity_bonus += 150
                if unit["kind"] == "person" and re.search(r"\b(stance|boundar\w*|reconciliation|sibling bond|relationship status)\b", body):
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
        local_seen = set()
        covered_spans = {}
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
            handle, block = section_block(data, title, today=today,
                prior_context=result.get("contexts", {}).get(data.source_id))
            # Overlapping views of the same source are not independent corroboration.
            identity = (data.source_id, data.start, data.end)
            if handle in budget.seen or identity in local_seen or any(
                data.start < end and data.end > start
                for start, end in covered_spans.get(data.source_id, [])):
                continue
            cost = token_upper_bound(block) + 1
            if has_supporting and key not in core_keys and not key.startswith("raw:") and selected_focus + cost > min(3600, available * .60):
                packet.omissions.append(f"{key}: supporting-source share reserved")
                continue
            if key in core_keys and selected_core + cost > (min(900, available * .15) if has_supporting else 1800):
                packet.omissions.append(f"{key}: standing context share reached; focused evidence preserved")
                continue
            if selected + cost > available:
                packet.omissions.append(f"{key}: complete source section exceeds remaining budget")
                continue
            packet.blocks.append(block)
            packet.evidence_handles.append(handle)
            if revision:
                packet.unit_revisions[key] = revision
            local_seen.add(identity)
            covered_spans.setdefault(data.source_id, []).append((data.start, data.end))
            budget.seen.add(handle)
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
            if state := block.get("current_state"):
                packet.unit_revisions.pop("iacon:" + state["record_id"], None)
            budget.seen.discard(removed)
            packet.omissions.append("Serialized packet budget reached")
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
        "omissions_count": len(packet.omissions), "limitations": [x[:160] for x in limitations],
        "ambiguities": packet.ambiguities[:4],
        "notice": "Bounded source evidence; absence is not proof of absence."},
        ensure_ascii=False, separators=(",", ":"))


def data_is_identity(text: str) -> bool:
    return bool(re.match(r"\s*(?:\*\*)?(?:Who|Identity)(?:\*\*)?\s*:", text, re.I))
