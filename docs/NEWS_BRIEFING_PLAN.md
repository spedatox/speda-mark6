# News Briefing — Two-Tier RSS + NewsData.io Intelligence Desk

**Goal:** Give SPEDA a professional news desk. Tier 1 is an always-on, zero-cost
RSS watcher over curated Turkish + English outlets: breaking-news keyword
triggers, deduped headline store, daily briefing feed. Tier 2 is NewsData.io as
the analyst — corroboration, related-article timelines, historical/category
queries — behind a strict 200-req/day quota ledger. RSS degradation-proof on the
bottom, targeted deep-dive on top.

**Repo:** `C:\Users\AREL TARIM\speda-mark6` (`packages/igor`). CLAUDE.md rules
apply — especially: **n8n is the sole scheduler** (no internal cron, ever),
Rule 1 (no logic in routers), Rule 9 (retrieval skills read-only annotated),
Rule 11 (3–4 sentence tool descriptions).

---

## 0. Established facts — do NOT re-derive these

1. **Scheduling already has one owner: n8n.** The repo contains a full n8n
   workflow composer (`app/automations/composer.py`) with pinned node types —
   including `n8n-nodes-base.scheduleTrigger` and even
   `n8n-nodes-base.rssFeedReadTrigger` — and an `AutomationManager`
   (`app/automations/manager.py`) that creates/activates workflows via
   `N8nClient`. Composed workflows terminate in an HTTP node calling
   `POST /trigger/{agent_id}` with `output_mode:"push"`, and the orchestrator
   composes the owner-facing message (Telegram delivery already wired). The
   news system must NOT introduce any other timer.

2. **But per-item LLM turns are the wrong shape for Tier 1.** Polling ~30–50
   feeds every 5–15 minutes and deduping cannot be an orchestrator run per fire
   (cost, latency, noise). The correct split: **backend owns a non-LLM collector
   endpoint; n8n owns the clock.** n8n cron → `POST /news/poll` (pure Python:
   fetch, parse, dedup, store, keyword-match) → only a keyword HIT escalates to
   `POST /trigger/{agent}` and becomes an LLM turn + push.

3. **Skill patterns to copy:** `app/skills/osint.py` — every retrieval skill is
   `read_only = True`, `requires_network = True`, long descriptive docstrings,
   returns explanatory strings on every failure (never raises). Registration in
   `main.py` Tier 1 block. API keys live in `app/config.py` settings +
   `.env.example` + optionally `config_schema.py` for the Settings UI.

4. **Dependencies:** `packages/igor/pyproject.toml` has `httpx` but **no**
   `feedparser` / `trafilatura` — both must be added (`feedparser>=6.0`,
   `trafilatura>=1.12`; both pure-python-friendly, Windows-safe).

5. **Persistence:** models live in `app/models/` (one file per table), created
   via SQLAlchemy; `notification.py` exists; `AsyncSessionLocal` is the pattern
   for non-request DB work (see `app/core/dispatch.py`).

6. **Agent ownership (memory: agent domains):** NightCrawler = OSINT / web
   surveillance / research — the watcher persona. SPEDA = the owner's briefing
   voice. Tool allowlists are currently unrestricted (`tool_allowlist = None`),
   so registration alone exposes the tools to everyone; ownership is expressed
   in prompts and in which agent n8n triggers.

7. **The n8n trigger contract** (`CLAUDE.md`): `X-N8N-Secret` +
   `X-API-Key` headers, payload like
   `{"type":"cron","job":"morning_brief","output_mode":"push"}`.

---

## 1. Architecture

```
                      n8n (the only clock)
        ┌────────────────────┬─────────────────────────┐
        │ every 10 min       │ 07:30 daily             │
        ▼                    ▼                         │
POST /news/poll       POST /trigger/speda              │
 (no LLM — pure       {"type":"cron",                  │
  Python collector)    "job":"news_briefing",          │
        │               "output_mode":"push"}          │
        │                    │                         │
        ▼                    ▼                         │
┌───────────────┐     SPEDA orchestrator run           │
│ RSS fetch      │     └─ news_headlines tool ──┐      │
│ parse (feedparser)                            │      │
│ dedup (URL+title hash)                        ▼      │
│ store → news_items                    reads news_items
│ keyword match → watchlist                            │
└──────┬────────┘                                      │
       │ HIT (e.g. "siber", "OSTİM")                   │
       ▼                                               │
POST /trigger/nightcrawler {"type":"news_flash", ...}  │
  → NightCrawler run: news_deep_dive (Tier 2) if       │
    warranted → push notification to owner             │
                                                       │
Tier 2 — NewsData.io (news_deep_dive tool, any agent)◄─┘
  corroboration / related timeline / historical search
  guarded by news_quota ledger (200/day, DB-backed)
```

