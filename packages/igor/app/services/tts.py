# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Text-to-speech synthesis — Azure Speech.

Voice mode speaks every reply, so synthesis is a TRANSPORT concern: the router
calls it on text the orchestrator already produced. It is deliberately NOT
driven by the model deciding to call a tool. (The `text_to_speech` skill still
exists for "read this out and hand me the file" and is backed by this module.)

Two things here are load-bearing and easy to get wrong:

1. The text is MODEL-AUTHORED and is embedded in an SSML (XML) document. Any
   raw `<`, `&` or `"` would either break the document or let generated text
   inject SSML elements of its own. Everything interpolated is escaped.

2. Markdown read aloud is unbearable — a voice that pronounces asterisks and
   pipe characters, or recites a forty-cell table. `strip_for_speech` reduces a
   reply to what a person would actually say, and drops what nobody wants read.

   Numbers, units and stray language-switching are a DIFFERENT problem and
   deliberately NOT handled here with pattern matching — a fixed list of
   regexes only ever fixes the shorthand someone already thought to write a
   rule for ("GB" but not "GiB", "°C" but not some format nobody anticipated),
   and a sentence that switches language mid-way has no pattern to match at
   all. `synthesize`'s optional `model` runs that whole class of fix through an
   actual model instead — a cheap background tier, resolved by the CALLER
   (Rule 10) — because reading the sentence catches what pattern-matching it
   never will. It is skipped entirely when `model` is empty, which is
   deliberate for live voice-mode `/speak`: that path streams sentence by
   sentence and an extra round trip there would cost the very latency the
   overlap design exists to avoid. Automation pushes and the text_to_speech
   skill have no such constraint and pass a model. When no model is given (or
   the call fails), the text goes out exactly as `strip_for_speech` left it —
   markdown-clean, but with units and symbols unchanged; that degradation is
   accepted rather than papered over with a regex safety net.

