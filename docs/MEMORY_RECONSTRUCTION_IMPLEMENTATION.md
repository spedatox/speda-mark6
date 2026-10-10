# Reconstruction destination: implementation audit (2026-10-10)

This audit precedes inspection of the supplied database. It describes the checked
out implementation, not a proposal for a new architecture. Private data, plans,
reports and database copies must stay outside Git. No production cutover is
authorized by the reconstruction run.

## Authoritative structures

`app/database.py` registers `app.models`, creates missing ORM tables and runs
existence-guarded additive column/index migrations. SQLite connections normally
enable WAL and foreign keys. Offline tools must use separate explicit engines,
not call application startup or drain the durable queue.

* `memory_files` is the virtual document filesystem, unique by owner and path.
  Canonical topic documents retain prose, tables, headings and dated logs.
* `memory_record_meta` binds one file to a stable record ID, version/hash,
  category/kind, storage period and separate occurrence/effective/source dates.
  A storage month is not evidence of an occurrence date.
* `memory_entities`, `memory_entity_heads`, `memory_record_links` and
  `memory_path_aliases` preserve person/project identity, current edition,
  explicit record relationships and compatibility addresses.
* `observations` contains searchable attributed claims: explicit, deductive,
  inductive or contradiction; owner/person/project subject; biography,
  preference, state, project, training, finance or event domain. Validity and
  supersession preserve historical values. Origins distinguish live, owner,
  seed, reindex, artifact, tool, synthetic and agent records. Soft deletion is
  demotion, not erasure. This is an index alongside documents, not their source.
* `memory_sources` preserves immutable full originals, exact row envelopes,
  original addresses and hashes. `memory_issues` records unresolved discrepancies.
  `memory_passages` mechanically indexes sections/entries; changed passages are
  retired. `memory_graph_edges` labels associations and evidence explicitly.
* `memory_revisions` and `memory_write_receipts` are append-only change and
  admission trails. `memory_reviews` is a fingerprint-bound, expiring attestation;
  an unreviewed or changed record cannot be declared semantically clean.
* `memory_capture_jobs` and `memory_capture_payloads` bind durable intake to its
  complete candidate and idempotency hash. Legacy incomplete captures require
  review. A crash during review is not permission to blindly replay.
* `pattern_states` and `pattern_evidence` wrap canonical inductive observations;
  countermeasure tables track responses/outcomes. Inferences and synthetic
  evidence have zero confidence weight. Evidence independence uses source groups.
* `messages` and `sessions` remain historical evidence, including session recaps
  and compaction watermarks. Compaction changes model context, not raw history.
  Message vectors and FTS tables are retrieval indexes, not new testimony.
* The legacy `memories` table remains registered. Its contents must be inventoried
  even though the current tools do not use it as their authoritative write surface.

## Routing and lifecycle

Checked `memory_spec`, `memory_policy`, `memory_paths`, `memory_calendar`,
`memory_catalog`, `memory_identity`, and the shaped writers:

| Information | Current destination |
|---|---|
| Life history | Root `owner.md`, native chapters |
| Standing owner instructions | Dossier topics, with date and attribution |
| Personal experience without specialist ownership | `general/MM-YY/topic.md` |
| Person | `social/MM-YY/personal\|professional/name.md`, Who and dated Events |
| Project | `projects/MM-YY/name.md`, native project structure and stable identity |
| Academic topic | `academic/MM-YY/topic.md` |
| Course | `academic/courses/YYYY-YYYY-term/CODE.md`, exact code/term/name |
| Training | Wellness directive/profile/program/gym/session topics |
| Ongoing situation/confirmed plan | `states/stable-key.md`, typed lifecycle |
| Financial fact | `finance/records/stable-id.md`, typed record |
| Finance projections | `finance/MM-YY/ledger.md`, stable balance/report/rule views |
| Unreconciled historical finance table | `finance/legacy/YYYY-MM.md`, excluded from totals |
| Host operations/security learning | Owning ops/cybersec topics and dated logs |
| Pattern | Inductive observation, evidence/state and distinct pattern document |
| Unknown/conflicting material | Original source plus explicit issue; no guessed fact |