## 2. New components

### `app/news/` package (the desk — no LLM inside)

- **`feeds.py`** — the curated feed registry: the Turkish outlet table from the
  concept (NTV, Hürriyet, Milliyet, Sabah, Yeni Akit, Daily Sabah, A News,
  Yeni Şafak + Haber7/Star/Takvim) as `(outlet, category, url)` tuples, plus
  owner extras from a `news_extra_feeds` setting (comma-separated URLs). Keep
  per-feed `enabled` state in DB so a dead feed can be muted without a deploy.
- **`collector.py`** — `poll_all()`: concurrent `httpx` GETs (timeout ~10 s,
  per-feed failure isolated — one feed down never fails the poll), parse with
  `feedparser`, normalize items, dedup, insert new rows, run watchlist
  matching, fire escalations. Returns poll stats (fetched/new/flagged) for the
  endpoint response and logs.
- **`dedup.py`** — two-stage: exact URL (canonicalized — strip tracking params)
  then normalized-title similarity hash (lowercase, Turkish-aware fold, strip
  punctuation/stopwords, first-N-token hash). Same story across NTV+Hürriyet+
  Sabah collapses to one item with an `also_in` outlet list — this is what
  protects the Tier-2 budget.

### Models (`app/models/`)

- **`news_item.py`** — id, url (unique), title, title_hash (indexed), outlet,
  category, summary, published_at, fetched_at, also_in (JSON), flagged (bool),
  flagged_keyword. Retention: poll prunes rows older than ~14 days.
- **`news_watch.py`** — keyword watchlist: keyword, created_by (owner|agent),
  active, last_hit_at, hit_count. Case/diacritic-insensitive matching.
- **`news_quota.py`** — Tier-2 ledger: date (UTC), used, per-purpose counters
  (deep_dive / auto_flag / digest). One row per day; the skill refuses politely
  when the day's budget bucket is exhausted (allocations from the concept:
  ~50/50/50 + 50 buffer).

### Router (`app/routers/news.py`) — thin, per Rule 1

- `POST /news/poll` — n8n-called (validates `X-N8N-Secret` like `/trigger`);
  body optional `{prune: true}`. Calls `collector.poll_all()`; returns stats.
- `GET /news/items?since=&flagged=&limit=` — for the UI / debugging.
- `GET /news/watch` / `POST /news/watch` — owner CRUD for keywords (Settings
  UI later; skill covers the chat path now).

### Skills (`app/skills/news.py`) — all Rule-11 descriptions

- **`news_headlines`** *(read-only)* — reads `news_items` (since-hours,
  category, flagged-only, limit). This is what SPEDA calls for the daily
  briefing and "bugün ne oldu?" turns. Returns deduped headlines with outlet
  cross-counts ("also in 3 outlets") — corroboration signal for free.
- **`news_watch`** *(not read-only)* — add/remove/list watchlist keywords, so
  "SPEDA, 'OSTİM' geçen haberleri anında bildir" is one turn.
- **`news_deep_dive`** *(read-only, requires_network)* — Tier 2. NewsData.io
  `/api/1/latest` + `/api/1/archive` wrappers: query, category, country=tr,
  language, from/to dates. Takes a `purpose` enum (`deep_dive`|`auto_flag`|
  `digest`) → checks/increments the matching quota bucket **before** the call;
  on exhausted bucket returns the graceful-degradation message ("Tier-2 quota
  for X is spent today — here is what Tier 1 already has" + a headlines
  fallback). Response trimmed to the fields that matter (title, source, date,
  description, link) within the usual result-size discipline.
- **`read_article`** *(read-only, requires_network)* — free full-text: fetch
  the RSS item's URL with httpx + `trafilatura.extract()`. This is the "don't
  spend an API call for text" escape hatch; description must steer the model to
  prefer it over `news_deep_dive` when it just needs an article's content.

### Config (`app/config.py` + `.env.example` + `config_schema.py`)

- `newsdata_api_key: str = ""` (empty disables `news_deep_dive` with a clear
  message, same pattern as other keyed skills).
- `news_extra_feeds: str = ""`, `news_poll_enabled: bool = True`,
  `news_retention_days: int = 14`, quota split overrides (defaults 50/50/50).

### Escalation path (keyword hit → owner's phone)