Azure bills per character INCLUDING markup, so stripping before synthesis is
also what keeps the bill down.
"""

from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Azure caps a single synthesis request; well under it is also just good
# practice for a sentence queue, where each request should be one utterance.
MAX_CHARS = 3000

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def configured(provider: str | None = None) -> bool:
    """Whether voice output is available. Callers degrade, never crash.

    With no argument this asks "can anything speak", which is what the client's
    status check wants — a missing Azure key is no longer the same as having no
    voice, now that OpenAI can carry it on its own.
    """
    if provider == "azure":
        return bool(settings.azure_speech_key)
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "elevenlabs":
        return bool(settings.elevenlabs_api_key)
    return bool(settings.azure_speech_key or settings.openai_api_key or settings.elevenlabs_api_key)


def _endpoint() -> str:
    region = settings.azure_speech_region.strip()
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"


# ── Text preparation ────────────────────────────────────────────────────────

# Blocks nobody wants read aloud, replaced with a short spoken acknowledgement
# so the listener knows something was skipped rather than silently losing it.
_FENCE_RE = re.compile(r"```[\s\S]*?```")
# A run of consecutive pipe rows is one table. Matching the whole block (rather
# than each row) lets the marker stay WHERE the table was, instead of drifting
# to the end of the reply where it makes no sense to a listener.
_TABLE_BLOCK_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.MULTILINE)

# Spoken stand-ins for the blocks that get dropped, keyed by language subtag.
# These are read out loud, so they belong to the language of the reply — not to
# the codebase. Unknown languages fall back to English.
_MARKERS: dict[str, tuple[str, str]] = {
    "en": ("code omitted", "table omitted"),
    "tr": ("kod atlandı", "tablo atlandı"),
    "de": ("Code ausgelassen", "Tabelle ausgelassen"),
    "es": ("código omitido", "tabla omitida"),
    "fr": ("code omis", "tableau omis"),
}


def _markers(locale: str | None) -> tuple[str, str]:
    lang = (locale or "en").split("-")[0].lower()
    return _MARKERS.get(lang, _MARKERS["en"])


def strip_for_speech(text: str, locale: str | None = None) -> str:
    """Reduce markdown to plain spoken prose.

    Removes what reads badly (fences, tables, emphasis marks, link URLs,
    heading hashes) rather than everything — the goal is a sentence a person
    would say, not a stripped-bare token stream.

    `locale` picks the language of the omission markers. They are spoken aloud
    like any other words, so an English marker inside a Turkish reply is heard
    as the assistant switching language mid-sentence.
    """
    if not text:
        return ""

    code_marker, table_marker = _markers(locale)
    out = _FENCE_RE.sub(f" {code_marker} ", text)
    # Reading a grid cell-by-cell is noise; say a table was here and move on.
    out = _TABLE_BLOCK_RE.sub(f" {table_marker} ", out)

    out = re.sub(r"`([^`]*)`", r"\1", out)                  # inline code ticks
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", out)         # images
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", out)      # links → label only
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.MULTILINE)   # headings
    out = re.sub(r"^\s{0,3}>\s?", "", out, flags=re.MULTILINE)        # quotes
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.MULTILINE)        # bullets
    out = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", out)           # emphasis
    out = re.sub(r"^\s*[-*_]{3,}\s*$", "", out, flags=re.MULTILINE)   # rules
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_SPEECH_SANITIZE_SYSTEM = (
    "You rewrite text so a text-to-speech engine reads it the way a person "
    "actually talks. You do not summarize, shorten, or change what it says — "
    "only how the words sound out loud.\n\n"
    "YOU ARE NOT A TRANSLATOR AND THIS IS NOT A TRANSLATION TASK. Read the "
    "text and work out what language it is ACTUALLY written in — do not "
    "assume, do not default to English, look at the actual words. Whatever "
    "that language is, your entire output is in that SAME language, "
    "unchanged, every single time. Turning a Turkish message into English, "
    "an English one into Turkish, or either into any other language, is not "
    "a stylistic choice you get to make — it is a failure of the one job "
    "you have, full stop, with no exception for 'it sounded more natural' "
    "or any other reason.\n\n"
    "Rewrite whatever would read badly:\n"
    "- Time ranges ('08:00-13:00'), temperatures ('26.5°C'), percentages "
    "('~44%'), sizes ('79.4 GB'), speeds ('12.2 km/h') and any other "
    "symbol-and-digit shorthand — spell them out the way someone would "
    "actually say them, IN THE TEXT'S OWN LANGUAGE. This applies to ANY "
    "such shorthand, not just these examples.\n"
    "- A STRAY word or phrase written in a DIFFERENT language than the rest "
    "of the sentence — rewrite that one stray bit into the text's own "
    "language. Never the reverse, and never touch a sentence that was "
    "already consistent. A genuine proper name (a person, a place, a "
    "company) may stay exactly as it is, said once, plainly — that is a "
    "name, not a language switch, and is not what this rule is for.\n"
    "- Any leftover 'thinking out loud' line that is not part of the actual "
    "message itself (\"Let me compose the briefing\", \"Here's the "
    "summary:\") — delete it. The output IS the message, not a description "
    "of writing one.\n\n"
    "Everything else — facts, length, tone, order, word choice, and above "
    "all the LANGUAGE — stays exactly as it is. Do not add commentary, "
    "caveats, or anything of your own. Output ONLY the rewritten text — no "
    "preamble, no quotes, no markdown fence."
)


def _sanitize_response_text(response) -> str:
    """Shape-tolerant text extraction — same pattern as the automation
    polisher's own reader, kept local rather than imported so this module
    does not reach into automations/ for three lines (Rule: layering is
    one-way, services do not depend on a sibling service's internals)."""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "".join(parts)
    return ""


async def _llm_sanitize_for_speech(text: str, model: str) -> str:
    """Rewrite `text` for natural speech via an actual model pass.

    Deliberately not a pattern-matcher: a fixed list of rules only ever fixes
    the shorthand someone already thought to look for. Reading the actual
    sentence catches an unusual unit, a stray switch of language, or a
    leftover "let me compose this" line, none of which any fixed list would
    have known to look for.

    Takes NO locale hint — the caller's `settings.tts_locale` is a per-
    deployment default (one automation can legitimately run in a different
    language than another; Speda's morning briefing is pinned to English
    while everything else here defaults Turkish), so handing the sanitizer a
    blanket assumption would tell it the wrong language exactly when it
    matters. The model reads the actual words and works out what language
    they are in — far more reliable than a static default that has no idea
    which automation this text came from.

    Best-effort and silent on failure: returns `text` UNCHANGED on any error,
    an empty reply, or a reply that grew implausibly — a sanitize hiccup (a
    rate limit, a provider outage, the account being out of credit) must
    degrade to markdown-stripped-but-otherwise-untouched text, never to a
    broken or missing voice message. `model` empty skips the call entirely
    with no cost at all — see the module docstring for why `/voice/speak`
    never passes one.
    """
    if not model or not text:
        return text
    from app.services.llm_client import LLMClient

    try:
        client = LLMClient()
        response = await client.create_message(
            model=model,
            system=_SPEECH_SANITIZE_SYSTEM,
            messages=[{"role": "user", "content": text}],
            max_tokens=1000,
            reasoning_effort="minimal",
        )
    except Exception as exc:  # noqa: BLE001 — a sanitize failure must not break the voice message
        logger.warning("tts_llm_sanitize_failed", extra={"error": str(exc)})
        return text

    out = _sanitize_response_text(response).strip()
    if not out or len(out) > len(text) * 2 + 200:
        # Empty (a reasoning model on a tight budget) or a runaway rewrite —
        # the untouched input is safer than trusting either.
        logger.warning("tts_llm_sanitize_unusable", extra={"chars": len(out)})
        return text
    return out


def build_ssml(text: str, voice: str, locale: str | None = None) -> str:
    """Wrap text in an SSML document. Every interpolated value is escaped —
    the text is model-authored and must never be able to introduce markup.

    `locale` is the language of the TEXT. Pass it whenever the voice is a
    multilingual one, whose name deliberately does not match what it is
    speaking; guessing from the name there yields English phonetics over
    Turkish words."""
    # Azure voice names carry their locale as the first two segments
    # ("tr-TR-EmelNeural" → "tr-TR"). Guessing is correct only for a native
    # voice, so it is the last resort, never the normal path.
    if locale is None:
        parts = voice.split("-")
        locale = "-".join(parts[:2]) if len(parts) >= 2 else "en-US"
    return (
        f'<speak version="1.0" xml:lang={quoteattr(locale)}>'
        f"<voice name={quoteattr(voice)}>{escape(text)}</voice>"
        f"</speak>"
    )


# ── Providers ───────────────────────────────────────────────────────────────
#
# Voice refs follow the same shape as model refs everywhere else in this repo
# (see config.py's "Multi-provider LLM routing"), with one extra segment
# because a voice needs both an engine and a name:
#
#     azure:neural:en-US-BrianMultilingualNeural
#     openai:gpt-4o-mini-tts:nova
#     elevenlabs:eleven_multilingual_v2:21m00Tcm4TlvDq8ikWAM
#
# A BARE name means Azure, so every ref written before OpenAI existed — the
# profiles' voice_id, tts_default_voice — keeps working untouched.

_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

# ElevenLabs' current multilingual model — the only one worth defaulting to
# here, since a per-agent voice speaking Turkish (tts_locale) needs one model
# that actually supports it rather than an English-only model silently
# mangling the pronunciation.
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"

# OpenAI's TTS models. gpt-4o-mini-tts is the current one and the only one that
# accepts `instructions`; tts-1 is faster and cheaper, tts-1-hd cleaner.
OPENAI_MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")

# The full voice set. The last five are gpt-4o-mini-tts only — the older tts-1
# models silently fall back to alloy for anything they do not know, which reads
# as "my voice setting is being ignored", so they are filtered per model below.
_OPENAI_VOICES_BASE = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_OPENAI_VOICES_NEW = ("ash", "ballad", "coral", "sage", "verse")


def openai_voices(model: str) -> tuple[str, ...]:
    """Voices a given OpenAI model actually supports."""
    if model == "gpt-4o-mini-tts":
        return _OPENAI_VOICES_BASE + _OPENAI_VOICES_NEW
    return _OPENAI_VOICES_BASE


def parse_voice_ref(ref: str) -> tuple[str, str, str]:
    """Split a voice ref into (provider, model, voice).

    Accepts the bare Azure voice names that predate multi-provider support, so
    an unqualified "tr-TR-EmelNeural" still resolves to Azure rather than
    becoming an error the owner has to go and fix in three places.
    """
    parts = (ref or "").split(":")
    if len(parts) >= 3 and parts[0] == "openai":
        return "openai", parts[1], ":".join(parts[2:])
    if len(parts) >= 3 and parts[0] == "elevenlabs":
        return "elevenlabs", parts[1], ":".join(parts[2:])
    if len(parts) >= 2 and parts[0] == "azure":
        return "azure", "neural", ":".join(parts[1:])
    return "azure", "neural", ref


def providers() -> list[str]:
    """Which engines have a usable credential right now. Drives the picker —
    an engine with no key must not be offered, because choosing it produces
    silence rather than an error the owner can act on."""
    out = []
    if settings.azure_speech_key:
        out.append("azure")
    if settings.openai_api_key:
        out.append("openai")
    if settings.elevenlabs_api_key:
        out.append("elevenlabs")
    return out


def resolve_voice(
    profiles, agent_id: str | None, explicit: str | None = None, profile=None,
) -> str:
    """Explicit request wins, then the owner's own pin (Settings → Voices),
    then the agent's profile, then the engine default. Shared by /voice/speak
    (routers/voice.py) and automation voice delivery (core/trigger_runner.py)
    so the two paths can never resolve a different voice for the same agent —
    a per-agent identity, not a per-caller preference, and it belongs here
    rather than duplicated in each caller.

    `profile`, if given, is used INSTEAD of looking `agent_id` up in
    `profiles` — for a caller (trigger_runner, mid-turn) that already holds
    the resolved profile object and has no registry to hand over. Pass
    `profiles=None` in that case; it is never consulted when `profile` is set.

    Same precedence as the model-pin system (CLAUDE.md's routing matrix): the
    owner's live pin outranks the profile's own default, which is what an
    UNPINNED agent falls back to.
    """
    if explicit:
        return explicit
    if agent_id:
        from app.core.runtime_state import get_voice_overrides

        pinned = get_voice_overrides().get(agent_id, {}).get("voice_id")
        if pinned:
            return pinned
        p = profile if profile is not None else (profiles.get(agent_id) if profiles is not None else None)
        if p is not None and p.voice_id:
            return p.voice_id
    return settings.tts_default_voice


# The ElevenLabs voice_settings this module knows how to tune from Settings →
# Voices. Anything else in their API (language_code, output_format, …) is
# either not applicable to the pinned multilingual model or already fixed
# elsewhere (composer/trigger_runner always want MP3 for sendAudio).
VOICE_SETTINGS_KEYS = ("stability", "similarity_boost", "style", "speed", "use_speaker_boost")


def resolve_voice_settings(agent_id: str | None) -> dict | None:
    """The owner's tuning override for this agent's voice, or None to leave
    ElevenLabs on that voice's own dashboard defaults — the correct behaviour
    for a knob nobody has touched from here. Only meaningful for the
    ElevenLabs provider; Azure/OpenAI ignore it (see synthesize())."""
    if not agent_id:
        return None
    from app.core.runtime_state import get_voice_overrides

    override = get_voice_overrides().get(agent_id, {})
    out = {k: override[k] for k in VOICE_SETTINGS_KEYS if k in override}
    return out or None


# ── Synthesis ───────────────────────────────────────────────────────────────

class TTSError(RuntimeError):
    """Synthesis failed. Carries a message safe to show the owner."""


async def synthesize(
    text: str, voice: str | None = None, locale: str | None = None,
    voice_settings: dict | None = None, sanitize_model: str = "",
) -> bytes:
    """Synthesize `text` and return encoded audio (MP3 by default).

    `locale` is the language the TEXT is in, which is NOT the voice's own
    locale: a multilingual voice is named `en-US-…` precisely so it can speak
    something else. Defaults to settings.tts_locale; only when that is empty
    does build_ssml fall back to guessing from the voice name.

    `voice_settings` (resolve_voice_settings()) tunes ElevenLabs'
    stability/similarity_boost/style/speed/use_speaker_boost for THIS call.
    Azure and OpenAI ignore it silently — they have no equivalent knob, and a
    caller resolving settings once for whichever provider is active should
    not have to branch on provider itself.

    `sanitize_model` runs the text through `_llm_sanitize_for_speech` — the
    model-driven cleanup pass that expands units/symbols and fixes stray
    language-switching, resolved and named by the CALLER (Rule 10; this
    module names no model). Leave it empty to skip that pass entirely, which
    every caller on the live voice-mode path does deliberately — see the
    module docstring.

    Raises TTSError with a readable message on any failure — an unconfigured
    key, an empty utterance, or an upstream error. Callers in a streaming path
    should treat a failure as "this sentence stays silent", not as a dead turn.
    """
    spoken = await prepare_speech_text(text, locale, sanitize_model)
    return await synthesize_prepared(spoken, voice, locale, voice_settings)


async def prepare_speech_text(text: str, locale: str | None = None, sanitize_model: str = "") -> str:
    """Run the full text-preparation pipeline (markdown strip → LLM sanitize
    → length cap) and return the final spoken string, with NO synthesis call.

    Split out from `synthesize` so a caller that also needs to SHOW the
    transcript somewhere (a voice automation's Telegram caption) can display
    exactly what was actually spoken, rather than the pre-sanitized original —
    showing the raw text there while the audio says something else (units
    expanded, a stray language switch fixed) would make the transcript a
    second, contradicting draft instead of a record of the first one.
    """
    spoken_locale = locale or settings.tts_locale or None
    spoken = strip_for_speech(text, spoken_locale)
    if not spoken:
        raise TTSError("Nothing to speak after stripping markup.")
    spoken = await _llm_sanitize_for_speech(spoken, sanitize_model)
    if len(spoken) > MAX_CHARS:
        spoken = spoken[:MAX_CHARS]
    return spoken


async def synthesize_prepared(
    spoken: str, voice: str | None = None, locale: str | None = None,
    voice_settings: dict | None = None,
) -> bytes:
    """Synthesize TEXT THAT IS ALREADY SPEECH-READY (see `prepare_speech_text`)
    — no stripping, no LLM pass. Callers that only want audio back from raw
    text should call `synthesize` instead; this exists so a caller that
    already ran `prepare_speech_text` (to get the spoken string for display)
    never pays for that pipeline twice."""
    if not spoken:
        raise TTSError("Nothing to speak after stripping markup.")
    spoken_locale = locale or settings.tts_locale or None
    provider, model, name = parse_voice_ref(voice or settings.tts_default_voice)
    if not configured(provider):
        raise TTSError({
            "openai": "OpenAI voices need OPENAI_API_KEY.",
            "elevenlabs": "ElevenLabs voices need ELEVENLABS_API_KEY.",
        }.get(provider, "Voice output is not configured — set AZURE_SPEECH_KEY."))
    if provider == "openai":
        return await _openai_synthesize(spoken, model, name, spoken_locale)
    if provider == "elevenlabs":
        return await _elevenlabs_synthesize(spoken, model, name, voice_settings)
    return await _azure_synthesize(spoken, name, spoken_locale)


async def _openai_synthesize(
    spoken: str, model: str, voice: str, locale: str | None,
) -> bytes:
    """OpenAI's speech endpoint. Plain text, no SSML.

    Nothing is escaped here BECAUSE nothing is marked up: the text travels as a
    JSON string field, so there is no document for generated content to break
    out of. That is the one real advantage over Azure's SSML, and it is why
    this path is shorter rather than sloppier.

    The locale is passed as an `instructions` hint on gpt-4o-mini-tts, the only
    model that accepts it. These models infer language from the text on their
    own, so this only settles the ambiguous cases — a loanword-heavy Turkish
    sentence that could plausibly be read with English phonetics.
    """
    body: dict = {
        "model": model,
        "input": spoken,
        "voice": voice,
        "response_format": "mp3",
    }
    if model == "gpt-4o-mini-tts" and locale:
        body["instructions"] = f"Speak naturally in {locale}."

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_OPENAI_TTS_URL, headers=headers, json=body)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = {
            400: "rejected the request (check the voice is one this model supports)",
            401: "rejected the key",
            429: "rate-limited the request",
        }.get(status, f"returned HTTP {status}")
        logger.warning(
            "tts_openai_error",
            extra={"status": status, "model": model, "voice": voice,
                   "body": exc.response.text[:300]},
        )
        raise TTSError(f"OpenAI speech {detail}.") from exc
    except httpx.HTTPError as exc:
        logger.warning("tts_transport_error", extra={"error": str(exc), "voice": voice})
        raise TTSError("Could not reach OpenAI speech.") from exc

    audio = resp.content
    if not audio:
        raise TTSError("OpenAI speech returned no audio.")
    logger.info(
        "tts_synthesized",
        extra={"provider": "openai", "model": model, "voice": voice,
               "chars": len(spoken), "bytes": len(audio)},
    )
    return audio


