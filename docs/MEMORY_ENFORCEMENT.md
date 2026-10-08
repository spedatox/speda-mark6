# Memory enforcement

**Current corpus contract:** [Memory corpus](MEMORY_CORPUS.md) supersedes the
bulk Orion controller described in historical sections below. There is no
scheduled memory audit, mass repair or automatic replay of legacy capture jobs.

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

Observation review includes the adjacent persisted assistant turn as labelled,
bounded context for an owner's short answer. It can identify what a confirmation
or denial refers to; it is not owner evidence. Assistant messages still cannot
resolve as `message:<id>` evidence, and their text is not copied into observation
sources. The reviewer still judges whether the owner's answer supports the
proposed scope; a short answer never grants automatic admission.

Observation confirmation results identify an unsaved proposal, not a command to
repeat a question immediately. The conversational model checks the actual
exchange, clarifies a genuinely missing material detail once, and continues the
owner's substantive request even when a nonessential save remains unresolved.
A reviewer that requests confirmation without supplying a question fails
closed; the tool does not invent a date question. Original owner messages and
tool audit results remain the existing recovery evidence. This is no automatic
acceptance, background retry mechanism or guarantee of behavioral recovery.

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

## Orion memory coverage

The nightly bulk audit was removed at the owner's request. `memory_audit scan`
is a read-only coverage report. Historical review records and audit reports remain
available, but no scheduled or manual bulk review/repair controller runs. A legacy
`/trigger/orion` audit request returns HTTP 410 before starting a turn. Existing
queued audit jobs are retired without provider calls. Deactivate any previously
imported n8n workflow as well; deleting the repository template cannot remove a
workflow from a separate n8n instance.

An expired state leaves the confirmed-current section without being falsely
marked completed. A few overdue open situations remain visible with explicit
unconfirmed labels; the full record remains in the paginated review list. Closing or renewing a state
requires evidence; passage of time alone establishes neither an outcome nor
continued validity. Metadata and readable state text must agree.

## Operations and rollback

Model review and timeout controls appear in the shared backend configuration
schema, consumed by the clients' settings surfaces. There is no hidden scheduler.
Model review availability is on the agent write path;
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
unknown values, atomic record/view updates, lifecycle expiry, project
upsert idempotence, and retired audit triggers.