On a watchlist hit, the collector POSTs internally to the existing trigger
flow for **NightCrawler** with
`{"type":"news_flash","keyword":...,"item":{title,url,outlet},"output_mode":"push"}`
— reusing `/trigger`'s machinery (in-process call to the same handler, not an
HTTP loopback). NightCrawler's turn decides: is this worth the owner's
attention → optionally one `news_deep_dive(purpose="auto_flag")` for
corroboration → push notification via the existing delivery path. Rate-guard:
one escalation per keyword per ~2 h (cooldown column on `news_watch`) so a
developing story doesn't fire 20 pushes.

### n8n workflows (created via the existing AutomationManager, or manually)

1. **`news_poll`** — scheduleTrigger every 10 min → HTTP `POST /news/poll`.
   (Deliberately NOT the n8n RSS trigger node: dedup across 30+ feeds and the
   watchlist live in the backend; n8n only ticks.)
2. **`news_briefing`** — daily 07:30 → `POST /trigger/speda`
   `{"type":"cron","job":"news_briefing","output_mode":"push"}`. SPEDA's run
   calls `news_headlines(since_hours=24)`, composes the briefing (headlines +
   1-liners, flagged items first, grouped by category), optionally ONE
   `news_deep_dive(purpose="digest")` per tracked topic, and pushes it.

### Prompts (small)

- Add a short "news desk" paragraph to NightCrawler's identity prompt (it owns
  the watch + flash judgement) and one line to SPEDA's briefing-relevant shared
  section describing the two tiers and the quota discipline ("prefer
  news_headlines and read_article; news_deep_dive is budgeted").

## 3. Build order (respects CLAUDE.md phase discipline)

| Step | What | Depends on |
|------|------|-----------|
| 1 | deps (`feedparser`, `trafilatura`) + config settings | — |
| 2 | models: `news_item`, `news_watch`, `news_quota` | — |
| 3 | `app/news/` (feeds, dedup, collector) + unit tests | 1, 2 |
| 4 | `routers/news.py` + auth wiring | 3 |
| 5 | skills (`news_headlines`, `news_watch`, `news_deep_dive`, `read_article`) + registration in `main.py` Tier-1 block | 2, 3 |
| 6 | escalation path (collector → in-process trigger for NightCrawler, cooldowns) | 4, 5 |
| 7 | prompt additions; n8n `news_poll` + `news_briefing` workflows | 5, 6 |

## 4. Acceptance — all must pass

1. `POST /news/poll` against the real feed list completes < ~20 s, inserts
   deduped rows; a second immediate poll inserts ~0 (dedup holds); one dead
   feed URL in the registry does not fail the poll (logged, skipped).
2. The same story from 3 outlets lands as ONE `news_item` with `also_in` = 2.
3. Adding watchlist keyword "siber" via chat, then a poll ingesting a matching
   headline → exactly one NightCrawler push turn fires; a second matching item
   within the cooldown does not re-fire.
4. `news_deep_dive` returns structured NewsData results; with the day's
   `digest` bucket exhausted it refuses gracefully and offers Tier-1 data; the
   quota row survives restart (DB-backed, resets by UTC date naturally).
5. `read_article` extracts readable text from at least NTV + Hürriyet + Sabah
   article pages.
6. A simulated `{"job":"news_briefing"}` trigger produces a pushed briefing
   composed from stored items — zero NewsData calls when no topic digest is
   configured.
7. No scheduler code anywhere in the backend — `grep -ri "cron\|schedule" app/`
   shows only n8n composer references. `pytest` green.

## 5. Risks & guardrails

- **Feed rot:** outlets change RSS paths silently. Per-feed failure counters →
  auto-disable after N consecutive failures (logged WARN), owner sees it in
  `GET /news/items` stats / Settings later. Never let one 404 loop spam logs.
- **Encoding:** Turkish feeds mix UTF-8/ISO-8859-9; feedparser handles most —
  normalize aggressively in dedup, and never trust `<pubDate>` timezones
  (fall back to fetched_at).
- **Quota honesty:** the ledger increments BEFORE the HTTP call (a failed call
  still consumed a request upstream in most 4xx cases; refund only on connect
  errors).
- **Push fatigue:** cooldowns + flagged-first briefing ordering are the
  defense; never escalate unflagged items automatically.
- **trafilatura on paywalls/JS pages:** returns None → skill answers "couldn't
  extract; here is the summary + link" — never an exception.
- **CLAUDE.md:** no model IDs anywhere in `app/news/`; escalation turns are
  `triggered_by="n8n"`-equivalent through the existing trigger path (no new
  `triggered_by` value).
