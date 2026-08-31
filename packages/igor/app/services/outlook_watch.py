# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Outlook watch — the cheap, LLM-free half of "tell me when the university writes".

The exact posture of services/mail_watch.py, pointed at the owner's Microsoft 365
mailbox instead of Gmail: n8n polls `POST /outlook/watch/scan` on a cron, the scan
is one Graph request plus a strict sender-domain check, and the expensive half —
`POST /trigger/ultron` — fires only on a real hit. A quiet week costs nothing but
HTTP.

Two things differ from the Gmail probe, both forced by Graph rather than chosen:

  * **Filtering is client-side.** Gmail's `from:` operator is a server-side
    prefilter we then tighten; Graph offers `$search` (KQL, no `$filter` allowed
    alongside it) or `$filter` (no `endswith` on message sender addresses in v1.0).
    So the scan asks for the last N messages by receivedDateTime — a bounded,
    deterministic window — and does the domain test here. `domain_matches` is
    imported from mail_watch, not reimplemented: "is this really from them" has
    exactly one answer in this codebase.
  * **The seen-marker is a category, not a label.** Same properties that made a
    Gmail label the right choice — visible in the owner's own mailbox, undoable
    there, and not resurrected by wiping Igor's DB — and categories are what
    Outlook offers. The scan never marks its own findings; n8n acks via
    `/outlook/watch/seen` only AFTER the trigger was accepted, so a failed notify
    repeats next poll instead of vanishing.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.mcp.microsoft_rest import (
    GRAPH_API,
    LIST_SELECT,
    message_text,
    microsoft_access_token,
    microsoft_api_request,
    sender_address,
)
from app.services.mail_watch import domain_matches

logger = logging.getLogger(__name__)

# Outlook categories are free-text, but only categories defined in the mailbox's
# master list get a colour. An undefined one still applies and still filters —
# it just shows uncoloured, which is a cosmetic issue and not a correctness one.
# NOT renamed with the rest of the SPEDA -> Speda sweep: this is a live Outlook
# category already applied to real messages. Changing the string here would
# make the app stop recognizing mail it already marked seen and re-trigger on it.
SEEN_CATEGORY = "SPEDA-Seen"

# Graph caps $top at 1000; this is a notifier, not an importer. If more than 50
# messages land between polls the owner has a bigger signal than any one of them.
_MAX_WINDOW = 50


