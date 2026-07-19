"""
Surface awareness — which channel the owner is speaking from (phone, desktop,
Telegram, …) and, opt-in, from where. Rendered into a compact one-liner and
stamped onto the LIVE turn's newest user message only, never persisted — the
same discipline the timestamp uses (SessionManager.stamp_user_content), so the
cached prompt prefix and the stored history both stay byte-stable.

Every ingestion path (HTTP chat, Telegram gateway) funnels through here so SPEDA
learns the surface uniformly, no matter how the turn arrived.
"""
from __future__ import annotations

from app.schemas.chat import ClientContext

# Human phrasing for the surface the owner is on — the single most important fact
# ("am I on the phone, the desktop, or Telegram right now?").
_SURFACE_PHRASE = {
    "telegram": "on Telegram",
    "android": "on the Android app",
    "ios": "on the iOS app",
    "desktop": "on the desktop app",
    "web": "in the web app",
}


def telegram_context() -> ClientContext:
    """The client context for a Telegram-delivered turn."""
    return ClientContext(platform="telegram")


def render_client_context(cc: ClientContext) -> str:
    """Compact, self-labelled description of where/what the owner is speaking from.
    Leads with the surface; only the fields the caller actually set appear."""
    bits: list[str] = []
    if cc.platform:
        bits.append(_SURFACE_PHRASE.get(cc.platform.lower(), f"on {cc.platform}"))
    device = " ".join(x for x in [cc.os_version, cc.device] if x)
    if device:
        bits.append(device)
    if cc.location is not None:
        loc = cc.location
        where = loc.place or f"{loc.lat:.4f},{loc.lng:.4f}"
        acc = f" ±{round(loc.accuracy_m)}m" if loc.accuracy_m else ""
        # Exact coordinates too, so distance/direction questions are answerable.
        bits.append(f"location: {where}{acc} [{loc.lat:.5f},{loc.lng:.5f}]")
    if cc.locale:
        bits.append(f"locale {cc.locale}")
    if cc.app_version:
        bits.append(f"app {cc.app_version}")
    if not bits:
        return ""
    return "[client context — " + " · ".join(bits) + "]"


def annotate_last_user(history: list[dict], cc: ClientContext | None) -> None:
    """Stamp the client-context line onto the newest user message, in place. No-op
    when there's no context or the tail isn't a user turn. Never persisted — it
    decorates the uncached tail only, so history reconstructed next turn is
    byte-identical to what was cached this turn."""
    if cc is None or not history or history[-1].get("role") != "user":
        return
    line = render_client_context(cc)
    if not line:
        return
    c = history[-1]["content"]
    if isinstance(c, list):
        history[-1] = {**history[-1], "content": [*c, {"type": "text", "text": line}]}
    else:
        history[-1] = {**history[-1], "content": (f"{c}\n{line}" if c else line)}
