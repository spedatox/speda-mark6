"""Local query routing over attributed conversation content, never prompt authority."""
from datetime import date, timedelta
import json
import re

from app.services.memory_store import fold, source_text


def query_terms(value: str) -> list[str]:
    stop = set("the a an and or to of in on is it its this that my me we our you your her him "
               "they them their theri he she what who how about just know meant right current relationship "
               "perfect almighty all mighty great sir please remember recall context hey hello hi selam merhaba "
               "owner owners personal specific fact facts tell names name not merely from find grounded "
               "remembered conversations transient timezone said have has are was were for "
               "bir bu su ve ile ben benim sen onun o ne nasil mi mu icin adi adlari isim isimleri".split())
    return list(dict.fromkeys(t for t in re.findall(r"\w+", fold(value))
                             if len(t) >= 2 and t not in stop))[:20]


def conversation_content(context):
    """Unwrap only server-rendered room envelopes; retain speaker attribution."""
    result = []
    for message in context.conversation_history:
        text = source_text(message.get("content", ""))
        role = message.get("role")
        if context.room_id:
            owner = re.match(r"^\[Owner message, room event \d+\]\n", text)
            if owner:
                text = re.split(r'\n(?:Quoted (?:attachment sources|Telegram reply) \(|\{"reply_reference":)',
                                text[owner.end():], maxsplit=1)[0]
            else:
                try:
                    envelope = json.loads(text)
                except (ValueError, TypeError):
                    envelope = None
                if isinstance(envelope, dict):
                    if "attributed_contribution" in envelope and isinstance(envelope.get("text"), str):
                        text, role = envelope["text"], "assistant"
                    elif "recorded_room_decisions" in envelope:
                        continue
                    elif "collaboration_request" in envelope or "automation_event" in envelope:
                        text = source_text(envelope.get("collaboration_request", envelope.get("automation_event", "")))
                        role = "query"  # A retrieval cue, never an owner assertion.
        result.append((role, text))
    return result


def withdrawal_reply(context):
    """Recognize a standalone withdrawal on a fresh, direct owner turn."""
    if (context.triggered_by != "user" or context.parent_step_id or context.model_call_index
            or context.system_prompt or context.task_role != "interactive"):
        return None
    for msg in reversed(context.conversation_history):
        raw = source_text(msg.get("content", ""))
        if context.room_id:
            if raw.startswith('{"recorded_room_decisions":'):
                continue
            match = re.fullmatch(r"\[Owner message, room event \d+\]\n(.+)", raw)
            if not match:
                return None
            raw = match[1]
        if msg.get("role") != "user":
            return None
        phrase = fold(raw.strip())
        if re.fullmatch(r"(?:(?:oh )?for god['’]?s sake[.! ,]+)?(?:please )?(?:forget i asked|never mind|nevermind|drop it)[.! ]*", phrase):
            return "Understood."
        if re.fullmatch(r"(?:bos ver|bosver|vazgectim)[.! ]*", phrase):
            return "Tamam."
        return None


def planning_window(query, today):
    """Inclusive owner-local dates for common personal planning questions."""
    text = fold(query.partition("\n")[0])
    monday = today - timedelta(days=today.weekday())
    explicit_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if 1 <= len(explicit_dates) <= 2:
        try:
            first, last = date.fromisoformat(explicit_dates[0]), date.fromisoformat(explicit_dates[-1])
            if today <= first <= last:
                return first, last
        except ValueError:
            pass
    if re.search(r"\b(next weekend|(?:gelecek|onumuzdeki) hafta sonu)\b", text):
        return monday + timedelta(days=12), monday + timedelta(days=13)
    if re.search(r"\b(next week|gelecek hafta|onumuzdeki hafta)\b", text):
        return monday + timedelta(days=7), monday + timedelta(days=13)
    if re.search(r"\b(this week|bu hafta)\b", text) and not re.search(r"weekend|hafta sonu", text):
        return today, monday + timedelta(days=6)
    if re.search(r"\b(this weekend|bu hafta sonu)\b", text):
        return max(today, monday + timedelta(days=5)), monday + timedelta(days=6)
    if re.search(r"\b(tomorrow|yarin)\b", text):
        return today + timedelta(days=1), today + timedelta(days=1)
    return today, today


