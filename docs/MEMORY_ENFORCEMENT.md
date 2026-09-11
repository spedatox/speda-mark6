# Memory enforcement

The September 2026 incidents exposed two gaps in the previous contract. A valid
Markdown table could still contain the wrong kind of fact, and Orion could read
a scan then write a successful audit narrative without reviewing any documents.
Both paths are now enforced by the backend.

## One write boundary

| Knowledge | Writer | Canonical record |
|---|---|---|
| Ongoing situation / confirmed plan | `memory_state` | Stable state key, status, evidence and review deadline |
| Dated domain event | `ledger_append` | Subject's date-indexed event log |
| Person / project | `registry_upsert` | One entity, replaceable description, dated history |
| Reference / preference | `memory_edit` | Registered topic, exact patch, expected version |
| Biography correction | `narrative_revise` | One named chapter |
| Financial fact | `finance_record` | Typed record and stable occurrence identity |
| Search claim | `record_observation` | Sourced claim with domain, subject and reasoning level |
| Cross-document correction | Orion audit controller | Atomic exact patches, evidence and revisions |

Raw agent create/insert/replace/delete operations cannot commit. All agent writers
must resolve exact quotations in owner messages, existing observations or memory
documents. `message:latest` resolves only inside a genuine owner conversation,
never an automated trigger. Image evidence uses message:<id>#image:<index> and a
transcription checked against the attached original by a vision-capable reviewer.
Receipts store the source hash, not duplicate image bytes. A separate, tool-free model call checks source support,
subject, section, lifecycle, duplication and loss of unrelated knowledge. A timeout,
malformed result or rejection saves nothing. The model remains fallible: source
existence and quotations do not mathematically prove factual entailment.

Document ownership, taxonomy, typed schemas and optimistic concurrency are code
boundaries. Agent claims about their authority cannot bypass them. Approved writes
store their evidence hashes and validation rationale in `memory_write_receipts`,
in the same transaction as `memory_revisions` and the changed document. Owner
editing remains separate; computed views and managed records are not text-editable.

## Financial records and views

`/memories/finance/records/<id>.md` contains a validated record and its deterministic
readable representation. The representations must agree byte-for-byte.

| Type | Meaning | Required distinctions |
|---|---|---|
| transaction | An actual movement | expense, income, transfer, debt_payment, loan_disbursement |
| balance | One account's dated snapshot | account, as-of date, asset/liability, currency |
| report | A source document / aggregate comparison | report reference and report date; no transaction amount |
| recurring | A standing rule | effective interval, frequency, amount, optional due day |

Amounts use exact decimal strings (`4914.00`), explicit currencies and no grouping
separators. `null` means unknown, not zero. A transaction with unknown occurrence
date has `date: null` and an evidenced `reported_on`; it is excluded from dated
period totals unless an evidenced occurrence month is explicitly recorded in
`period`. Unverified records are explicitly marked. Incorrect records can
be voided, retaining their identity and revisions. Corrections update the original
record rather than creating a second debt containing a correction notice.

The database enforces unique occurrence/report identities, including simultaneous
writes. A record's identity is immutable. A balance is not a purchase amount;
a report aggregate is not another liability; paying a card is not a second purchase.

`ledger/YYYY-MM.md`, `balances.md`, `reports.md` and `monthly-structure.md` are
generated inside the record transaction. No agent chooses where a row is placed.
`monthly-structure.md` therefore cannot accept a month's actual activity. Summary
totals use Decimal and separate movement types and currencies. Unknown/unverified
entries are listed as exclusions, and only the newest dated snapshot per account
is returned as a balance. Historical source tables under `finance/legacy/` remain
discoverable and explicitly unreconciled; they must not be added to reconciled totals.

New documents or record types require a registered contract. Extending the grammar
means changing its schema, validator, writer, views and regression tests together.
Private owner content and migration plans never belong in git.

## Orion executes, measures and repairs

Scheduled `memory_audit` triggers enqueue a durable controller directly. An old
n8n intent cannot substitute a freeform conversation. `memory_audit run/status/scan`
offers the same workflow during a conversation. Only one pending/running audit per
owner is allowed; claims use database compare-and-swap and progress renews the lease.
Restart recovery and the existing n8n drain recover unfinished work.

The controller chooses every changed, never-reviewed, review-expired or unresolved
document and search observation within configured limits. It supplies complete
targets, related records and available primary conversation evidence to reviews.
Missing coverage stays pending; an incomplete provider response is a failure.
Document reviews are bound to exact content hashes and expire on a configurable
interval even without edits. A write invalidates the old review automatically.

Repair proposals need unique exact anchors and verifiable evidence. The independent
admission check evaluates the whole proposed move, and source/destination changes
commit atomically. A conflict rolls back every part. Repaired documents are reread
and reviewed again. Observation domain corrections and conservative duplicate
merges preserve the original record and provenance, with a reversible revision.
Owner-origin observations are flagged rather than automatically rewritten.

An expired state disappears from the current view without being falsely marked
completed. Its record remains in the review inbox. Closing or renewing a state
requires evidence; passage of time alone establishes neither an outcome nor
continued validity. Metadata and readable state text must agree.

The controller writes `/memories/.audit/runs/<request>.md` with actual review,
repair, failure, pending and unresolved counts. Manual audit success logs and
manual clean attestations are disabled. A job finishing is not a declaration that
all memory is correct: its verdict remains `review_required` while work or
uncertainty remains. Unresolved factual questions must stay visible.

## Operations and rollback

All model/budget/timeout/batch/expiry controls appear in the shared backend
configuration schema, consumed by the clients' settings surfaces. There is no
hidden second scheduler. Model review availability is now on the agent write path;
an unavailable provider blocks a write explicitly rather than silently accepting it.

Use `scripts/migrate_memory_contract.py` with a private, content-addressed plan.
Dry-run first against a consistent database copy. A production migration requires
a fresh database backup; every replaced/deleted original is also archived and
revisioned. A stale expected hash aborts the entire plan. Reapplying the same plan
is a no-op. Financial record migration validates and regenerates all affected views
in the same transaction. Rollback is a new forward plan from the preserved originals,
not a history rewrite.

Regression coverage includes raw-write rejection, monthly-structure misuse, exact
source quotes, reviewer outages, stable financial identities, ambiguous decimals,
unknown values, atomic record/view updates, atomic moves, lifecycle expiry, project
upsert idempotence, and real versus fabricated audit completion.
