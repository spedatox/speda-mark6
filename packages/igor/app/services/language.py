# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one language the system speaks, and the backstop that keeps it there.

`settings.agent_language` is a single two-letter code that every surface reads:
the system prompt is built with its name in it (prompts/core/15_language.md),
synthesis and recognition derive their BCP-47 locales from it, and the clients
render their own chrome from the matching i18n dictionary. One value, because
the failure this replaces was three independent ones — a UI in Turkish, a
prompt with no language clause at all, and a `tts_locale` set by hand — quietly
disagreeing about which language a spoken conversation was in.

The prompt contract is the enforcement. What lives here is the check that runs
AFTER a reply exists, for the residue the contract misses: a stray "tamam", an
"okay", a day name, a unit carried straight out of a tool result. It is a
lexical detector on purpose, not a statistical language-ID model — those want a
paragraph to be confident, and the thing being caught is usually three words
inside forty correct ones, which is exactly the case a whole-text classifier
scores as "Turkish, 0.98" and waves through.

False positives are the real risk, so everything that is legitimately not in
the target language is excised before the scan: code spans and fenced blocks,
URLs, file paths, identifiers, numbers, and quoted material. What survives is
prose, and in prose a Turkish function word inside an English reply is a leak
with no innocent reading.
"""

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)


# ── The supported languages ──────────────────────────────────────────────────
# `name` is what gets interpolated into the prompt contract — the English name,
# because the prompt around it is English and a Turkish instruction embedded in
# an otherwise English one reads as an example rather than as the instruction.
# `tts`/`stt` are the BCP-47 locales the speech engines want.

LANGUAGES: dict[str, dict[str, str]] = {
    "tr": {"name": "Turkish", "endonym": "Türkçe", "tts": "tr-TR", "stt": "tr-TR"},
    "en": {"name": "English", "endonym": "English", "tts": "en-US", "stt": "en-US"},
}

DEFAULT_LANGUAGE = "en"


def normalize(code: str | None) -> str:
    """Reduce anything locale-shaped to a supported two-letter code.

    Accepts "tr", "tr-TR", "TR_tr", " en-GB " — the clients variously send a
    bare code, `navigator.language`, and an Android locale tag, and all three
    have to land on the same switch. An unsupported language falls back to
    English rather than to the raw code: a code with no entry here has no
    prompt name and no i18n dictionary, and a half-applied language is worse
    than a consistent wrong one.
    """
    base = (code or "").strip().lower().replace("_", "-").split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def current() -> str:
    """The language the system is set to, right now."""
    return normalize(settings.agent_language)


def name_of(code: str | None = None) -> str:
    """The English name of a language, for the prompt contract."""
    return LANGUAGES[normalize(code) if code is not None else current()]["name"]


def tts_locale(explicit: str | None = None) -> str:
    """The locale synthesis should speak in.

    An explicit per-call locale wins (one automation can legitimately be pinned
    to another language), then the hand-set `settings.tts_locale` override for
    a deployment that wants a regional variant the switch does not offer, then
    the master switch.
    """
    if explicit:
        return explicit
    return settings.tts_locale or LANGUAGES[current()]["tts"]


def stt_locale(explicit: str | None = None) -> str:
    """The locale the microphone should be decoded as. Same precedence as
    `tts_locale` — with the extra note that recognition has no multilingual
    model to degrade into: told the wrong language, Azure returns confident
    nonsense rather than a worse transcript, so this must never guess."""
    if explicit:
        return explicit
    return settings.stt_locale or LANGUAGES[current()]["stt"]


# ── Leak detection ───────────────────────────────────────────────────────────

# Function words. Chosen for being impossible to write by accident in the other
# language and for carrying no proper-noun risk: nothing here is also a ticker,
# a surname or a product. Deliberately short lists — every entry added is
# another chance at a false positive, and the leaks that actually happen are
# the commonest words in the language, not the rare ones.
_MARKERS: dict[str, tuple[str, ...]] = {
    "tr": (
        "ve", "bir", "için", "ile", "bu", "şu", "ama", "değil", "var", "yok",
        "olarak", "daha", "çok", "gibi", "sonra", "önce", "kadar", "ancak",
        "tamam", "evet", "hayır", "şey", "yani", "böyle", "şimdi",
        "günü", "saat", "hafta", "bugün", "yarın", "dün", "efendim", "merhaba",
    ),
    "en": (
        "the", "and", "with", "for", "from", "that", "this", "there", "which",
        "have", "has", "been", "will", "would", "should", "about", "into",
        "okay", "yes", "sure", "done", "here", "your", "you", "not", "but",
        "today", "tomorrow", "yesterday", "week", "hour", "sir",
    ),
}

# Letters that exist in Turkish and in no English word. Their presence in prose
# is a leak on its own — but only these three: ç, ö and ü ride in on German and
# French loanwords and on names, so they are not evidence of anything.
_TURKISH_LETTERS = re.compile(r"[ğışĞİŞ]")

# Everything stripped before the scan, because none of it is prose and all of
# it is legitimately in whatever language it was written in.
_EXCISE = re.compile(
    r"```.*?```"                            # fenced code
    r"|`[^`]*`"                             # inline code
    r"|<[^>]+>"                             # tags
    r"|https?://\S+"                        # urls
    r"|[A-Za-z]:\\[^\s]+"                   # windows paths
    r"|/[\w./-]{4,}"                        # posix paths
    r"|\"[^\"]*\""                          # quoted material — preserved on purpose
    r"|“[^”]*”"
    r"|\b\w+[_.]\w+\b"                      # identifiers: session_id, app.config
    r"|\d[\d.,:%-]*",                       # numbers, times, percentages
    re.DOTALL,
)

# Every alphabetic token, with the case intact and with a flag for whether it
# opens a sentence. Case is what separates a leak from a proper noun, and a
# sentence-initial capital carries no information either way — "Tamam," and
# "Kızılay" look identical to a case rule, so the two are graded differently
# below rather than lumped together.
_WORD = re.compile(
    r"(?P<open>(?:^|[.!?:;\n\r]|^\s*[-*•])\s*)?"
    r"(?<![\w'’])(?P<word>[A-Za-zÇĞİÖŞÜçğıöşü]+)(?![\w'’])",
    re.MULTILINE,
)


def detect_leak(text: str, target: str | None = None) -> list[str]:
    """Foreign fragments found in `text`, given the target language.

    Returns the offending words (deduplicated, in order of appearance) — empty
    when the text is clean. The caller decides what a non-empty list means;
    this function never logs and never rewrites, so it stays usable from the
    voice path, the push path, and a test alike.
    """
    lang = normalize(target) if target is not None else current()
    foreign = [other for other in LANGUAGES if other != lang]
    prose = _EXCISE.sub(" ", text or "")

    markers = {w for other in foreign for w in _MARKERS.get(other, ())}
    # The target language's own words are never evidence against it, even when
    # the two lists overlap on a shared loanword.
    markers -= set(_MARKERS.get(lang, ()))

    hits: list[str] = []
    seen: set[str] = set()

    # Two grades of candidate, because the two tests carry different risk:
    #
    #   The MARKER test is safe on any word, capitalised or not — every entry in
    #   the lists is a function word, and no proper noun is spelled "tamam" or
    #   "okay". So it runs on lowercase words and on sentence-openers, which is
    #   what catches the leak that matters most: the "Tamam," or "Okay," that
    #   starts the reply.
    #
    #   The LETTER test is not safe on a capitalised word: "Kızılay" and
    #   "Kadıköy" are proper nouns the contract explicitly permits, and at the
    #   start of a sentence they are indistinguishable from a leaked Turkish
    #   word. So it runs on lowercase words only, and a leak that manages to be
    #   both sentence-initial and absent from the marker lists is let through —
    #   the price of never crying wolf over a place name.
    for match in _WORD.finditer(prose):
        word = match.group("word")
        lowered = word.lower()
        sentence_open = match.group("open") is not None
        if lowered in seen:
            continue
        leak = lowered in markers and (word.islower() or sentence_open)
        leak = leak or (lang == "en" and word.islower() and _TURKISH_LETTERS.search(word))
        if leak:
            hits.append(lowered)
            seen.add(lowered)

    return hits


def leaked(text: str, target: str | None = None) -> list[str]:
    """`detect_leak`, but honouring the enforcement switch and the tolerance.

    Returns the hits only when there are enough of them to count as a leak;
    below the threshold, or with enforcement off, returns an empty list. This
    is the function call sites should use — `detect_leak` is the raw one.
    """
    if not settings.language_enforcement:
        return []
    hits = detect_leak(text, target)
    return hits if len(hits) >= max(1, settings.language_leak_tolerance) else []


_REPAIR_SYSTEM = (
    "You are a language corrector. The text you are given must be entirely in "
    "{name}, and it is not — some words or sentences are in another language.\n\n"
    "Rewrite it so that every word is {name}. Change NOTHING else: not the "
    "meaning, not the facts, not the numbers, not the tone, not the formatting, "
    "not the line breaks. Do not add anything, do not remove anything, do not "
    "explain, and do not comment on what you changed.\n\n"
    "Leave proper nouns, code, file paths, identifiers, commands and quoted "
    "material exactly as they are — those are not translated.\n\n"
    "Return only the corrected text."
)


def _response_text(response) -> str:
    """Read the text out of a provider response, whichever shape it came in."""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            piece = getattr(block, "text", None)
            if piece is None and isinstance(block, dict):
                piece = block.get("text")
            if piece:
                parts.append(str(piece))
        return "".join(parts)
    return ""


async def repair(text: str, model: str, target: str | None = None) -> str:
    """Rewrite `text` into the target language with one cheap-model pass.

    Best-effort and silent on failure, on the same contract as the speech
    sanitizer it runs beside: a rate limit, an outage or an implausibly long
    reply returns the input UNCHANGED. A leak that survives is a worse answer;
    a repair pass that throws would be no answer at all, and the reply the
    owner is waiting on is not the place to take that trade.
    """
    if not model or not text:
        return text
    lang = normalize(target) if target is not None else current()
    from app.services.llm_client import LLMClient

    try:
        client = LLMClient()
        response = await client.create_message(
            model=model,
            system=_REPAIR_SYSTEM.format(name=LANGUAGES[lang]["name"]),
            messages=[{"role": "user", "content": text}],
            max_tokens=2000,
            reasoning_effort="minimal",
        )
    except Exception as exc:  # noqa: BLE001 — a repair failure must not lose the reply
        logger.warning("language_repair_failed", extra={"error": str(exc)})
        return text

    out = _response_text(response).strip()
    if not out or len(out) > len(text) * 2 + 200:
        logger.warning("language_repair_unusable", extra={"chars": len(out)})
        return text
    return out


async def enforce(text: str, model: str = "", target: str | None = None) -> str:
    """Check `text` against the chosen language and, where allowed, fix it.

    The one call-site helper: detect, log what leaked (with the fragments, so a
    recurring miss can be turned into a prompt fix rather than a permanent
    rewrite bill), and rewrite only when `language_repair` is on and a model
    was supplied. Callers on a path where the owner has already SEEN the text
    pass no model — they get the logging and their text back untouched, which
    is the honest outcome there.
    """
    hits = leaked(text, target)
    if not hits:
        return text
    lang = normalize(target) if target is not None else current()
    logger.warning(
        "language_leak",
        extra={"language": lang, "fragments": hits[:8], "count": len(hits)},
    )
    if settings.language_repair and model:
        return await repair(text, model, lang)
    return text
