"""
Surface awareness — which channel the owner is speaking from (phone, desktop,
Telegram, voice, …) and, opt-in, from where. Rendered into a compact one-liner
and stamped onto the LIVE turn's newest user message only, never persisted — the
same discipline the timestamp uses (SessionManager.stamp_user_content), so the
cached prompt prefix and the stored history both stay byte-stable.

Mostly this is ambient fact. Voice is the exception: a spoken channel changes how
the answer must be WRITTEN, so that one carries a directive as well as a fact.
It belongs here rather than in the system prompt because it is a property of the
turn, not of the session — see _VOICE_DIRECTIVE.

Every ingestion path (HTTP chat, Telegram gateway) funnels through here so Speda
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


# Voice mode is not a fact about the device, it is a fact about the CHANNEL: the
# reply is going to a speech engine, sentence by sentence, while a canvas beside
# it renders whatever the reply draws. Without this the model writes for a
# reader — LaTeX, JSON chart specs, tables — and the owner hears a machine
# reciting `\frac{-b \pm \sqrt{b^2-4ac}}{2a}` out loud.
#
# It lives on the per-turn context line rather than in the system prompt for one
# reason: voice mode is toggled mid-conversation, and a system prefix that
# changes mid-session invalidates the cached prompt for every turn after it.
_VOICE_DIRECTIVE = (
    "in VOICE MODE — this reply is being spoken aloud and rendered on a canvas "
    "at the same time. Write what you SAY as plain spoken prose: no markdown "
    "syntax, no LaTeX, no tables, no bullet symbols, and never read out an "
    "identifier, a URL or raw data. Anything visual — a formula, a chart, a map, "
    "a table, a diagram — goes in its own fenced block, and each block becomes "
    "its own window on the canvas. Do not narrate the blocks' contents "
    "line by line; say what they MEAN and what the owner should notice, the way "
    "you would if you were standing next to them pointing at a screen"
)


def render_client_context(cc: ClientContext) -> str:
    """Compact, self-labelled description of the channel the owner is speaking
    from. Leads with voice (which changes how to answer) then the surface; only
    the fields the caller actually set appear."""
    bits: list[str] = []
    # First, because it changes how the whole answer should be written.
    if cc.voice:
        bits.append(_VOICE_DIRECTIVE)
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


# ── Platform capability ──────────────────────────────────────────────────────
# Some things the owner can ask for are not available on every surface. The
# House Party Protocol is the first: it stages the whole roster in a war room
# with a live transcript and a colour parade across the entire palette, which
# only the desktop client builds. On the phone there is nothing to show, so the
# protocol must not engage there rather than engage invisibly — a protocol the
# owner cannot see running is worse than one that refuses.
#
# Desktop-class surfaces. Anything else — the Android app, Telegram, an
# unstated platform — is not.
_DESKTOP_SURFACES = frozenset({"desktop", "web"})


def is_desktop_surface(platform: str | None) -> bool:
    """Whether `platform` is a surface that renders the full deck.

    Unknown or missing is deliberately NOT desktop: a client that does not say
    what it is cannot be assumed to have a war room to show.
    """
    return (platform or "").strip().lower() in _DESKTOP_SURFACES


# Surfaces that can render the Skyfall countdown — the full-screen arming clock
# with its abort. A DIFFERENT question from the war room's, and deliberately a
# wider answer: the Android app builds this screen, so it qualifies.
#
# The rule underneath both is the same one, though, and it is worth stating
# plainly because it is what stops this from being an exception: a protocol the
# owner cannot SEE running must refuse rather than run invisibly. House Party
# fails that on the phone because there is no war room there. Skyfall passes it,
# because the screen is the protocol. Telegram fails it for both — there is no
# countdown to show and no abort to press, and a Skyfall that fired without one
# would be the whole design thrown away.
_ARMING_SURFACES = frozenset({"desktop", "web", "android", "ios"})


def can_arm_skyfall(platform: str | None) -> bool:
    """Whether `platform` can show a countdown the owner is able to abort.

    Unknown or missing is NOT armable, for the same reason it is not desktop: a
    client that will not say what it is cannot be assumed to have a screen.
    """
    return (platform or "").strip().lower() in _ARMING_SURFACES


#: What every Skyfall refusal says when the surface cannot show the clock.
NO_COUNTDOWN_NOTICE = (
    "The Skyfall Protocol cannot be armed from this channel. Arming opens a "
    "full-screen countdown with an abort — that screen IS the protocol, and "
    "this channel has nowhere to draw it. Firing without one would remove the "
    "only chance to stop it. Ask again from the desktop app or the phone."
)


#: What every refusal path tells the owner, in one place so the tool, the
#: router and the client cannot drift into three different explanations.
DESKTOP_ONLY_NOTICE = (
    "The House Party Protocol is available on the desktop app only. It stages "
    "the whole roster in the war room, and the phone has no war room to stage "
    "it in — engaging from here would run the roster at full grade with nothing "
    "to show for it."
)


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
