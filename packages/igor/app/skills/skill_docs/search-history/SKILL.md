---
name: search_history
description: Search the owner's entire past conversation history by keyword and/or date range. Use to recall previous discussions, find an earlier decision, or check whether a topic came up before — beyond the current session's visible window.
---

# Search History

Recall over raw past conversations. Complements the curated memory files: memory holds
distilled facts, this searches what was actually said.

## Parameters

| Param | Purpose |
|-------|---------|
| `query` | Keyword or phrase to match in message text. Omit to filter by date alone. |
| `after` | Only messages on/after this date (`YYYY-MM-DD`). |
| `before` | Only messages on/before this date (`YYYY-MM-DD`). |
| `limit` | Max matching messages (default 20, max 50). |

## Examples

What did we say about the launcher script?
```json
{ "query": "launcher" }
```

Everything from a specific week:
```json
{ "after": "2026-05-24", "before": "2026-05-31" }
```

Find a past decision about streaming:
```json
{ "query": "streaming", "limit": 10 }
```

Results are grouped by conversation, newest first, with session title and date.

## When to use

- The owner refers to something from before ("like we discussed", "that thing last week")
- You need to verify whether a topic or decision already happened
- Building a briefing that should reference prior context

Do not use for facts already captured in memory files — read those directly instead.
