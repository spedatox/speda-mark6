---
name: web-research
description: Search, read difficult pages and extract source documents; load when ordinary search results do not resolve the task.
---

## Decision policy

**Search tool priority** — always follow this order:
1. **Tavily** — primary for all web search, current events, quick lookups
2. **Exa** — fallback only when Tavily returns insufficient results, or for deep semantic/research queries where finding conceptually similar content matters
Never call both for the same query. Tavily first, Exa only if Tavily comes up short.
If a search finds the right page but its contents remain unreadable, choose an
appropriate reading method below instead of repeating the search.

**News is the one exception.** If you have the News Desk tools (see
Capabilities — `news_headlines`, `news_deep_dive`, `read_article`), they come
BEFORE Tavily for anything news-related: headlines, "what's happening",
gündem, breaking events. They're free, deduplicated and purpose-built; Tavily
is a generic web search and pulls stale or duplicate results for this case.
Reach for Tavily on a news query only when the News Desk tools aren't
available to you, or `news_headlines` genuinely doesn't cover it.

### Reading difficult pages

Search and fetch speak plain HTTP and believe whatever the server says. That is
fast and sufficient for most pages. Choose another method only when it is likely
to answer the remaining question. The options below are not a mandatory retry
sequence. Report an access limitation accurately; never reconstruct unread
content from snippets.

1. **Tavily / Exa / `fetch` / `read_article`** — the default. Milliseconds.
2. **`browse_page`** (via `tool_search`) when step 1 comes back empty,
   truncated, an "enable JavaScript" stub, a cookie wall, a challenge page, or
   a 403 that a real browser wouldn't get. It runs actual Chromium: JS executes,
   the page renders, iframes are read. This is the single most common thing you
   are not reaching for often enough.
3. **`browser_act`** when reading isn't enough — the content is behind a click,
   a form, a search box, a menu, a "load more", or a file the site only hands
   over after you ask for it. Downloads it captures reach the owner as files.
4. **`run_command`** for anything the browser renders but can't hand you as
   text — **PDFs above all**. Chromium draws a PDF into a viewer, so
   `browse_page` on a `.pdf` URL returns nothing useful. Download and extract
   it in the sandbox instead:
   `pip install -q pypdf` then urllib to fetch the bytes and
   `pypdf.PdfReader(...).pages[i].extract_text()`. Same route for a CSV, a zip,
   an Excel file, or anything else that needs parsing rather than reading.

**A paywall or a login wall is a different failure from a broken page.** If the
site is one of the owner's saved portals, `portal_login` gets in. If it isn't,
say plainly that it needs an account — don't keep retrying, and don't invent a
workaround.

**The owner's own accounts** — his student automation and any other site he has
saved are reachable: `portal_login` signs in, then `browse_page` and `browser_act`
work inside it. You name the portal; the backend supplies the credential. NEVER ask
him to type a password to you, and never accept one if he offers — if a portal is
not set up, tell him to add it in Settings, Connections, Web portals.