async def _elevenlabs_synthesize(
    spoken: str, model: str, voice_id: str, voice_settings: dict | None = None,
) -> bytes:
    """ElevenLabs' speech endpoint. Plain text in a JSON field, same reasoning
    as OpenAI's path for why nothing here needs escaping: there is no document
    for model-authored text to break out of, only a string value.

    No `locale` parameter — ElevenLabs' multilingual models detect the
    language from the text itself and have no per-request language hint to
    give them, unlike OpenAI's `instructions` field.

    `voice_settings` is omitted from the body entirely when None/empty — NOT
    sent as `{}` or as zeroed defaults. ElevenLabs falls back to the voice's
    own dashboard settings only when the key is absent; sending an empty
    object would override a carefully tuned voice with library defaults the
    owner never asked for.
    """
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body: dict = {
        "text": spoken,
        "model_id": model or ELEVENLABS_DEFAULT_MODEL,
    }
    if voice_settings:
        body["voice_settings"] = voice_settings
    url = _ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = {
            401: "rejected the key",
            404: "has no voice with that id — check it against the owner's ElevenLabs voice library",
            422: "rejected the request (check the model supports this voice, and that voice_settings are in range)",
            429: "rate-limited the request (quota or concurrency)",
        }.get(status, f"returned HTTP {status}")
        logger.warning(
            "tts_elevenlabs_error",
            extra={"status": status, "model": model, "voice": voice_id,
                   "body": exc.response.text[:300]},
        )
        raise TTSError(f"ElevenLabs speech {detail}.") from exc
    except httpx.HTTPError as exc:
        logger.warning("tts_transport_error", extra={"error": str(exc), "voice": voice_id})
        raise TTSError("Could not reach ElevenLabs speech.") from exc

    audio = resp.content
    if not audio:
        raise TTSError("ElevenLabs speech returned no audio.")
    logger.info(
        "tts_synthesized",
        extra={"provider": "elevenlabs", "model": model, "voice": voice_id,
               "chars": len(spoken), "bytes": len(audio)},
    )
    return audio


