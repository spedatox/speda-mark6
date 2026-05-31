---
name: memory
description: Persistent memory filesystem. Read and write markdown files under /memories that survive across conversations. Use to recall the owner's projects, preferences, and history, and to record durable new facts. Always relevant for continuity across sessions.
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

## Default files

- `owner.md` — who the owner is, how he communicates (always preloaded)
- `projects.md` — active projects and their status
- `preferences.md` — explicit standing instructions and preferences
- `log.md` — rolling session log, most recent first

## Examples

Read the projects file before discussing work:
```json
{ "command": "view", "path": "/memories/projects.md" }
```

Update a fact in place (never append a duplicate):
```json
{ "command": "str_replace", "path": "/memories/preferences.md",
  "old_str": "Prefers email drafts in formal tone",
  "new_str": "Prefers email drafts in a direct, concise tone" }
```

Record a new project:
```json
{ "command": "create", "path": "/memories/projects.md", "file_text": "# Active Projects\n\n## ..." }
```

## Rules

- Keep files coherent, current, and free of redundancy. Prefer `str_replace` over appending.
- Never store secrets, credentials, or API keys.
- Do not announce routine memory operations — update silently and continue.
- All paths must start with `/memories`.
