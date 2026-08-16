"""
Mail watch — the cheap, LLM-free half of "tell me when <domain> writes".

n8n polls `POST /mail/watch/scan` on a cron. A scan is one Gmail search plus a
strict sender-domain check on the results: no model, no tokens, no agentic turn.
The expensive half — `POST /trigger/{agent_id}` — fires only on a real hit, so a
mailbox that stays quiet all week costs nothing but HTTP.

This is the same posture as `/academic/ask-pending`: n8n still owns the schedule
(CLAUDE.md — sole scheduling organ), the backend just performs one deterministic
check. There is no reasoning in "did anyone from tdv.org write" — either the
From header ends in that domain or it does not.

Exactly-once is handled with a Gmail label rather than backend state. The scan
query excludes the label, and n8n applies it via `/mail/watch/seen` AFTER the
trigger was accepted, so:
  * a notify that fails leaves the mail unlabelled and it retries next poll;
  * the marker is visible in the owner's mailbox and can be undone there;
  * wiping Igor's DB does not resurrect a month of already-handled mail.

Gmail is used through app/mcp/google_rest.py's public aliases — the owner's
existing "Sign in with Google" connection, one token cache, no second credential
for n8n to hold.
"""

import logging
import re

from app.mcp.google_rest import (
    GMAIL_API,
    gmail_header,
    gmail_text_body,
    google_access_token,
    google_api_request,
)

logger = logging.getLogger(__name__)

# Flat, not nested ("SPEDA/Seen"): a slash makes a nested label whose Gmail
# search term needs quoting, and the query is assembled as a plain string here.
SEEN_LABEL = "SPEDA-Seen"

# Gmail's own cap for a single messages.list page in this use; the watch is a
# notifier, not an importer — if 25 mails arrive between polls the owner has a
# bigger signal than any one of them.
_MAX_RESULTS = 25

_ADDR = re.compile(r"[\w.+-]+@[\w.-]+")


def _sender_address(from_header: str) -> str:
    """The bare address out of a From header (`"TDV Burs" <x@tdv.org>` → x@tdv.org)."""
    match = _ADDR.search(from_header or "")
    return match.group(0).lower() if match else ""


# Headers that can carry the address a message was DELIVERED to. Delivered-To is
# the one that survives forwarding — Gmail stamps it with the mailbox that
# actually received the message — but a plain To/Cc match is the common case and
# X-Forwarded-To turns up on some relays.
_RECIPIENT_HEADERS = ("Delivered-To", "To", "Cc", "X-Forwarded-To", "X-Original-To")


def recipient_matches(headers: list[dict], recipient: str) -> bool:
    """Whether `recipient` is one of the addresses this message was sent to.

    This is the FORWARDING case, and it exists because the sender check cannot
    cover it. When a mailbox is forwarded elsewhere, what reliably identifies the
    mail is not who sent it — Exchange may rewrite From to the forwarding account
    — but the fact that it was addressed to the original mailbox. That address
    survives in Delivered-To/To even after the hop.

    Exact address comparison, not substring: `ahmet@x.edu.tr` must not be matched
    by `notahmet@x.edu.tr`, and Gmail's own `deliveredto:` operator is a
    prefilter with the same fuzziness problem `domain_matches` exists to fix.
    """
    wanted = (recipient or "").strip().lower().lstrip("@")
    if not wanted:
        return False
    for name in _RECIPIENT_HEADERS:
        value = gmail_header(headers, name)
        if not value:
            continue
        if any(addr.lower() == wanted for addr in _ADDR.findall(value)):
            return True
    return False


def domain_matches(address: str, domain: str) -> bool:
    """Strict sender-domain test — the whole point of this module.

    Gmail's `from:tdv.org` is a fuzzy token match: it also hits display names
    containing the string and other addresses that merely tokenise the same way.
    That is fine as a server-side prefilter (it is a superset) but it is not the
    answer to "is this really from them". This is. Subdomains count
    (burs@mail.tdv.org), lookalikes do not (info@nottdv.org).
    """
    address = (address or "").lower()
    domain = (domain or "").lower().lstrip("@").strip(".")
    if not address or not domain:
        return False
    return address.endswith("@" + domain) or address.endswith("." + domain)