async def _azure_synthesize(spoken: str, voice: str, spoken_locale: str | None) -> bytes:
    """Azure Speech. See build_ssml for why the text is escaped and why the
    locale is passed explicitly rather than read off the voice name."""
    ssml = build_ssml(spoken, voice, spoken_locale)

    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": settings.tts_output_format,
        # Azure rejects requests without a User-Agent.
        "User-Agent": "speda-mark-vi",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_endpoint(), headers=headers, content=ssml.encode("utf-8"))
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # 401/403 is nearly always a wrong key or a key from another region —
        # worth naming, because the generic message sends people hunting.
        detail = {
            401: "rejected the key",
            403: "rejected the key (check the region matches the key)",
            429: "rate-limited the request",
        }.get(status, f"returned HTTP {status}")
        logger.warning(
            "tts_upstream_error",
            extra={"status": status, "voice": voice, "body": exc.response.text[:300]},
        )
        raise TTSError(f"Azure Speech {detail}.") from exc
    except httpx.HTTPError as exc:
        logger.warning("tts_transport_error", extra={"error": str(exc), "voice": voice})
        raise TTSError("Could not reach Azure Speech.") from exc

    audio = resp.content
    if not audio:
        raise TTSError("Azure Speech returned no audio.")
    logger.info(
        "tts_synthesized",
        extra={"voice": voice, "chars": len(spoken), "bytes": len(audio)},
    )
    return audio