def _since(newer_than_days: int) -> str:
    """RFC3339 lower bound for the scan window."""
    days = max(0, int(newer_than_days))
    return (datetime.now(timezone.utc) - timedelta(days=days or 1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


async def scan(
    *,
    domain: str,
    max_results: int = 10,
    newer_than_days: int = 2,
    unread_only: bool = False,
    include_body: bool = True,
    body_chars: int = 2000,
    category: str = SEEN_CATEGORY,
    folder: str = "",
) -> dict:
    """Look for unseen mail from `domain` in the Microsoft mailbox. Returns a
    status envelope, never raises.

    status is one of:
      ok            — the scan ran; `count` may be 0 (the common case)
      disconnected  — no usable Microsoft token; the owner must re-authorise
      error         — Graph answered with a failure; `detail` carries it

    n8n fires the agent only when count > 0, and surfaces disconnected/error on
    the EDGE rather than every poll — a watch that has gone blind forever is the
    failure worth alerting on, not the fact that it is 03:40 and still blind.
    """
    token = await microsoft_access_token()
    if not token:
        logger.warning("outlook_watch_disconnected", extra={"domain": domain})
        return {"status": "disconnected", "count": 0, "message_ids": [], "messages": []}

    limit = max(1, min(int(max_results), 25))
    # Ask for more than we will return: the domain and category tests happen
    # here, so the window has to be wide enough to still contain `limit` real
    # hits after both have thinned it.
    window = min(max(limit * 3, 20), _MAX_WINDOW)

    base = f"{GRAPH_API}/me/mailFolders/{folder}/messages" if folder else f"{GRAPH_API}/me/messages"
    # receivedDateTime leads both $filter and $orderby — Graph rejects an orderby
    # property that isn't first in the filter with InefficientFilter.
    filters = [f"receivedDateTime ge {_since(newer_than_days)}"]
    if unread_only:
        filters.append("isRead eq false")
    params = {
        "$select": LIST_SELECT,
        "$filter": " and ".join(filters),
        "$orderby": "receivedDateTime desc",
        "$top": window,
    }

    listing = await microsoft_api_request("GET", base, token, params=params)
    if listing.status_code != 200:
        logger.error(
            "outlook_watch_search_failed",
            extra={"domain": domain, "status": listing.status_code},
        )
        return {
            "status": "error",
            "detail": f"Graph returned {listing.status_code}: {listing.text[:200]}",
            "count": 0,
            "message_ids": [],
            "messages": [],
        }

    candidates = listing.json().get("value", [])
    messages, rejected, already_seen = [], 0, 0
    for message in candidates:
        address = sender_address(message)
        if not domain_matches(address, domain):
            rejected += 1
            continue
        if category and category in (message.get("categories") or []):
            already_seen += 1
            continue

        row = {
            "id": message.get("id", ""),
            "thread_id": message.get("conversationId", ""),
            "from": _from_header(message),
            "from_email": address,
            "subject": message.get("subject", "") or "",
            "date": message.get("receivedDateTime", "") or "",
            "snippet": (message.get("bodyPreview") or "")[:300],
            "web_link": message.get("webLink", "") or "",
            "unread": not message.get("isRead", True),
            "has_attachments": bool(message.get("hasAttachments")),
        }
        if include_body:
            row["body"] = await _body(token, row["id"], body_chars)
        messages.append(row)
        if len(messages) >= limit:
            break

    logger.info(
        "outlook_watch_scanned",
        extra={
            "domain": domain,
            "scanned": len(candidates),
            "matched": len(messages),
            "rejected": rejected,
            "already_seen": already_seen,
        },
    )
    return {
        "status": "ok",
        "query": f"from *@{domain.lstrip('@')} since {_since(newer_than_days)}",
        "count": len(messages),
        "message_ids": [m["id"] for m in messages],
        "messages": messages,
    }


def _from_header(message: dict) -> str:
    holder = (message.get("from") or {}).get("emailAddress") or {}
    name, addr = holder.get("name", ""), holder.get("address", "")
    return f"{name} <{addr}>" if name and name != addr else addr


async def _body(token: str, message_id: str, body_chars: int) -> str:
    """Fetch and flatten one message body. A body that won't load is not worth
    failing the whole scan over — the subject and preview still say enough for
    the agent to tell the owner something arrived."""
    if not message_id:
        return ""
    detail = await microsoft_api_request(
        "GET", f"{GRAPH_API}/me/messages/{message_id}", token,
        params={"$select": "body,bodyPreview"},
    )
    if detail.status_code != 200:
        return ""
    return message_text(detail.json())[:body_chars]


async def mark_seen(message_ids: list[str], *, category: str = SEEN_CATEGORY) -> dict:
    """Tag the given messages with the seen category so the next scan skips them.

    Called by n8n only after the trigger was accepted. Graph has no batchModify
    equivalent for categories, so this is one PATCH per message — bounded by the
    scan's own cap, and it never touches the read flag: the owner still finds the
    mail exactly as it arrived, just categorised.
    """
    ids = [i for i in (message_ids or []) if i][:25]
    if not ids:
        return {"status": "ok", "marked": 0, "category": category}

    token = await microsoft_access_token()
    if not token:
        return {"status": "disconnected", "marked": 0, "category": category}

    marked, failures = 0, []
    for message_id in ids:
        current = await microsoft_api_request(
            "GET", f"{GRAPH_API}/me/messages/{message_id}", token,
            params={"$select": "categories"},
        )
        # Read-modify-write: PATCHing `categories` replaces the whole array, so
        # blindly writing [SPEDA-Seen] would erase the owner's own categories.
        existing = current.json().get("categories", []) if current.status_code == 200 else []
        if category in existing:
            marked += 1
            continue
        resp = await microsoft_api_request(
            "PATCH", f"{GRAPH_API}/me/messages/{message_id}", token,
            json={"categories": [*existing, category]},
        )
        if resp.status_code in (200, 204):
            marked += 1
        else:
            failures.append(f"{message_id[:12]}…:{resp.status_code}")

    if failures:
        logger.error(
            "outlook_watch_mark_failed",
            extra={"failed": len(failures), "marked": marked},
        )
        return {
            "status": "error",
            "detail": f"{len(failures)} message(s) could not be categorised: "
                      + ", ".join(failures[:5]),
            "marked": marked,
            "category": category,
        }

    logger.info("outlook_watch_marked", extra={"count": marked, "category": category})
    return {"status": "ok", "marked": marked, "category": category}
