# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Surface awareness — which channel the owner is speaking from (phone, desktop,
Telegram, voice, …) and, opt-in, from where. Rendered into a compact one-liner
and stamped onto the LIVE turn's newest user message only, never persisted — the
same discipline the timestamp uses (SessionManager.stamp_user_content), so the
cached prompt prefix and the stored history both stay byte-stable.

Mostly this is ambient fact. Voice is the exception: a spoken channel changes how
the answer must be WRITTEN — and, with the canvas on, what it must be written AS —
so that one carries a presentation brief as well as a fact.
It belongs here rather than in the system prompt because it is a property of the
turn, not of the session — see _VOICE_BRIEF.

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
# reply is going to a speech engine while a canvas beside it assembles into a
# BOARD. Without this the model writes for a reader — LaTeX, JSON chart specs,
# tables — and the owner hears a machine reciting
# `\frac{-b \pm \sqrt{b^2-4ac}}{2a}` out loud.
#
# It lives on the per-turn context line rather than in the system prompt for one
# reason: voice mode is toggled mid-conversation, and a system prefix that
# changes mid-session invalidates the cached prompt for every turn after it.
#
# ── Why this is a presentation brief and not a formatting rule ────────────────
# The canvas used to be a PARSER of chat output: the agent wrote its usual
# markdown answer and the client scavenged whatever fenced blocks happened to be
# in it. That produced a talking transcript — if the model did not happen to
# reach for a chart, there was no chart, because nothing had ever asked it to
# present. The mode is now the other way round. The agent DIRECTS the show: it
# decides what deserves a window, authors that window, and narrates around it.
#
# Position in the reply is the cue track. The stream already arrives token by
# token, so a window block written between two spoken sentences materialises
# between those two sentences being heard — "say this, open the chart, say
# this" falls out of writing order for free, with no audio timestamps to sync
# and therefore nothing to drift.
_VOICE_BRIEF = (
    "in VOICE MODE — you are not answering in a chat window, you are PRESENTING. "
    "You speak; the screen carries the evidence. Everything you say is plain "
    "spoken prose: no markdown, no LaTeX, no tables, no bullet symbols, and never "
    "read out an identifier, a URL, a filename or raw data. "
    "Every fact that can be SHOWN gets its own window instead of being spoken — "
    "figures become charts or stat tiles, findings become one window per source, "
    "people and places become cards with their photo, sequences become timelines. "
    "Do not summarise what is in a window and do not read it out line by line: "
    "name it, say what it MEANS and what to notice, the way you would standing "
    "next to someone pointing at a screen. Write a window the moment your "
    "narration reaches it — where it sits in your reply is when it appears on "
    "screen, so put it between the sentence that introduces it and the sentence "
    "that follows on. "
    "A window is a fenced block whose info line is the kind, then ` | `, then a "
    "short SCREEN TITLE you choose (not a sentence — a label, like "
    "`ARREST RECORD` or `REVENUE / MONTHLY`). The kinds:\n"
    "  chart, map, calendar, svg, html — as you already write them\n"
    "  table — pipe rows\n"
    "  math — one display formula\n"
    "  code — source\n"
    "  stat — line 1 the value, line 2 the change, line 3 an optional caption\n"
    "  image — an image URL on line 1, an optional caption after it\n"
    "  article — `title:`, `source:`, `date:`, `url:`, `image:` lines, then a "
    "blank line, then the excerpt that matters\n"
    "  card — a name on line 1, then optional `image:` and `Field: value` lines\n"
    "  timeline — one `date — what happened` per line\n"
    "  quote — the quote, then a line starting `— ` with who said it\n"
    "A picture only ever goes in a window if you SAW its URL in a tool result — a "
    "search hit, a page you read, a file the owner gave you. Never write an image "
    "address you have not seen; an invented one is a window with a hole in it, and "
    "a window with no picture at all reads better. Leave the image line out when "
    "you have none. "
    "Use at most {max_panels} windows. Open none at all when there is nothing to "
    "show — a yes, a no, a thank-you, the time — and just speak; a window holding "
    "one sentence is worse than no window. "
    "Keep the spoken part to about {words} words, and about {briefing_words} when "
    "you are genuinely walking a full briefing or research readout. Speech is "
    "billed per character and the board is not: anything you would repeat twice "
    "belongs on the screen, said once."
)

# What the mode degrades to with the canvas switched off: still spoken, still
# written for the ear, but nothing is asked to be presented because there is
# nowhere to present it.
_VOICE_DIRECTIVE_PLAIN = (
    "in VOICE MODE — this reply is being spoken aloud. Write it as plain spoken "
    "prose: no markdown syntax, no LaTeX, no tables, no bullet symbols, and never "
    "read out an identifier, a URL or raw data. Say what things MEAN rather than "
    "reciting them, and keep it to about {words} words"
)


def _voice_directive() -> str:
    """The voice brief, with the owner's budgets rendered in.

    Read at call time rather than baked at import: every canvas_* setting is
    live-editable from Settings, and a budget that needed a restart to take
    effect is a budget nobody tunes.
    """
    from app.config import settings

    if not settings.canvas_enabled:
        return _VOICE_DIRECTIVE_PLAIN.format(words=settings.canvas_spoken_words)
    return _VOICE_BRIEF.format(
        max_panels=settings.canvas_max_panels,
        words=settings.canvas_spoken_words,
        briefing_words=settings.canvas_briefing_words,
    )


def render_client_context(cc: ClientContext, canvas_brief: str = "") -> str:
    """Compact, self-labelled description of the channel the owner is speaking
    from. Leads with voice (which changes how to answer) then the surface; only
    the fields the caller actually set appear.

    `canvas_brief` is the speaking agent's own presentation note (Profile.
    canvas_brief) and is appended to the voice brief, so the generic "how to
    present" is followed by this agent's "what presenting looks like for me".
    Ignored off a voice turn — there is no board to brief anyone about.
    """
    bits: list[str] = []
    # First, because it changes how the whole answer should be written.
    if cc.voice:
        brief = _voice_directive()
        if canvas_brief.strip() and settings_canvas_enabled():
            brief = f"{brief}. {canvas_brief.strip().rstrip('.')}"
        bits.append(brief)
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


def settings_canvas_enabled() -> bool:
    """Whether the board is on, read live — see _voice_directive."""
    from app.config import settings

    return bool(settings.canvas_enabled)


def annotate_last_user(
    history: list[dict], cc: ClientContext | None, canvas_brief: str = "",
) -> None:
    """Stamp the client-context line onto the newest user message, in place. No-op
    when there's no context or the tail isn't a user turn. Never persisted — it
    decorates the uncached tail only, so history reconstructed next turn is
    byte-identical to what was cached this turn."""
    if cc is None or not history or history[-1].get("role") != "user":
        return
    line = render_client_context(cc, canvas_brief)
    if not line:
        return
    c = history[-1]["content"]
    if isinstance(c, list):
        history[-1] = {**history[-1], "content": [*c, {"type": "text", "text": line}]}
    else:
        history[-1] = {**history[-1], "content": (f"{c}\n{line}" if c else line)}