def build_query(
    domain: str,
    *,
    recipient: str = "",
    extra_query: str = "",
    newer_than_days: int = 2,
    label: str = SEEN_LABEL,
) -> str:
    """Assemble the Gmail search. Deliberately a superset of what we want —
    precision comes from domain_matches()/recipient_matches(), breadth from here.

    `newer_than` is a floor, not the dedup mechanism: it bounds the scan if the
    label is ever missing or was cleared by hand, so a fresh watch does not fire
    on five years of archived mail on its first tick.

    A `recipient` narrows by who the mail was addressed TO, which is how a
    FORWARDED mailbox is watched: mail from the university arrives in Gmail, and
    what identifies it is the school address it was sent to, not the sender.
    """
    parts = []
    if domain:
        parts.append(f"from:{domain.lstrip('@')}")
    if recipient:
        # Gmail brace syntax = OR. BOTH operators are needed and neither is
        # redundant, because the two delivery shapes put the address in
        # different headers:
        #   direct    Delivered-To: <the address>        → deliveredto: matches
        #   forwarded Delivered-To: <the Gmail account>  → deliveredto: MISSES;
        #             To:           <the address>          only to: matches
        # Using deliveredto: alone is what silently breaks a forwarded mailbox
        # watch — which is the exact case `recipient` exists for. The strict
        # header check in recipient_matches() still decides; this only has to be
        # a superset.
        addr = recipient.lstrip("@")
        parts.append(f"{{to:{addr} deliveredto:{addr}}}")
    if label:
        parts.append(f"-label:{label}")
    if newer_than_days > 0:
        parts.append(f"newer_than:{int(newer_than_days)}d")
    if extra_query:
        parts.append(extra_query)
    return " ".join(parts)


async def scan(
    *,
    domain: str = "",
    recipient: str = "",
    extra_query: str = "",
    max_results: int = 10,
    newer_than_days: int = 2,
    include_body: bool = True,
    body_chars: int = 2000,
    label: str = SEEN_LABEL,
) -> dict:
    """Look for unseen mail from `domain` and/or addressed to `recipient`.
    Returns a status envelope, never raises.

    At least one of `domain` / `recipient` is required — a scan with neither
    would match the entire mailbox. Supplying BOTH ANDs them ("from the
    registrar, to my school address").

    `recipient` is the forwarding case: when the university mailbox is forwarded
    into Gmail, the sender may or may not survive the hop, but the address it was
    delivered to always does.

    status is one of:
      ok            — the scan ran; `count` may be 0 (the common case)
      disconnected  — no usable Google token; the owner must re-authorise
      error         — Gmail answered with a failure; `detail` carries it

    The caller (n8n) fires SPEDA only when count > 0, and should surface
    `disconnected`/`error` on the EDGE rather than every poll — a broken watch
    that goes quiet forever is the failure mode worth alerting on.
    """
    if not (domain or recipient):
        return {
            "status": "error",
            "detail": "a watch needs a domain, a recipient, or both — with neither "
                      "it would match the whole mailbox",
            "count": 0, "message_ids": [], "messages": [],
        }

    token = await google_access_token()
    if not token:
        logger.warning("mail_watch_disconnected", extra={"domain": domain})
        return {"status": "disconnected", "count": 0, "message_ids": [], "messages": []}

    query = build_query(
        domain, recipient=recipient, extra_query=extra_query,
        newer_than_days=newer_than_days, label=label,
    )
    limit = max(1, min(int(max_results), _MAX_RESULTS))

    listing = await google_api_request(
        "GET",
        f"{GMAIL_API}/users/me/messages",
        token,
        params={"q": query, "maxResults": limit},
    )
    if listing.status_code != 200:
        logger.error(
            "mail_watch_search_failed",
            extra={"domain": domain, "status": listing.status_code},
        )
        return {
            "status": "error",
            "detail": f"Gmail search returned {listing.status_code}: {listing.text[:200]}",
            "count": 0,
            "message_ids": [],
            "messages": [],
        }

    ids = [m["id"] for m in listing.json().get("messages", [])]
    messages, rejected = [], 0
    for message_id in ids:
        detail = await google_api_request(
            "GET",
            f"{GMAIL_API}/users/me/messages/{message_id}",
            token,
            params=(
                {"format": "full"}
                if include_body
                else {
                    "format": "metadata",
                    # Delivered-To/Cc are needed for the recipient check; without
                    # them a metadata-only scan silently rejects every forward.
                    "metadataHeaders": [
                        "From", "To", "Cc", "Subject", "Date",
                        "Delivered-To", "X-Forwarded-To", "X-Original-To",
                    ],
                }
            ),
        )
        if detail.status_code != 200:
            continue
        message = detail.json()
        headers = message.get("payload", {}).get("headers", [])
        from_header = gmail_header(headers, "From")
        address = _sender_address(from_header)
        # Both operators Gmail offers here (`from:`, `deliveredto:`) are fuzzy
        # prefilters; these are the strict answers. Every supplied criterion must
        # hold — an AND, so combining them narrows rather than widens.
        if domain and not domain_matches(address, domain):
            rejected += 1
            continue
        if recipient and not recipient_matches(headers, recipient):
            rejected += 1
            continue

        row = {
            "id": message_id,
            "thread_id": message.get("threadId", ""),
            "from": from_header,
            "from_email": address,
            "subject": gmail_header(headers, "Subject"),
            "date": gmail_header(headers, "Date"),
            "snippet": message.get("snippet", ""),
        }
        if include_body:
            row["body"] = gmail_text_body(message.get("payload", {}))[:body_chars]
        messages.append(row)

    logger.info(
        "mail_watch_scanned",
        extra={
            "domain": domain,
            "matched": len(messages),
            "rejected": rejected,
            "query": query,
        },
    )
    return {
        "status": "ok",
        "query": query,
        "count": len(messages),
        "message_ids": [m["id"] for m in messages],
        "messages": messages,
    }