def referential(value):
    """Detect elliptical fragments, grammatical anaphora/deixis, or definite bridging references.

    Linguistic criteria (zero domain vocabulary):
    1. Ultra-short elliptical fragments (<=3 words: 'names?', 'adları?', 'when?').
    2. Explicit anaphoric/deictic pronouns within a short turn (<160 chars).
    3. Short turns (<=6 words) using a definite bridging article ('the ...') without
       a self-contained first-person anchor ('I'/'my'/'me'), e.g. 'What would the name be?'.
    """
    folded = fold(strip_reply_quote_prefix(value))
    words = re.findall(r"\w+", folded)
    if len(words) <= 3:
        return True
    if len(folded) < 160 and re.search(
        r"\b(her|him|their|theri|they|them|that|this|these|those|there|meant|she|he|it|its|"
        r"onun|onlarin|bunu|sunu|orasi|oraya)\b",
        folded,
    ):
        return True
    if len(words) <= 6 and re.search(r"\bthe\b", folded) and not re.search(r"\b(i|my|me|mine)\b", folded):
        return True
    return False


def strip_reply_quote_prefix(text: str) -> str:
    """Strip client-prepended 'Reply context (selected chat excerpts):' quote blocks so line 1 is the owner's actual turn."""
    stripped = (text or "").lstrip()
    if not stripped.lower().startswith("reply context"):
        return text
    lines = stripped.splitlines()
    idx = 1
    while idx < len(lines) and (not lines[idx].strip() or lines[idx].lstrip().startswith(">")):
        idx += 1
    body = "\n".join(lines[idx:]).strip()
    return body or text


