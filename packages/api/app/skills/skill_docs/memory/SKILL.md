---
name: memory
description: Persistent memory filesystem. Read and write markdown files under /memories that survive across conversations. Use to recall the owner's current state, projects, people, and history, and to file durable new facts into the one correct canonical file. Always relevant for continuity across sessions.
---

# Memory

A virtual filesystem of markdown files under `/memories`, persisted across every conversation.
This is how you maintain continuity — knowing the owner without being re-told each session.

The directory listing and `owner.md` are preloaded into your system prompt every turn. Read
other files on demand; write back when you learn something durable.

## Commands

| Command | Purpose |
|---------|---------|
| `view` | List a directory, or read a file (with line numbers). Optional `view_range: [start, end]`. |
| `create` | Create a new file. Errors if it already exists. |
| `str_replace` | Replace a unique string in a file (`old_str` must appear exactly once). |
| `insert` | Insert `insert_text` after line `insert_line` (0 = top of file). |
| `delete` | Delete a file. |

## The canonical files (closed set — one question each)

- `current.md` — what is true in the owner's life RIGHT NOW (preloaded; outranks
  every other file for the present tense)
- `owner.md` — who he is + his biography BEFORE Mark VI existed; identity
  constants (name, address forms). Updated in place, never demoted. (preloaded)
- `dossier.md` — observed preferences: what he likes, dislikes, wants and in what
  manner. LEARN from it and act on it silently; never cite it. (preloaded)
- `history.md` — things that began and ended during Mark VI's watch (since
  2026-05) and no longer apply — demotions only. (preloaded)
- `projects.md` — what he's building and where each effort stands
- `social.md` — people who matter: each has a Who block + timestamped Events log
- `sessions.md` — the gym log, day by day (Atomix writes it)
- `log.md` — rolling one-line session summaries

The **epoch line** (Mark VI's birth, 2026-05) divides owner.md from history.md:
pre-Mark-VI context → owner.md; Mark-VI-era states that have ended → history.md.

Do not create files outside this set. File a new fact into the ONE file that fits
(see the routing rules in your memory protocol). Hygiene across files is Orion's
job, not yours — don't reorganise other files in passing.

## Examples

Read the projects file before discussing work:
```json
{ "command": "view", "path": "/memories/projects.md" }
```

Update a fact in place (never append a duplicate):
```json
{ "command": "str_replace", "path": "/memories/current.md",
  "old_str": "Cutting for the wedding (as of 2026-06-20)",
  "new_str": "Cutting for the wedding (as of 2026-07-06)" }
```

Add a person section (Who block + Events log), or append an event to one:
```json
{ "command": "str_replace", "path": "/memories/social.md",
  "old_str": "**Events:**",
  "new_str": "**Events:**\n- [2026-07-06] Venue confirmed" }
```

File an observed preference into the dossier (attributed + dated):
```json
{ "command": "str_replace", "path": "/memories/dossier.md",
  "old_str": "## Dislikes / friction",
  "new_str": "## Dislikes / friction\n- [2026-07-06, sentinel] dislikes padding before the answer" }
```

## Rules

- Keep files coherent, current, and free of redundancy. Prefer `str_replace` over appending.
- Every write is versioned automatically; the owner can review and roll it back.
- Never store secrets, credentials, or API keys.
- Do not announce routine memory operations — update silently and continue.
- All paths must start with `/memories`.
