# Lossless memory corpus and maintenance contract

This contract supersedes the abandoned observation-only document regeneration
and the Orion bulk/nightly audit. The normal write tools keep this layout current;
there is no recurring cleanup, re-review, re-extraction or provider call.

## Identity and storage

- Persons/projects have stable `memory_entities` identities, explicit aliases,
  one current head and a stable `record_id`. A recording month never creates a
  second person. Registry updates resolve identity/head before choosing a path.
- Topic/entity files use `/memories/<domain>/<MM-YY>/<topic>.md`, with
  `social/<MM-YY>/<personal|professional>/<person>.md`. The folder represents
  an edition/storage period. Occurrence/effective dates remain separate metadata.
- Owner/history/patterns/log remain stable root documents. Course records stay
  under `academic/courses/<term>/<code>.md`; distinct codes/terms are never
  merged because their notes look similar. Lifecycle states use stable keys.
- Finance facts use `finance/records/<id>.md`. Calculated monthly ledgers use
  `finance/<MM-YY>/ledger.md`; aggregate views have stable paths. Unreconciled
  original month tables live under `finance/legacy/YYYY-MM.md` and are explicitly
  excluded from calculations. Compatibility paths are aliases, not duplicate views.
- `memory_sources` holds full immutable original payloads, original IDs,
  timestamps, raw row envelopes and SHA-256 hashes. Old archive/audit directories
  leave the active filesystem only after exact preservation. Historical material
  remains retrievable; archival uniqueness is not proof of new/current knowledge.
- Replaced domain monoliths (such as `wellness.md` beside `wellness/`) are
  historical sources, excluded from active listings and recall. Exact reads
  remain available with historical warnings. Offline cleanup retires their live
  rows only after matching the complete original payload/hash; unique legacy
  details stay searchable and are never declared reconciled by this operation.
- `memory_passages` indexes source sections and dated entries mechanically.
  Passage IDs survive line reordering/path moves; changed entries preserve their
  prior passage as retired. A mentioned date does not establish a completed event.

## Transactions and retrieval

Document mutation, metadata hash/version, passages, connections, admission receipt
and revision share a transaction. An exact no-op makes no reviewer request;
concurrency conflicts require a reread, not replay of a whole prior payload.
Financial projections update their catalogs atomically with the financial fact.

Observation claims, canonical documents, original conversations and source
editions remain distinct authorities. Graph labels distinguish `about`,
`mentions`, `cites`, `evidenced_by`, `preserves`, `part_of`, `consolidated_into`
and candidate `similar_to`. Co-mention/similarity is never causal proof, a
confirmed interpersonal relationship or permission to delete a claim. Historical
observation repair pseudo-paths and audit/archive revisions are not live documents.

`memory_issues` records unresolved alternatives, source gaps, missing references,
course/code discrepancies and candidate similar claims. Sources with differing
dates/amounts remain unchanged. Document/candidate recall exposes warnings; do
not resolve one by silently choosing the latest write time.

Automatic recall remains bounded: a small owner/current/preferences block,
limited recent-session continuity, and query-specific documents/facts. Dated
dossier editions must still supply binding preferences. Raw memory reads have
a 6,500-character content/line budget, source searches at most six candidates and
4,200 characters, and graph context at most 60 edges/three hops and 5,800
characters. Exact line/character pagination lets the agent retrieve more when
needed without loading the vault into a single tool result. Course readers share
the same paging/warning boundary. No migration calls any provider or embedder.

## Explicit offline migration

Use `scripts.cleanup_memory_corpus` with `--source`, a different `--output`,
`--report` and optional reviewed `--path-mappings` JSON. Never run against a live
WAL file or replace the source. The output must match the source or the same
durable plan; a differing source/mapping/committed unit fails closed.

The runner preserves originals first, consolidates documents in independent
transactions, persists per-unit checkpoints, repairs metadata/aliases, separates
raw finance history from calculated views, indexes passages/connections and
verifies byte parity. Only exact or presentation-equivalent contained blocks are
collapsed in the reading view; originals remain exact in the source capsules.
Distinct descriptions/values are kept and flagged. An interrupted run resumes
missing units. A completed rerun verifies parity without repeating cutover.

Before removing an orphan message embedding from a broken index, the runner
preserves its complete row, text and binary vector as a source capsule. Deleted
observation FTS rows are index debris; the observation records remain unchanged.
Legacy uncommitted captures become `needs_review`; their evidence/control rows
are retained and never replayed automatically. Verification compares every
unmodified table, every existing revision, every original document, and every
surviving/preserved embedding, plus SQLite integrity and foreign keys.

New event intake persists its complete candidate in `memory_capture_payloads`
before reviewer execution. A source idempotency key binds to that exact payload;
different content cannot reuse it. A durable compare-and-swap claims each unit
and preserves its record ID through retries. Only transient connection/timeouts,
408/429/selected 5xx responses and write conflicts can retry, at most three
attempts. Invalid evidence/configuration/400 responses require review. Committed
units return their original receipt without another provider call. A crash while
`reviewing` requires explicit operator reconciliation; it is never auto-replayed.

The source database, output database and private reports/exports stay outside
Git. Deployment requires both compatible code and the validated database; a
local migration copy does not change the production database.

`scripts.apply_owner_memory_review` applies a private, explicit reviewed plan
to a different offline copy. Each unit binds exact before hashes, literal
source quotations and complete replacement text. Original documents and changed
legacy timetable rows become immutable sources; receipts, metadata, passages,
graph and the correction commit together. Completed units return their durable
receipts on resumption. Source/plan changes fail closed. Explicit owner writes
need no model approval; this operator path is never exposed as an agent tool.

Agent course writes preserve exact code, term and heading. A new optional name
must occur literally with its code in source evidence; otherwise use a neutral
code-only record. `record_course_memory` operation `confirm_identity` requires
an explicit owner statement and changes only the heading, preserving all notes.
Generic edits and note append cannot silently rename or translate a course.

State reads return `current`, `review_due`, `expired_unconfirmed` or `closed`
separately from stored status. Bounded current views retain a few labelled
unconfirmed situations rather than only a count. Explicitly central owner
situations can have high salience; this affects selection, never factual truth.
State lists page summaries and select one head per key in a single pass; exact
monthly legacy editions remain updateable. Moved/archived source references
resolve within the same owner; current/state projections cannot prove a state.

## Planned evolution

[MEMORY_EVOLUTION_PLAN.md](MEMORY_EVOLUTION_PLAN.md) defines the ordered,
not-yet-implemented design for everyday episodes, unresolved-state continuity,
typed organizations/places/relations, learning evidence and shared call budgets.
It does not replace this deployed contract until each phase is implemented and
verified. Its implementation checkboxes distinguish design from working behavior.
