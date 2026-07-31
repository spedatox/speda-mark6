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


def configured() -> bool:
    """Whether voice output is available at all. Callers degrade, never crash."""
    return bool(settings.azure_speech_key)


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


def strip_for_speech(text: str) -> str:
    """Reduce markdown to plain spoken prose.

    Removes what reads badly (fences, tables, emphasis marks, link URLs,
    heading hashes) rather than everything — the goal is a sentence a person
    would say, not a stripped-bare token stream.
    """
    if not text:
        return ""

    out = _FENCE_RE.sub(" (code omitted) ", text)
    # Reading a grid cell-by-cell is noise; say a table was here and move on.
    out = _TABLE_BLOCK_RE.sub(" (table omitted) ", out)

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


# ── Synthesis ───────────────────────────────────────────────────────────────

class TTSError(RuntimeError):
    """Synthesis failed. Carries a message safe to show the owner."""


async def synthesize(text: str, voice: str | None = None, locale: str | None = None) -> bytes:
    """Synthesize `text` and return encoded audio (MP3 by default).

    `locale` is the language the TEXT is in, which is NOT the voice's own
    locale: a multilingual voice is named `en-US-…` precisely so it can speak
    something else. Defaults to settings.tts_locale; only when that is empty
    does build_ssml fall back to guessing from the voice name.

    Raises TTSError with a readable message on any failure — an unconfigured
    key, an empty utterance, or an upstream error. Callers in a streaming path
    should treat a failure as "this sentence stays silent", not as a dead turn.
    """
    if not configured():
        raise TTSError("Voice output is not configured — set AZURE_SPEECH_KEY.")

    spoken = strip_for_speech(text)
    if not spoken:
        raise TTSError("Nothing to speak after stripping markup.")
    if len(spoken) > MAX_CHARS:
        spoken = spoken[:MAX_CHARS]

    voice = voice or settings.tts_default_voice
    ssml = build_ssml(spoken, voice, locale or settings.tts_locale or None)

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
    """Voices available to this key's region — used by the settings UI to
    populate the per-agent voice picker. Returns [] when unconfigured."""
    if not configured():
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
            "name": v.get("ShortName"),
            "locale": v.get("Locale"),
            "gender": v.get("Gender"),
            "display": v.get("LocalName") or v.get("DisplayName"),
        }
        for v in data
        if v.get("ShortName")
    ]
