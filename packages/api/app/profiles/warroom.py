from app.profiles.base import DocTheme
from app.profiles.speda import SPEDAProfile


class WarRoomProfile(SPEDAProfile):
    """
    WAR ROOM — the House Party Protocol command channel.

    This is SPEDA's brain behind a separate conversation scope: same prompts,
    same full tool access, same model policy. The distinct agent_id exists so
    war-room operations get their own session history (sessions are scoped by
    (user_id, agent_id)) instead of mixing into the owner's day-to-day SPEDA
    chats. The Heartbreaker war room addresses POST /chat/warroom while the
    protocol is engaged.

    dispatch_target=False keeps "warroom" out of the dispatch tool schema —
    agents dispatch to speda, never to its command-channel alias.
    """

    agent_id = "warroom"
    domain = "House Party Protocol command center — full-roster operations"
    name = "War Room"
    doc_theme = DocTheme(accent="#f2b75c")   # engagement amber
    dispatch_target = False
    # A session-scope alias, not a notifying agent — no Telegram bot of its own.
    telegram_enabled = False
