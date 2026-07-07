"""
Telegram channel — first-class conversation surface and primary notification
transport for Mark VI. One bot per agent; the orchestrator is untouched (a
Telegram turn is a normal `triggered_by="user"`, `output_mode="respond"` run,
and its SSEEvent stream is rendered to chat bubbles by renderer.py).

See docs/TELEGRAM_ARCHITECTURE.md for the full design and decision log.
"""