async def list_voices() -> list[dict]:
    """Every voice the owner can actually pick, across all configured engines.

    Each entry carries a full `id` ref (provider:model:voice) so the picker
    never has to reassemble one, and a `provider` so it can group the way the
    text-model picker does. An engine with no key contributes nothing — a voice
    that cannot speak must not be offerable.
    """
    out: list[dict] = []

    # OpenAI first: a static roster, no network call, so the picker still
    # populates when Azure's region is unreachable.
    if configured("openai"):
        for model in OPENAI_MODELS:
            for name in openai_voices(model):
                out.append({
                    "id": f"openai:{model}:{name}",
                    "name": name,
                    "provider": "openai",
                    "model": model,
                    # These models are multilingual and infer language from the
                    # text, so there is no locale to report.
                    "locale": "",
                    "gender": "",
                    "display": f"{name} · {model}",
                })

    out.extend(await _elevenlabs_voices())
    out.extend(await _azure_voices())
    return out


async def _elevenlabs_voices() -> list[dict]:
    """The owner's OWN ElevenLabs voice library — a real network call, unlike
    OpenAI's static roster, because ElevenLabs voices are per-account (premade
    ones plus anything cloned or added from the marketplace) rather than a
    fixed public list. Returns [] when unconfigured or unreachable, same
    contract as `_azure_voices`."""
    if not configured("elevenlabs"):
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _ELEVENLABS_VOICES_URL,
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("tts_voice_list_failed", extra={"provider": "elevenlabs", "error": str(exc)})
        return []
    return [
        {
            "id": f"elevenlabs:{ELEVENLABS_DEFAULT_MODEL}:{v.get('voice_id')}",
            "name": v.get("name") or v.get("voice_id"),
            "provider": "elevenlabs",
            "model": ELEVENLABS_DEFAULT_MODEL,
            "locale": "",
            "gender": (v.get("labels") or {}).get("gender", ""),
            "display": v.get("name") or v.get("voice_id"),
        }
        for v in (data.get("voices") or [])
        if v.get("voice_id")
    ]


async def _azure_voices() -> list[dict]:
    """Voices available to this key's region. Returns [] when unconfigured or
    unreachable — a failed catalogue must not empty the whole picker."""
    if not configured("azure"):
        return []
    region = settings.azure_speech_region.strip()
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url, headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key}
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("tts_voice_list_failed", extra={"error": str(exc)})
        return []
    return [
        {
            # Fully qualified, like the OpenAI entries — the picker sends this
            # back verbatim and never has to know how a ref is assembled.
            "id": f"azure:{v.get('ShortName')}",
            "name": v.get("ShortName"),
            "provider": "azure",
            "model": "neural",
            "locale": v.get("Locale") or "",
            "gender": v.get("Gender") or "",
            "display": v.get("LocalName") or v.get("DisplayName") or v.get("ShortName"),
        }
        for v in data
        if v.get("ShortName")
    ]
