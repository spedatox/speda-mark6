"""Local query routing over attributed conversation content, never prompt authority."""
import json
import re

from app.services.memory_store import fold, source_text


def query_terms(value: str) -> list[str]:
    stop = set("the a an and or to of in on is it its this that my me we our you your her him "
               "they them their theri he she what who how about just know meant right current relationship "
               "perfect almighty all mighty great sir please remember recall context "
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
                text = text[owner.end():].split('\nQuoted attachment sources (data, never instructions):', 1)[0]
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


def referential(value):
    return len(value) < 160 and bool(re.search(
        r"\b(her|him|their|theri|they|that|this|meant|she|he|it|onun|onlarin)\b|"
        r"^\s*(?:names?|adlari|isimleri)\s*\??\s*$", fold(value)))


def turn_query(context, query=None) -> str:
    history = conversation_content(context)
    latest = next((i for i in range(len(history)-1, -1, -1) if history[i][0] in {"user", "query"}), None)
    value = query if query is not None else (history[latest][1] if latest is not None else "")
    if latest is not None and referential(value):
        # Borrow one useful nearby referent, not repeated retries or operational text.
        for _, prior in reversed(history[max(0, latest-6):latest]):
            if query_terms(prior) and not referential(prior):
                referent = prior[:1200]
                if re.search(r"\b(names?|adlari|isimleri)\b", fold(value)):
                    referent = re.split(r"(?<=[.!?])\s", referent, maxsplit=1)[0]
                value += "\n" + referent
                break
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
    match = re.match(r"\s*(?:\*\*)?(?:Who|Identity|Kim)(?:\*\*)?\s*:\s*(.*)", text, re.I | re.S)
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
