# Memory tool incidents — 3 October 2026

Two supplied chat exports exposed runtime failures beyond the offline corpus
repair. The fixes are in `091217c`; private exports, database files and reports
remain outside Git. No production deployment or live database write occurred.

## Reproduced causes and resulting behavior

- Parallel local tools shared one SQLAlchemy `AsyncSession`. Even reads could
  fail while that session provisioned a connection. The registry now gives
  annotated reads independent sessions bound to the request's engine and
  serializes local mutations sharing a session. Connection-bound transactions
  and pending ORM changes use the original session under that lock. External
  research calls retain their parallel execution; the orchestrator and routers
  do not learn tool-specific dispatch rules.
- A read finishing after a mutation could restore a stale memo entry. A memo
  epoch and in-flight mutation count prevent that. Mixed memory tools declare
  their read operations explicitly; `get`/`list` do not make `put` memoizable.
- Identical repeated empty `Log` sections generated the same passage ID twice.
  The duplicate pending insert failed, and a broad integrity-error handler
  called it a concurrent document creation. Passage indexing now indexes each
  stable content address once, retaining the complete document and its first
  location. Actual document-create conflicts remain retryable; index integrity
  failures now identify the index failure rather than recommend blind retries.
- Optional paging arrays populated with `[0,0]` failed or prompted one-character
  retries. Null/omitted fields and that empty placeholder now select a normal
  bounded read. Real ranges retain validation and their limits.
- History readers showed quotations without original message IDs. Both literal
  and semantic results now include `message:<id>` beside the role, so source
  evidence can cite the actual owner message.
- State evidence accepted `message:latest` while its separate source validator
  rejected it. The state tool now pins that source to the single message
  resolved by its exact evidence quote, including an earlier matching owner
  message in the same session. Ambiguous/missing evidence remains refused.
- Atomix's protocol described an obsolete, always-preloaded monolithic record
  and required repeated scans. It now follows the actual monthly topics, uses
  supplied context, distinguishes plans from completed activity, retrieves only
  details that change the answer and stops optional calls when asked. General
  memory instructions also distinguish partial write success and discourage
  retries on casual acknowledgements.

The monthly gym topic contract already works in the current code; the supplied
older runtime had rejected it. This delivery does not weaken document admission
or recreate legacy monoliths to accommodate that old failure.

## Validation

- Incident regressions before the fixes: **7 failed, 2 passed**.
- Provider/cache/Legion/incident group after fixes: **128 passed**.
- Final memory/owner-review/graph/cleanup group: **76 passed**.
- Final full Igor suite, with workspace Forge on PYTHONPATH: **949 passed**.
  Existing datetime deprecation warnings remain; no test failed in this run.
- Private integrated verification at `091217c`: all **153** active documents
  opened, largest result **6,320** characters; real parallel registry reads
  passed. The central unconfirmed situation retained its labels in a **476**
  character excerpt under a 550-character budget on both 2 and 3 October.
- Original document/raw-row, unchanged-table, revision/receipt, timetable,
  embedding and PDF parity passed. SQLite integrity was `ok`, foreign-key
  violations zero, provider calls zero. Source, baseline and final DB hashes
  remained unchanged throughout verification.

The private final DB is still an offline September snapshot with the reviewed
corrections. It does not contain every later conversation in the supplied
exports. The exported raw `current.md` is a migration marker; dated runtime
inspection projections are separate private artifacts.

These deterministic regressions and prompt contradictions are fixed in code.
Prompt behavior has not been tested through live provider turns after deployment.
Aggregate request/token budgets, episode/relation assertions and the unified
automatic selector remain unimplemented parts of MEMORY_EVOLUTION_PLAN.