async def mark_seen(message_ids: list[str], *, label: str = SEEN_LABEL) -> dict:
    """Label the given messages so the next scan skips them.

    Called by n8n only after the trigger was accepted. Labelling is one
    batchModify call and never touches the unread flag — the owner still finds
    the mail exactly as it arrived, just tagged.
    """
    ids = [i for i in (message_ids or []) if i][:100]
    if not ids:
        return {"status": "ok", "marked": 0, "label": label}

    token = await google_access_token()
    if not token:
        return {"status": "disconnected", "marked": 0, "label": label}

    label_id = await _ensure_label(token, label)
    if not label_id:
        return {"status": "error", "detail": f"could not resolve label {label!r}",
                "marked": 0, "label": label}

    resp = await google_api_request(
        "POST",
        f"{GMAIL_API}/users/me/messages/batchModify",
        token,
        json={"ids": ids, "addLabelIds": [label_id]},
    )
    if resp.status_code not in (200, 204):
        logger.error(
            "mail_watch_mark_failed",
            extra={"status": resp.status_code, "count": len(ids)},
        )
        return {
            "status": "error",
            "detail": f"batchModify returned {resp.status_code}: {resp.text[:200]}",
            "marked": 0,
            "label": label,
        }

    logger.info("mail_watch_marked", extra={"count": len(ids), "label": label})
    return {"status": "ok", "marked": len(ids), "label": label}


async def _ensure_label(token: str, name: str) -> str | None:
    """Label id for `name`, creating it on first use. Returns None if Gmail
    refuses both the lookup and the creation."""
    listing = await google_api_request("GET", f"{GMAIL_API}/users/me/labels", token)
    if listing.status_code == 200:
        for label in listing.json().get("labels", []):
            if label.get("name", "").lower() == name.lower():
                return label.get("id")

    created = await google_api_request(
        "POST",
        f"{GMAIL_API}/users/me/labels",
        token,
        json={
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    )
    if created.status_code in (200, 201):
        return created.json().get("id")

    logger.error(
        "mail_watch_label_create_failed",
        extra={"name": name, "status": created.status_code},
    )
    return None
