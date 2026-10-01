# Memory maintenance correction — 2 October 2026

This delivery fixes the active corpus, course-note identity and state-reader
boundaries before applying explicit owner confirmations to an offline copy.
It is a subset of MEMORY_EVOLUTION_PLAN, not completion of P0–P8 or a live DB
cutover. No recurring audit or provider-powered corpus sweep is introduced.

## Root causes and resulting behavior

1. Legacy splitting was additive: a domain monolith could remain beside its
   replacement directory as a second current authority. The shared catalog now
   excludes replaced monoliths from active listings/recall. Exact reads label
   them historical; offline retirement requires the matching complete original
   capsule/hash. Unique original details remain accessible, not silently erased.
2. Course names were an unguarded generated heading. A conflicting optional
   name on an existing record was silently ignored; generic edits could rename
   it. All agent course mutations now guard code/name deterministically before
   model admission. Explicit owner correction changes only the heading. Notes
   and different term/code histories remain separate.
3. State evidence accepted only live physical paths although the reader could
   open moved aliases or original capsules. State verification now resolves
   those same owner-scoped sources and rejects state/current self-evidence.
4. Overdue open situations became only a counter. They now appear in a bounded
   section as last-reported, unconfirmed situations, never assumed completed.
   High salience can preserve an explicitly central owner situation in compact
   recall; it does not certify continued validity.
5. State get assumed flat addresses, monthly updates could fail schema gates,
   list returned oversized text and directory reads were unbounded. Monthly
   heads work, state results are paginated summaries, and directories use the
   common continuation boundary. Head selection parses each edition once rather
   than rescanning all editions for each key.
6. Manual data repair had no evidence-bound, durable unit receipt. The new
   operator-only offline runner binds the source and complete private plan,
   preserves originals, commits one unit atomically and resumes its receipt.
   Explicit owner confirmations require zero provider calls.

## Every changed code file

| File under `packages/igor/` | Change |
|---|---|
| `app/services/course_identity.py` | New shared literal code/name and owner-correction guard. |
| `app/services/memory_admission.py` | Resolved source authority distinguishes owner statements from documents. |
| `app/services/memory_store.py` | Runs identity guard before agent review; owner-evidence receipts use normal transactional catalog/revision path; legacy monoliths cannot become editable authorities. |
| `app/services/memory_catalog.py` | Owner-scoped active projection and replaced-monolith detection; source-result authority labels. |
| `app/services/memory_cleanup.py` | Versioned retirement policy, exact matching edition preservation, historical retirement issue; unsplit records survive. |
| `app/services/memory_graph.py` | Receipt-source evidence edges and labelled historical/source lookup. |
| `app/services/memory_policy.py` | Actual monthly routing and stable-path exceptions; retired monolith write protection. |
| `app/services/memory_schema.py` | Valid monthly legacy state editions remain writable through the lifecycle tool. |
| `app/services/memory_states.py` | Linear head selection, freshness, salience, bounded overdue view, shared active projection and archive/alias evidence resolution. |
| `app/services/memory_owner_review.py` | New immutable sources with complete raw timestamp spelling, before-hash units, atomic owner receipts, optional reviewed timetable name correction and issue resolution. |
| `app/skills/course_memory.py` | Explicit confirm_identity; conflicting optional name rejection; preservation of all notes. |
| `app/skills/memory.py` | Complete labelled state bullets in compact views; paged directories, hidden internal paths and historical/source labels; query-specific automatic recall shares the active catalog and prints actual monthly paths. |
| `app/skills/memory_state.py` | Bounded list/get, freshness, monthly head resolution and salience input. |
| `app/prompts/agents/ultron/02_course_memory.md` | Exact names, read-before-write, sourced correction and material-versus-learning distinction. |
| `scripts/apply_owner_memory_review.py` | New offline CLI, logical backup/source/plan validation, same-file/hardlink rejection, durable resume, integrity checks and optional vault export. |

Tests: `tests/test_owner_memory_review.py` is new;
`tests/test_memory_cleanup.py` and `tests/test_memory_contract.py` are updated.
AGENTS.md and the corpus/contract/enforcement/evolution documents describe the
new boundaries and explicitly retain unimplemented stages.

## Validation and limits

- Incident/cleanup/state group: **58 passed**.
- Final source-envelope and query-recall follow-up: **40 passed**.
- Full Igor suite with the workspace Forge import path: **937 passed, 1 failed**.
  The unchanged health test generates a sample one hour ago, then queries today;
  shortly after midnight the sample belongs to yesterday. Existing datetime
  deprecation warnings remain. No memory test failed in this run.
- Offline operator tests reject stale preconditions, foreign-owner evidence,
  hardlinked input/output, ungrounded names and document-authorized corrections.
  They preserve notes/originals, resolve historical evidence, resume receipts
  without model calls and bound large reads.
- Character bounds here are tool-result limits, not aggregate token/request
  budgets. The unified automatic selector, episode/relation schema and durable
  whole-run provider budget remain planned. Existing save_schedule continues to
  own timetable replacement; this delivery does not redesign that operation.
- Private sources, reviewed plans, databases and inspection exports stay out of
  Git. Production requires a compatible deployment and a fresh snapshot/cutover
  procedure; the old snapshot never replaces newer owner conversations.
