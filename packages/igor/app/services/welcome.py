"""
Welcome remark — a JARVIS-style one-liner for the app's welcome screen.

The home screen already shows a generic "Good morning, <name>". This adds the
flavour beneath it: a short, contextual remark in the addressed agent's voice,
drawn from the owner's memory (who they are + what's current) — the kind of
anticipatory line JARVIS opens with.

Latency is handled by NOT generating per view. A remark is generated once per
agent per part-of-day (with a short TTL) by the agent's CHEAPEST model and
served from an in-process cache thereafter, so the endpoint is effectively free
and instant after the first hit. The frontend shows the static greeting
immediately and swaps this line in when it arrives, so a cold first call is
invisible. Every failure path degrades to "" — the UI just keeps the greeting.
"""

import logging
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.memory_file import MemoryFile

logger = logging.getLogger(__name__)

_OWNER_PATH = "/memories/owner.md"
_CURRENT_PATH = "/memories/current.md"
_TTL_S = 90 * 60          # regenerate at most every ~90 min within a part-of-day
_MEM_CHARS = 900          # how much of each memory file to feed the model


class WelcomeCache:
    """Process-local TTL cache backing get_welcome(). One instance lives on
    app.state.welcome_cache (Rule 6) — created in the lifespan and passed in
    by the router, rather than living as a bare module global."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[str, float]] = {}

    def get(self, key: tuple[str, str]) -> str | None:
        hit = self._entries.get(key)
        if hit is not None and hit[1] > _time.monotonic():
            return hit[0]
        return None

    def set(self, key: tuple[str, str], text: str) -> None:
        self._entries[key] = (text, _time.monotonic() + (_TTL_S if text else 120))


def _part_of_day(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _clean(text: str) -> str:
    """One clean line: strip quotes/emoji-ish wrapping, collapse whitespace."""
    line = " ".join((text or "").split()).strip().strip('"').strip("'")
    # Keep it to a single sentence-ish remark even if the model rambled.
    if len(line) > 200:
        line = line[:200].rsplit(" ", 1)[0] + "…"
    return line


async def _read_memory(user_id: int) -> tuple[str, str]:
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(MemoryFile).where(
                    MemoryFile.user_id == user_id,
                    MemoryFile.path.in_([_OWNER_PATH, _CURRENT_PATH]),
                )
            )).scalars().all()
        by_path = {r.path: (r.content or "") for r in rows}
        return by_path.get(_OWNER_PATH, "")[:_MEM_CHARS], by_path.get(_CURRENT_PATH, "")[:_MEM_CHARS]
    except Exception as e:  # noqa: BLE001 — memory is optional flavour
        logger.warning("welcome_memory_read_failed", extra={"error": str(e)})
        return "", ""


async def get_welcome(agent_id: str, profiles, cache: WelcomeCache, *, user_id: int = 1) -> str:
    """Return a cached-or-freshly-generated welcome remark for `agent_id`.
    Returns "" on any failure so the caller simply keeps the static greeting."""
    profile = profiles.get(agent_id)
    if profile is None:
        return ""

    try:
        now_dt = datetime.now(ZoneInfo(settings.owner_timezone))
    except Exception:  # unknown IANA name → server clock (UTC)
        now_dt = datetime.now()
    pod = _part_of_day(now_dt.hour)
    key = (agent_id, pod)
    hit = cache.get(key)
    if hit is not None:
        return hit

    text = await _generate(profile, pod, now_dt, user_id)
    # Cache even an empty result briefly so a broken provider isn't hammered on
    # every welcome-screen mount.
    cache.set(key, text)
    return text


async def _generate(profile, part_of_day: str, now_dt: datetime, user_id: int) -> str:
    from app.services.llm_client import LLMClient

    owner, current = await _read_memory(user_id)
    model = profile.background_model(profile.allocate_model("user"))
    system = (
        f"You are {profile.name}, the owner's {profile.domain} agent in a Stark-style "
        "personal AI. Greet the owner the way JARVIS would: refined and warm, lightly "
        "witty, quietly anticipatory — never fawning, never corny. Address him "
        "respectfully (sir, or by name)."
    )
    context = (
        f"Time: {now_dt.strftime('%A %H:%M')} ({part_of_day}). "
        f"You are the '{profile.domain}' agent.\n\n"
        f"WHO HE IS (memory/owner.md):\n{owner or '(unknown)'}\n\n"
        f"WHAT'S CURRENT (memory/current.md):\n{current or '(nothing noted)'}\n\n"
        "Write ONE short opening remark (8–16 words) to sit under a "
        f"'Good {part_of_day}' greeting. Make it feel personal to HIM or to your "
        "domain — you may nod to something real from the memory above if it fits "
        "naturally, otherwise keep it about the moment. One line only. No quotes, "
        "no emoji, no preamble — just the remark."
    )
    try:
        client = LLMClient()
        resp = await client.create_message(
            model=model,
            system=system,
            messages=[{"role": "user", "content": context}],
            max_tokens=300,
            reasoning_effort="minimal",
        )
        remark = _clean(resp.content[0].text if resp.content else "")
        logger.info("welcome_generated", extra={"agent_id": profile.agent_id, "model": model, "chars": len(remark)})
        return remark
    except Exception as e:  # noqa: BLE001 — always degrade to the static greeting
        logger.warning("welcome_generate_failed", extra={"agent_id": profile.agent_id, "model": model, "error": str(e)})
        return ""
