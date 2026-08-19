"""
Web watch — "tell me when this page publishes something", without a model.

Same shape as services/mail_watch.py, and for the same reason: n8n polls a
deterministic endpoint on a cron, and only a real change is allowed to cost an
agentic turn. Fetching a page and diffing it against last time is not a
reasoning problem — either a line appeared that was not there before, or it did
not.

Why a line-level diff and not a page hash:

  A hash answers "something changed", which on a real university page is true
  every poll — a rotating banner, a visitor counter, a CSRF token. The owner
  does not want to hear about those; they want the sentence that appeared. So
  the previous text is kept and the scan reports the ADDED lines. Boilerplate
  that never changes never shows up in that set, which is what makes the signal
  usable without a model to sort it out. It also means the trigger payload can
  carry the new lines themselves, so the agent does not have to re-fetch and
  re-read the whole page to say what was published.

Anchors are rendered as "text [href]" before diffing, because on exam-result and
academic-calendar pages the publication IS a new link — usually to a PDF. Losing
the href would reduce "2025-2026 Akademik Takvim [/files/takvim.pdf]" to a bare
title with no way to reach it.

Turkish folding comes from app/news/dedup.py so `look_for: "sınav sonuç"` matches
"SINAV SONUÇLARI" — the same normalization the news pipeline already agrees on.

Exactly-once mirrors the mail watch: the scan does NOT commit what it saw, it
parks it as `pending`; n8n calls /web/watch/ack after the trigger was accepted.
A failed notify therefore leaves the old snapshot in place and the next poll
reports the same publication again, instead of losing it silently.

When the plain fetch finds nothing readable, the scan renders the page once in
the browser container (services/browser.py) rather than returning a permanent
error. This is the one place a probe is allowed to become expensive, and it is
worth it: the pages a watch exists for — exam results, an academic calendar —
are exactly the ones most likely to draw themselves client-side, so before this
the watch the owner cared most about was the watch that silently never worked.
lines_from_render() produces the same line shape as extract_lines() on purpose;
one snapshot is fed by both, and a watch that renders once and fetches the next
time must not report the whole page as new because its two extractors disagreed
about whitespace.
"""

import hashlib
import html as html_mod
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.core.runtime_state import (
    drop_web_watch,
    get_web_watch,
    get_web_watches,
    set_web_watch,
)
from app.news.dedup import normalize_text

logger = logging.getLogger(__name__)

# Cap on the stored snapshot. runtime_state.json is rewritten in full on every
# save, so an unbounded snapshot of a big page would turn every settings toggle
# in the app into a multi-megabyte write.
_SNAPSHOT_CHARS = 60_000

# Ceiling on what a single scan hands to the agent. A site-wide redesign makes
# every line "added"; there is no reading a thousand of them and no point paying
# to try.
_MAX_ADDED = 40

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 Speda-WebWatch/1.0"
)

