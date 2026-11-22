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
    folded = fold(value)
    return len(value) < 180 and (
        bool(re.search(
            r"\b(her|him|their|theri|they|that|this|meant|she|he|it|onun|onlarin|"
            r"destination|still|way|there|bus|exam|result|score|time|changed)\b|"
            r"^\s*(?:names?|adlari|isimleri)\s*\??\s*$",
            folded,
        ))
        or len(re.findall(r"\w+", folded)) <= 5
    )


_NAME_QUESTION = re.compile(r"\b(?:names?|adlari|isimleri|adi)\b")
SCHOOL_TERM = re.compile(r"\b(?:universit\w*|college|school|okul\w*)\b")
_OWNER_SCHOOL = re.compile(
    r"\b(?:my|your|owner(?:['’]s)?)\b(?:\W+\w+){0,3}\W+(?:universit\w*|college|school)\b"
    r"|\b(?:universitem\w*|okulum\w*)\b"
)
_PAST_SCHOOL = re.compile(r"\b(?:old|former|previous|used to|graduated|once|eski|onceki|mezun)\b")


def education_fact_query(query):
    """Recognize a current enrollment or program question from the owner turn."""
    primary, _, borrowed = fold(query).partition("\n")
    if _PAST_SCHOOL.search(primary):
        return False
    if re.search(r"\b(?:where (?:do i study|am i enrolled)|what (?:do i study|am i studying)|"
                 r"(?:what|which) is my (?:major|degree|program|school)|my major|"
                 r"ne okuyorum|bolumum|hangi bolum\w*)\b", primary):
        return True
    if SCHOOL_TERM.search(primary):
        owner_subject = re.search(r"\b(?:i|owner|okuyorum|my (?:current )?(?:university|college|school))\b", primary)
        if owner_subject and re.search(
            r"\b(?:which|what|hangi|name|adi|attend|enroll\w*|okuyor\w*)\b", primary
        ):
            return True
        if re.fullmatch(r"(?:my (?:university|college|school)|universitem\w*|okulum\w*)", primary.strip(" ?.?!")):
            return True
    referent = borrowed.partition("\n")[0]
    return bool(_NAME_QUESTION.search(primary) and _OWNER_SCHOOL.search(referent)
                and not _PAST_SCHOOL.search(referent))


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
    if latest is not None and referential(value) and not education_fact_query(value):
        # Borrow nearby user and assistant context when the turn is short, referential, or deictic
        # (e.g. "hey speda" -> "Im on my way still" -> "how far am I from the destination",
        # or "You have a younger brother and a sister." -> "theri names?").
        name_follow_up = bool(_NAME_QUESTION.search(fold(value)))
        borrowed = []
        for _, prior in reversed(history[max(0, latest-6):latest]):
            if not query_terms(prior):
                continue
            referent = prior[:1200]
            if name_follow_up:
                sentences = re.split(r"(?<=[.!?])\s+", referent)
                first = sentences[0]
                school = None
                if not relations(first):
                    school = next((sentence for sentence in sentences
                                   if _OWNER_SCHOOL.search(fold(sentence))
                                   and not _PAST_SCHOOL.search(fold(sentence))), None)
                if not school and referential(prior) and not education_fact_query(prior):
                    continue
                referent = school or first
            borrowed.append(referent)
            if name_follow_up or len(borrowed) >= 2:
                break
        if borrowed:
            value += "\n" + "\n".join(borrowed)
    value += "\n" + str(context.extra.get("attachment_text", ""))[:2000]
    return value[:6000]


_RELATIONS = {
    "sister": r"\bsisters?\b|\bkiz kardes\w*",
    "brother": r"\bbrothers?\b|\berkek kardes\w*|\babim\b|\babisi\b",
    "sibling": r"\bsiblings?\b|\bkardes\w*",
    "mother": r"\bmother\b|\bmom\b|\banne\w*",
    "father": r"\bfather\b|\bdad\b|\bbaba\w*",
    "parent": r"\bparents?\b|\bebeveyn\w*",
    "spouse": r"\bspouse\b|\bwife\b|\bhusband\b|\besi\b|\besim\b",
}


def relations(text):
    found = {kind for kind, pattern in _RELATIONS.items() if re.search(pattern, fold(text))}
    if found.intersection({"sister", "brother"}):
        found.add("sibling")
    if found.intersection({"mother", "father"}):
        found.add("parent")
    return found


def identity_text(text):
    match = re.search(r"(?m)^\s*(?:\*\*)?(?:Who|Identity|Kim)(?:\*\*)?\s*:\s*(.*)", text, re.I | re.S)
    if match:
        return re.split(r"\n|(?<=[.!?])\s", match[1], maxsplit=1)[0]
    # Plain identity statements are also used by native serving records.
    return re.split(r"\n|(?<=[.!?])\s", text.strip(), maxsplit=1)[0][:500]


def owner_relations(sections, owner_name):
    """Route by explicit identity assertions, never a family mention in an event."""
    names = fold(owner_name).split()
    anchors = [r"\b(?:the )?owner['’]s\b", r"\byour\b"]
    if names and names[0] != "owner":
        # Both full and familiar given-name forms occur in the bilingual records.
        anchors.append(r"\b" + re.escape(names[0]) + r"(?:\s+\w+){0,3}['’](?:s|in|un|nin|nun)\b")
    result = set()
    for section in sections:
        text = section.get("text", "")
        if re.match(r"\s*(?:[-*] |\[\d{4}-)", text):
            continue
        identity = fold(identity_text(text))
        for anchor in anchors:
            match = re.search(anchor, identity)
            if not match:
                continue
            if re.search(r"\bof\s+(?:the\s+)?$", identity[:match.start()]):
                continue
            tail = identity[match.end():].lstrip()
            tail = re.sub(r"^(?:(?:younger|older|elder|little|big|only|youngest|eldest|late|half|step)\s+)+", "", tail)
            # Only the directly asserted relationship counts: an owner's sister's
            # friend is not the owner's sister. Turkish genitives have the same trap.
            if re.match(r"(?:sister|brother|sibling|mother|father|parent|wife|husband|spouse)['’]s\b", tail):
                continue
            for kind, pattern in _RELATIONS.items():
                if re.match(pattern, tail):
                    result.add(kind)
            if re.match(r"(?:kiz |erkek )?kardes(?:inin|imin)\b|(?:anne|baba)(?:sinin|min)\b", tail):
                result.difference_update({"sister", "brother", "sibling", "mother", "father", "parent"})
            if result.intersection({"sister", "brother"}):
                result.add("sibling")
            if result.intersection({"mother", "father"}):
                result.add("parent")
    return result
