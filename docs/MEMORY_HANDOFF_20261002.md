# Resume memory work — 2 October 2026

## Completed continuation — 3 October 2026

The pending final offline verifier has now passed at `091217c`, including actual
parallel registry reads. All 153 documents opened within the limit, source/row
and binary parity passed, DB hashes remained unchanged and provider calls were
zero. The initial private verifier had referenced a nonexistent flat wellness
path; it now checks the actual preserved archive and its source ID. A verifier
reporting-only ORM access after rollback was also corrected.

The owner then supplied two chat debug exports and asked that their memory/tool
failures guide the work. The runtime fixes and **949-passed** full Igor suite are
documented in [MEMORY_TOOL_INCIDENT_FIXES_20261003.md](MEMORY_TOOL_INCIDENT_FIXES_20261003.md).
The private owner-readable final report is `db_migrate/FINAL_REPORT_20261003.md`;
the private continuation note starts with the updated status and artifact list.

The steps below describe the original checkpoint and its then-pending work.
Do not replay owner corrections or migrate the final DB again. The final `v2`
DB remains offline; it must never overwrite later live conversations. No P0–P8
redesign or production cutover was performed.

Read AGENTS.md in full first. Then read the detailed private continuation note
at `db_migrate/CONTINUE_HERE_20261002.md` in this workspace. That ignored file
contains the exact source/output paths, reviewed data changes and remaining
validation steps. Never add it, the DBs, source PDF, plans or vaults to Git.

The owner's latest request is to save and push safely before usage runs out and
leave detailed notes for a new chat. Stop after this checkpoint; do not start
another redesign phase during this turn.

## Saved code

- `9f6acdd`: active corpus/legacy retirement, exact course identity, state
  freshness/paging, evidence resolution and offline resumable owner review.
- `8e441ad`: preserve original raw row fields including timestamp spelling;
  query-specific automatic recall uses the active catalog rather than selecting
  a legacy monolith; generated path instructions match the monthly layout.
- This checkpoint: prioritize explicitly central state bullets across freshness
  sections when building a tiny current excerpt. Keep the unconfirmed heading
  and last-reported label with overdue content. This fixes a real-data smoke
  failure where ordinary fresh states consumed the excerpt before a central
  overdue situation. No claim of continued validity is introduced.

`MEMORY_MAINTENANCE_FIXES_20261002.md` lists every changed code file and root
cause. `MEMORY_CORPUS.md` is the current contract; `MEMORY_EVOLUTION_PLAN.md`
still contains unimplemented phases. Unified aggregate token/request budgets,
episode/relation assertions and the full automatic selector are not delivered.
Nightly bulk audit remains removed.

## Verified versus pending

- Full Igor suite with `packages/forge` on PYTHONPATH: 937 passed, one unchanged
  health test failed shortly after midnight because a sample one hour ago is in
  yesterday while the test queries today. No memory failure in that run.
- Initial incident/cleanup/state group: 58 passed.
- Raw source-envelope/query recall follow-up: 40 passed.
- Latest central-state/contract/connected/course group: 64 passed.
- Final offline owner correction run: 10 committed units; second run returned
  all 10 original receipts, provider calls zero, SQLite integrity ok, FK zero.
- Final cross-snapshot parity checks passed through original documents, original
  revisions/receipts, unchanged tables, exact timetable field preservation,
  embedding preservation, source PDF binary parity, metadata and integrity.
- The integrated runtime script stopped at tiny current-excerpt selection before
  the last code fix. Rerun it with the final checkpoint code and generate its
  final JSON report; do not claim that final script already succeeded.
- Two size advisory warnings remain on corrected long project/finance records;
  these are not structural errors. Reads are bounded and original history is
  preserved. Do not summarize away historical facts to silence a size warning.

## Immediate next chat

1. Confirm the latest code commit and remote status. Preserve unrelated dirty
   files, especially desktop UI, orchestrator, routers and chat export work.
2. Read the private note. Use the final `-v2.db`, never the older draft output.
3. Update the private verifier's `code_commit` to the final checkpoint and rerun
   `db_migrate/validate_final_20261002.py` with Igor on PYTHONPATH. Inspect actual
   agent tools, not only tables. If it fails, fix the specific cause and record
   the result; no whole-corpus LLM pass or blind replay.
4. Finish a private owner-readable report, distinguish runtime current projection
   from the raw stored current.md marker, and mark exactly which artifacts are
   final. Original DB snapshots and all intermediate outputs stay preserved.
5. No production cutover has happened. A fresh live snapshot and compatible code
   are needed before deployment; never overwrite new conversations with the
   September snapshot. Wait for the owner's next focus before starting P0–P8.
