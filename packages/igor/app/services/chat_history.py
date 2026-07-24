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
            return {
                'tools': block.get('tools', []),
                'files': block.get('files', []),
                'uploads': block.get('uploads', []),
            }
    return {}


def rows_from_messages(messages: list[Message]) -> list[dict]:
    """Shape stored user/assistant Message rows into the plain dicts the UI
    renders for a session's message list."""
    out = []
    for m in messages:
        if m.role not in ('user', 'assistant'):
            continue
        meta = _extract_meta(m.content)
        # For a user turn with document uploads, the real text blocks hold the
        # extracted file contents; the bubble must show only the user's own
        # message, which was stashed in the meta block at save time.
        if m.role == 'user' and meta.get('uploads'):
            content_text = meta.get('text', '')
        else:
            content_text = _extract_text(m.content)
        row = {
            'id': str(m.id),
            'role': m.role,
            'content': content_text,
            'tools': meta.get('tools', []),
            'isStreaming': False,
            'isError': False,
        }
        if (imgs := _extract_images(m.content)):
            row['images'] = imgs
        if meta.get('files'):
            row['files'] = meta['files']
        if meta.get('uploads'):
            row['uploads'] = meta['uploads']
        out.append(row)
    return out
