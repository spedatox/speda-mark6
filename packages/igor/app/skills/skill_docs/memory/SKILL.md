---
name: memory
description: Read a specific persisted owner-memory document or list its directory when the small standing context and per-turn recall do not contain the needed detail. Use evidence-bound domain tools for updates; raw memory edits are disabled.
---

# Memory documents

`/memories` is a virtual filesystem of owner knowledge. The system prompt carries
a small standing set and may add up to two relevant documents for the current
message. The directory listing shows paths without loading every file. A document
absent from the prompt is still available through `memory` with `command: view`.

Read the exact topic or entity before relying on detail that was not recalled.
Ultron can use `read_course_memory` to list records or open one course and term;
attendance and timetable remain separate structured data. For a person or project,
open its individual path rather than the whole collection.

```json
{"command":"view","path":"/memories/projects/speda-mark-vi.md"}
```

`view_range` can limit a large file. If the expected document is missing, use
`search_memory` for sourced observations or `recall_conversations` for earlier
discussion. Do not infer absence of knowledge from a failed automatic recall.

Agent writes through `memory` (`create`, `str_replace`, `insert`, `delete`) are
disabled. Use `record_observation` for durable sourced facts and the designated
evidence-bound domain tool for a managed document update. Do not edit derived
files such as `current.md` directly. Never store credentials or invented claims.