# <head> goes too: its <title> is captured separately by page_title(), and left
# in the body text it would count as a content line — which both hides a
# JS-rendered page from the "no readable text" guard below and turns a site that
# stamps a counter into its title into a false publication every poll.
_DROP_BLOCKS = re.compile(
    r"<(head|script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>", re.I | re.S
)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_ANCHOR = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a\s*>", re.I | re.S)
_BREAKS = re.compile(r"</?(br|p|div|li|tr|h[1-6]|section|article|td|th)\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.I | re.S)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def page_title(html: str) -> str:
    match = _TITLE.search(html or "")
    return re.sub(r"\s+", " ", html_mod.unescape(match.group(1))).strip()[:200] if match else ""


def extract_lines(html: str, *, ignore: str = "") -> list[str]:
    """HTML → the list of visible text lines, anchors kept as "text [href]".

    Dependency-free on purpose: the container has httpx but no bs4/lxml, and
    pulling in a parser for what is fundamentally "strip tags, keep links" would
    add a build dependency to every deploy for no accuracy this use needs.
    """
    text = _COMMENTS.sub(" ", html or "")
    text = _DROP_BLOCKS.sub(" ", text)
    # Anchors first — after _TAGS runs the href is gone for good.
    text = _ANCHOR.sub(lambda m: f" {_TAGS.sub(' ', m.group(2))} [{m.group(1)}] ", text)
    text = _BREAKS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = html_mod.unescape(text)

    noise = re.compile(ignore, re.I) if ignore else None
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", raw).strip()
        # One- and two-character fragments are punctuation left behind by the
        # tag strip, not content.
        if len(line) < 3:
            continue
        if noise and noise.search(line):
            continue
        lines.append(line[:500])
    return lines


def lines_from_render(page: dict, *, ignore: str = "") -> list[str]:
    """The same list of lines, from a BROWSER render instead of raw HTML.

    Deliberately produces the same shape as extract_lines — visible text, with
    anchors as "text [href]" — because the two feed one snapshot. A watch that
    fell back to the browser once and to plain HTTP the next poll must not
    report the whole page as new because the two extractors disagreed about
    formatting.
    """
    noise = re.compile(ignore, re.I) if ignore else None
    lines: list[str] = []
    seen: set[str] = set()

    def keep(raw: str) -> None:
        line = re.sub(r"[ 	 ]+", " ", raw).strip()
        if len(line) < 3 or (noise and noise.search(line)) or line in seen:
            return
        seen.add(line)
        lines.append(line[:500])

    for raw in (page.get("text") or "").splitlines():
        keep(raw)
    for link in page.get("links") or []:
        label = (link.get("text") or "").strip()
        href = (link.get("href") or "").strip()
        if label and href:
            keep(f"{label} [{href}]")
    return lines


async def render_lines(url: str, *, ignore: str = "") -> tuple[list[str], str, str]:
    """Plan B for a probe: render the page and extract the same lines.

    Returns (lines, title, error). Never raises — this is reached from a code
    path whose entire contract is that a dead site does not become a failed
    workflow run.
    """
    from app.services import browser as browser_svc

    if not browser_svc.configured():
        return [], "", "browser not configured"
    try:
        page = await browser_svc.render(url, wait_ms=1200)
    except Exception as exc:  # noqa: BLE001
        return [], "", str(exc)[:200]
    if page.get("error"):
        return [], "", str(page["error"])[:200]
    return lines_from_render(page, ignore=ignore), (page.get("title") or ""), ""


def fingerprint(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def matches_terms(line: str, terms: list[str]) -> bool:
    """Turkish-aware keyword test. No terms configured = everything matches."""
    if not terms:
        return True
    haystack = normalize_text(line)
    return any(normalize_text(t) in haystack for t in terms if t.strip())


def _parse_terms(look_for: str | list[str]) -> list[str]:
    if isinstance(look_for, list):
        return [t for t in look_for if t and t.strip()]
    return [t.strip() for t in (look_for or "").split(",") if t.strip()]


async def scan(
    *,
    watch_id: str,
    url: str,
    look_for: str | list[str] = "",
    ignore: str = "",
    max_added: int = _MAX_ADDED,
    timeout: float = 25.0,
) -> dict:
    """Fetch `url` and report what appeared since the last acknowledged scan.

    status is one of:
      ok        — the scan ran; `changed` says whether anything new appeared
      baseline  — first ever sight of this page; recorded, deliberately silent
      error     — the fetch failed; `detail` carries it

    Never raises: n8n polls this on a schedule and a dead site must not become a
    failed workflow run every few minutes.
    """
    terms = _parse_terms(look_for)
    lines: list[str] = []
    title = ""
    rendered = False
    http_error = ""

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        lines = extract_lines(resp.text, ignore=ignore)
        title = page_title(resp.text)
    except Exception as exc:  # noqa: BLE001
        http_error = str(exc)[:300]
        logger.warning("web_watch_fetch_failed", extra={"watch_id": watch_id, "error": http_error})

    # ── Plan B ───────────────────────────────────────────────────────────────
    # An empty extraction is almost always a JS-rendered page or a block page,
    # not a page that genuinely went blank — and until the browser existed, that
    # was where this ended: a permanent "error" on a watch the owner had every
    # reason to think was working, for exactly the pages (exam results, an
    # academic calendar behind a React shell) that a watch is for. So the probe
    # renders it once. That is more expensive than a GET and still nothing next
    # to a turn, which is the boundary this whole file is built around.
    if not lines and settings.browser_fallback_enabled:
        lines, browser_title, browser_error = await render_lines(url, ignore=ignore)
        rendered = bool(lines)
        title = title or browser_title
        if not lines:
            detail = http_error or "no readable text (JS-rendered or blocked?)"
            if browser_error:
                detail = f"{detail}; browser fallback also failed: {browser_error}"
            return {"status": "error", "detail": detail, "changed": False,
                    "watch_id": watch_id, "url": url, "added": [], "added_count": 0}
        logger.info("web_watch_rendered", extra={"watch_id": watch_id, "lines": len(lines)})

    if not lines:
        # Committing an empty scan as the new snapshot would make every line of
        # the real page "new" on recovery, so it stays an error either way.
        return {"status": "error",
                "detail": http_error or "no readable text (JS-rendered or blocked?)",
                "changed": False, "watch_id": watch_id, "url": url,
                "added": [], "added_count": 0}

    print_ = fingerprint(lines)
    snapshot = "\n".join(lines)[:_SNAPSHOT_CHARS]
    state = get_web_watch(watch_id)

    if not state.get("snapshot"):
        set_web_watch(watch_id, {"fingerprint": print_, "snapshot": snapshot,
                                 "pending": None, "updated_at": _now(), "url": url})
        logger.info("web_watch_baseline", extra={"watch_id": watch_id, "lines": len(lines)})
        return {"status": "baseline", "changed": False, "watch_id": watch_id, "url": url,
                "title": title, "fingerprint": print_, "added": [], "added_count": 0,
                "rendered": rendered,
                "detail": "first scan — snapshot recorded, nothing reported"}

    previous = set(state["snapshot"].split("\n"))
    added = [l for l in lines if l not in previous]
    kept = [l for l in added if matches_terms(l, terms)]
    removed = sum(1 for l in previous if l not in set(lines))

    # Park it. Committing here instead would lose the publication if the trigger
    # call that follows fails — see the module docstring.
    state["pending"] = {"fingerprint": print_, "snapshot": snapshot}
    state["url"] = url
    state["scanned_at"] = _now()
    set_web_watch(watch_id, state)

    changed = bool(kept)
    logger.info(
        "web_watch_scanned",
        extra={"watch_id": watch_id, "added": len(added), "matched": len(kept),
               "removed": removed, "changed": changed},
    )
    return {
        "status": "ok",
        "changed": changed,
        "watch_id": watch_id,
        "url": url,
        "title": title,
        "fingerprint": print_,
        "added": kept[:max_added],
        "added_count": len(kept),
        "truncated": len(kept) > max_added,
        "removed_count": removed,
        "matched_terms": terms,
        # Which half answered. Worth reporting: a watch that quietly started
        # costing a browser render every ten minutes is a thing the owner should
        # be able to see, not deduce from the container's CPU graph.
        "rendered": rendered,
    }


def ack(watch_id: str, fingerprint_: str) -> dict:
    """Promote the parked snapshot to the committed one, so the next scan diffs
    against what Speda has actually been told about.

    The fingerprint must match the pending one: if the page moved on between the
    scan and this call, acknowledging blind would silently skip whatever landed
    in between.
    """
    state = get_web_watch(watch_id)
    pending = state.get("pending") or {}
    if not pending:
        return {"status": "noop", "detail": "nothing pending", "watch_id": watch_id}
    if fingerprint_ and pending.get("fingerprint") != fingerprint_:
        logger.warning("web_watch_ack_stale", extra={"watch_id": watch_id})
        return {"status": "stale", "watch_id": watch_id,
                "detail": "page changed again since that scan — not committing"}

    set_web_watch(watch_id, {
        "fingerprint": pending["fingerprint"],
        "snapshot": pending["snapshot"],
        "pending": None,
        "url": state.get("url", ""),
        "updated_at": _now(),
    })
    logger.info("web_watch_acked", extra={"watch_id": watch_id})
    return {"status": "ok", "watch_id": watch_id, "fingerprint": pending["fingerprint"]}


def list_watches() -> list[dict]:
    """What Igor actually holds snapshots for — the owner edits the watch LIST
    in n8n, so this is the only way to see the other side of that and spot
    watch_ids left behind by a renamed or deleted entry."""
    out = []
    for watch_id, state in sorted(get_web_watches().items()):
        out.append({
            "watch_id": watch_id,
            "url": state.get("url", ""),
            "lines": len((state.get("snapshot") or "").split("\n")) if state.get("snapshot") else 0,
            "updated_at": state.get("updated_at", ""),
            "scanned_at": state.get("scanned_at", ""),
            "pending": bool(state.get("pending")),
        })
    return out


def reset(watch_id: str) -> dict:
    """Forget a watch's snapshot. The next scan re-baselines it silently rather
    than reporting the whole page as new — which is exactly what you want after
    changing `ignore`, or when a site redesign has made the diff meaningless."""
    existed = drop_web_watch(watch_id)
    return {"status": "ok" if existed else "noop", "watch_id": watch_id}