def _select_referent_sentence(prior: str, query: str) -> str:
    """Select the most informative referent sentence from a multi-sentence prior turn."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prior.strip()) if s.strip()]
    if len(sentences) <= 1:
        return prior
    # Drop trivial <=3-word discourse fillers (e.g. 'My mistake.', 'Sure.', 'Tamam.')
    substantive = [s for s in sentences if len(re.findall(r"\w+", fold(s))) > 3]
    if not substantive:
        return sentences[0]
    if len(substantive) == 1:
        return substantive[0]
    # Rank substantive sentences semantically against the current turn (and prefer current-tense statements).
    try:
        import numpy as np
        from app.services.episodic_cortex import _cached_passage_vec, _cached_query_vec, _intent_prototypes
        qv = _cached_query_vec(query)
        protos = _intent_prototypes()
        if qv is not None:
            best_sent = substantive[0]
            best_score = -1e9
            for idx, sent in enumerate(substantive):
                sv = _cached_passage_vec(sent)
                if sv is None:
                    continue
                sim = float(np.dot(qv, sv))
                # Penalize past-tense correction clauses relative to present-tense statements.
                past_pen = float(np.dot(sv, protos["sec_past"])) * 0.35 if protos else 0.0
                # Slight preference for earlier core answer sentences over trailing meta-explanations.
                pos_bonus = 0.03 * max(0, 2 - idx)
                score = sim - past_pen + pos_bonus
                if score > best_score:
                    best_score = score
                    best_sent = sent
            return best_sent
    except Exception:
        pass
    return substantive[0]


def turn_query(context, query=None) -> str:
    if query is None and context.triggered_by == "n8n":
        payload = context.trigger_payload or {}
        data = payload.get("event_data")
        if isinstance(data, dict) and payload.get("type") == "mail_watch":
            messages = data.get("messages", [])
            if messages:
                return "\n".join(
                    str(message.get(field, ""))
                    for field in ("subject", "from", "snippet", "body", "body_excerpt")
                    for message in messages
                )[:6000]
    history = conversation_content(context)
    latest = next((i for i in range(len(history)-1, -1, -1) if history[i][0] in {"user", "query"}), None)
    value = query if query is not None else (history[latest][1] if latest is not None else "")
    if latest is not None and referential(value):
        # Borrow nearby user and assistant context when the turn is an elliptical fragment
        # or contains explicit grammatical anaphora/deixis.
        borrowed = []
        for _, prior in reversed(history[max(0, latest-6):latest]):
            if not query_terms(prior):
                continue
            if referential(prior) and len(re.findall(r"\w+", fold(prior))) <= 3:
                continue
            referent = _select_referent_sentence(prior[:1200], value)
            borrowed.append(referent)
            if len(borrowed) >= 2:
                break
        if borrowed:
            value += "\n" + "\n".join(borrowed)
    value += "\n" + str(context.extra.get("attachment_text", ""))[:2000]
    return value[:6000]


def identity_text(text):
    match = re.search(r"(?m)^\s*(?:\*\*)?(?:Who|Identity|Kim)(?:\*\*)?\s*:\s*(.*)", text, re.I | re.S)
    if match:
        return re.split(r"\n|(?<=[.!?])\s", match[1], maxsplit=1)[0]
    # Plain identity statements are also used by native serving records.
    return re.split(r"\n|(?<=[.!?])\s", text.strip(), maxsplit=1)[0][:500]


_MODIFIER_PREFIX = re.compile(
    r"^(?:(?:younger|older|elder|little|big|only|youngest|eldest|late|half|step|current|former|ex|"
    r"kucuk|buyuk|en\s+kucuk|en\s+buyuk)\s+)+",
    re.I,
)
# Structural 2-hop possessive guard (zero domain vocabulary):
# rejects English "X's Y's Z" and Turkish nested genitives "...inin / ...unun / ...sının / ...sunun ..."
_EN_SECOND_HOP = re.compile(r"^\w+(?:\s+\w+)?['’]s\b")
_TR_SECOND_HOP = re.compile(r"^(?:\w+\s+){0,2}\w+(?:inin|unun|sinin|sunun|imin|umun)\b")


def extract_owner_relation_phrases(sections, owner_name) -> list[str]:
    """Structurally extract direct owner-anchored relationship phrases from identity clauses.

    Preserves deterministic syntax boundaries (skips event bullets, requires owner anchor,
    rejects 'of the owner's ...' and 2-hop possessive chains like 'owner's sister's friend')
    and keeps original Unicode diacritics for accurate multilingual neural embeddings.
    """
    names = fold(owner_name).split()
    anchors = [r"\b(?:the )?owner['’]s\b", r"\byour\b"]
    if names and names[0] != "owner":
        # Both full and familiar given-name forms occur in bilingual records.
        anchors.append(r"\b" + re.escape(names[0]) + r"(?:\s+\w+){0,3}['’](?:s|in|un|nin|nun)\b")
    phrases: list[str] = []
    seen: set[str] = set()
    for section in sections:
        text = section.get("text", "")
        if re.match(r"\s*(?:[-*] |\[\d{4}-)", text):
            continue
        raw_id = identity_text(text)
        folded_id = fold(raw_id)
        for anchor in anchors:
            match = re.search(anchor, folded_id)
            if not match:
                continue
            if re.search(r"\bof\s+(?:the\s+)?$", folded_id[:match.start()]):
                continue
            # Slice from raw_id at the same character offset (fold preserves length for Latin/Turkish).
            tail_raw = raw_id[match.end():].lstrip() if len(raw_id) == len(folded_id) else folded_id[match.end():].lstrip()
            tail_folded = folded_id[match.end():].lstrip()
            mod_match = _MODIFIER_PREFIX.match(tail_folded)
            if mod_match:
                tail_raw = tail_raw[mod_match.end():].lstrip()
                tail_folded = tail_folded[mod_match.end():].lstrip()
            if not tail_folded:
                continue
            if _EN_SECOND_HOP.match(tail_folded) or _TR_SECOND_HOP.match(tail_folded):
                continue
            # Preserve the clean head relation clause (before sentence/semicolon/comma breaks).
            clause = re.split(r"[.;,\n]", tail_raw, maxsplit=1)[0].strip()
            clause_key = fold(clause)
            if len(clause_key) >= 2 and clause_key not in seen:
                seen.add(clause_key)
                phrases.append(clause)
    return phrases
