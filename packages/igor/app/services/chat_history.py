"""
Chat history shaping — turns stored ORM Message rows into the plain dicts the
Heartbreaker UI renders.

Pulled out of the chat router (CLAUDE.md Rule 1: zero business logic in
routers) since it is pure content-block parsing with no request/response
concerns of its own: extracting display text, rebuilding data: URLs for
persisted image attachments, and recovering the display-only `_speda_meta`
block (tool disclosure, download cards, upload chips) so a reloaded session
renders identically to the live turn that produced it.
"""

from app.models.message import Message


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(
            block.get('text', '') for block in content
            if isinstance(block, dict) and block.get('type') == 'text'
        )
    return ''


def _extract_images(content) -> list[str]:
    """Rebuild data: URLs from stored base64 image blocks so attachments
    re-render when an old session is reopened (they're persisted in the DB)."""
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'image':
            src = block.get('source', {})
            if src.get('type') == 'base64' and src.get('data'):
                out.append(f"data:{src.get('media_type', 'image/png')};base64,{src['data']}")
    return out


def _extract_meta(content) -> dict:
    """Pull the SPEDA display-only meta block so the tool disclosure,
    download cards (assistant) and upload chips (user) survive a reload."""
    if not isinstance(content, list):
        return {}
    for block in content:
        if isinstance(block, dict) and block.get('type') == '_speda_meta':
            meta = {
                'tools': block.get('tools', []),
                'files': block.get('files', []),
                'uploads': block.get('uploads', []),
                # Provenance for a turn the owner did not write — an n8n
                # automation, or another agent dispatching a task, opening a
                # session. The UI attributes the bubble to the sender instead of
                # to them.
                'trigger': block.get('trigger'),
            }
            # What the BUBBLE should show, when that differs from the text blocks
            # the model reads: the user's own message rather than the wall of
            # extracted upload text, the dispatched task rather than its routing
            # preamble. Carried through only when present — an absent key means
            # "show the real text", and defaulting it to '' silently blanked
            # every reloaded upload bubble.
            if 'text' in block:
                meta['text'] = block['text']
            return meta
    return {}


def final_answer_text(content) -> str:
    """The closing answer of an assistant turn — the text emitted AFTER the last
    tool call — rather than everything the turn said.

    An agentic turn interleaves two very different kinds of text. Between tool
    calls the model narrates what it is about to do ("RSS store is empty, moving
    to deep dive"); after the last tool returns it writes the actual answer.
    Both are streamed live, and both are persisted as one text block, because
    the chat UI wants the whole thing — the narration is what makes a long turn
    watchable, and it is interleaved with tool cards via `afterChars`.

    A delivered message is the opposite case. A briefing pushed to Telegram has
    no tool cards, no live stream and no reader watching the work happen — the
    narration arrives as a preamble of stage directions on top of the report,
    which is exactly how a scheduled briefing ends up opening with "let me get
    the free news first". So delivery takes this slice, and the transcript keeps
    the full text.

    The offset comes from the last tool's `afterChars` (stamped at save time by
    TurnRegistry._persist), so no new persisted field is needed and turns saved
    before this existed still resolve correctly. Falls back to the full text
    whenever the slice would be empty — a turn that ended right after a tool
    call has no closing segment, and delivering nothing is worse than delivering
    the narration.
    """
    full = _extract_text(content).strip()
    tools = _extract_meta(content).get('tools') or []
    offsets = [
        t['afterChars'] for t in tools
        if isinstance(t, dict) and isinstance(t.get('afterChars'), int)
    ]
    if not offsets:
        return full
    tail = _extract_text(content)[max(offsets):].strip()
    return tail or full


def rows_from_messages(messages: list[Message]) -> list[dict]:
    """Shape stored user/assistant Message rows into the plain dicts the UI
    renders for a session's message list.

    One row does not survive as a row: the seed of a background-completion
    report (a legionnaire, or an agent that was dispatched to, finishing and
    waking its caller — app/core/trigger_runner.py). It is stored as a user
    turn because that is what the model was handed, but the owner did not write
    it and it is nothing they asked to read: it is the scaffolding that produced
    the reply below it. So it FOLDS INTO that reply, which carries it as a
    folded card the owner can open — the same shape the live stream paints from
    the START event, so watching the answer arrive and reopening it later look
    alike. If no reply follows (the run died before persisting), the seed stays
    a row of its own rather than vanishing with the work it recorded.
    """
    out = []
    held: dict | None = None   # a report seed waiting for the reply it produced
    for m in messages:
        if m.role not in ('user', 'assistant'):
            continue
        meta = _extract_meta(m.content)
        is_seed = m.role == 'user' and (meta.get('trigger') or {}).get('report')
        if m.role == 'assistant' and held is not None:
            meta = {**meta, 'trigger': held['trigger']}
            held = None
        # A user turn whose real text blocks are not what the bubble should show:
        # document uploads (the blocks hold the extracted file contents) and an
        # inter-agent dispatch (the blocks hold the routing preamble and the
        # agent channel). Both stash the display text in the meta block at save
        # time; see _extract_meta.
        if m.role == 'user' and 'text' in meta:
            content_text = meta['text']
        else:
            content_text = _extract_text(m.content)
        row = {
            'id': str(m.id),
            'role': m.role,
            'content': content_text,
            'tools': meta.get('tools', []),
            'isStreaming': False,
            'isError': False,
            # UTC, ISO-8601 with the Z the naive DB value implies. The war room
            # merges these against inter-agent traffic timestamps to rebuild one
            # group-chat timeline, so a reloaded room reads in the right order.
            'createdAt': m.created_at.isoformat() + 'Z',
        }
        if (imgs := _extract_images(m.content)):
            row['images'] = imgs
        if meta.get('files'):
            row['files'] = meta['files']
        if meta.get('uploads'):
            row['uploads'] = meta['uploads']
        if meta.get('trigger'):
            row['trigger'] = meta['trigger']
        # A seed waits for the reply that consumes it. Anything else arriving
        # first means nothing ever will — release it in place, so it is still
        # in the transcript and still in the right order.
        if held is not None:
            out.append(held)
            held = None
        if is_seed:
            held = row
            continue
        out.append(row)
    if held is not None:
        out.append(held)
    return out