State `parse/encode/validate` enforces exact JSON/text parity, source, status,
verification/review dates and terminal closure date. Expiry means unconfirmed,
not completed. `project_files` recomputes current context on the owner's day;
the stored `current.md` marker enables this projection. Before migration a legacy
snapshot stays labelled unverified. Financial `parse/encode/validate/views`
distinguishes transactions, dated balances, reports and recurring rules; unknown
amounts stay null, transfers/loans/repayments are not spending, and legacy totals
are never additive. Event identities are unique and immutable.

## Active write, extraction and review paths

Startup registers memory, shaped edit/event/state/course/finance, observation,
pattern and graph tools. `memory_store._mutate_in_txn` is the document boundary:
policy and grammar -> resolve evidence -> literal course guard -> semantic
admission for agent writes -> conditional mutation -> metadata -> passages ->
revision/graph/receipt -> commit. Explicit owner corrections do not require a
provider verdict, but may not bypass managed state/current/finance boundaries.
The reconstruction must not label an operator inference as an owner correction.

`RecordObservationSkill` resolves persisted owner messages/tool context, invokes
the reviewer, and distinguishes direct owner reports from unstated inference.
`record_observations` validates the ladder and fragment/domain/date rules,
converges subject spelling, reinforces exact same observer/level/subject/content
claims, then indexes graph/FTS and synchronizes inductive state. Preserve chronology
and all provenance when reconciling an old duplicate; do not infer independence
from repeated migration output. Higher levels need existing observation sources.

Post-turn work is enqueued before execution (`memory.schedule_background_tasks`,
`run_post_turn_tasks`, `task_queue`). `fact_extraction` reads the queued owner
message, quotes it literally, checks names/numbers, and writes explicit search
claims; it does not rewrite topic documents. Assistant text is not owner evidence.
The retired nightly audit is rejected by queue recovery. n8n is the scheduler.

`memory_admission.resolve_evidence/admit/ask_json` checks owner-scoped references,
literal quotations, source hashes and neighboring documents; reviewer outages
fail closed. `memory_audit.coverage/record_review` checks exact fingerprints,
expiry, unresolved findings, state freshness and structural failures.

## Retrieval

`recall_for_context` supplies bounded standing constraints/current context and a
root listing. `relevant_files_for_message` selects at most two native documents,
checks course terms and surfaces source discrepancy warnings. Exact document
reads use paging, aliases and historical-source fallback. `search_sources` is
bounded literal historical retrieval. `search_observations` filters owner,
level/subject/domain/validity and fuses Turkish-folded FTS5 BM25 with normalized
vector ranks above a relevance floor. No match means an empty result.
Conversation retrieval uses raw-message indexes and recaps. Graph traversal is
bounded; mentions/similarity are not causal or interpersonal assertions. Pattern
recall uses separately scored evidence/lifecycle and tactical context.

## Historical migrations and validation

Examined `memory_split.plan_split/plan_shard`, `memory_cleanup.CorpusCleanup`,
`memory_owner_review.apply_owner_unit`, `migrate_memory_contract`,
`memory_reindex`, `history_indexer`, `surprisal`, `memory_verify`, and tests for
cleanup, contract, enforcement, course identity, owner review, automatic intake,
observations, captures, patterns and connected/vector recall. Splitting operates
on declared headings and reports unmatched/stray/oversize content. Existing
cleanup preserves capsules before retiring sources, reconciles known layouts,
indexes passages/graph and flags candidate conflicts; its successful report does
not certify full semantic reconciliation.

`memory_render.RENDERED_FILES` and `memory_compose.COMPOSED_FILES` are empty.
Their old flat rendering/composition is retired. `derive_from_history` hard-deletes
prior reindex-origin rows, so it is unsuitable for this lossless task. Old
observation/model comments describing documents as fully derived are stale.
No new schema, provider re-extraction, biography regeneration, nightly audit,
or timer-based memory rewrite is part of this reconstruction.

The staged run must supplement existing cleanup checks with every-table/record
dispositions, logical-reference checks, immutable history parity, complete original
payload parity, current-schema inspection, actual retrieval/write smoke checks,
representative evidence cases, repeatability and a sandbox cutover/rollback drill.
Pending semantic issues must remain explicit approval blockers, not a clean verdict.
