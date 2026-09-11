# Current implementation

The enforced writers, typed finance views and durable Orion controller are specified in [Memory enforcement](MEMORY_ENFORCEMENT.md). That document supersedes the earlier voluntary-write/audit procedure below.

# Memory contract

This document describes the running design. Historical V1–V4 references in old
code comments describe retired experiments, not instructions for agents.

## Storage and responsibility

Memory has three complementary layers, with distinct authority:

| Layer | Authority and lifecycle |
|---|---|
| Topic documents | Canonical biography, domain references, tables, ledgers, people, projects and owner instructions. Preserve their native structure. |
| State records | One versioned situation per stable key in `states/`. Explicit status, source, verification date, review date and optional validity interval. |
| Observations and conversations | Searchable claims and original evidence. Search observations do not regenerate or replace topic documents. |

`memory_spec.py` owns document grammar, collection ownership and size limits.
`memory_policy.py` owns meaning-based routing instructions and protected paths.
The contract is generated into agent recall, so expanding an existing domain
does not require copying a second routing table into every agent prompt.

Subject and temporal meaning are independent. A financial event belongs in the
financial ledger even when another agent learns it. A sent application is an
event; waiting for its answer is a separate state. A preference is an owner
instruction; a behavioural pattern is a fallible, evidenced inference. Unknown
facts never default into current context.

## State lifecycle

`memory_state` provides list/get/put to local agents and the authenticated peer
memory channel. A put requires the exact content version from get/list (or `new`
for an absent key). It carries a real, owner-scoped evidence reference and an
explicit review date. Source existence is checked mechanically; whether the
source supports the claim still needs judgement.

Open statuses are `active`, `waiting`, `planned`. Terminal statuses are
`completed`, `cancelled`, `superseded`; closing requires a date and outcome
evidence. A past review/end date means **unverified**, never completed. Completed
records remain in their original path and revision history.

`current.md` is a read-only projection with active, planned and verification
sections. It is computed at read time using the owner's timezone, so a failed
nightly audit cannot keep an expired state asserting itself as current. Chat
recall, memory views, the knowledge-bank API, welcome context and peer recall use
the same projection. The cache changes when projected content or the day changes.
The stored current file is only a migration marker, not an independently editable
copy. Before migration, the legacy snapshot remains visible as unverified.

Existing client knowledge-bank surfaces can read state documents and the current
view; state transitions are performed through the assistant's lifecycle tool.
There is no new bespoke state editor in the desktop or Android clients.

## Writes, conflicts and recoverability

Raw and shaped agent writes and owner commits/restores use `mutate_file`:
policy → grammar → conditional database write → revision → one commit. An
unchanged payload is a no-op. A stale original or competing create is a conflict,
never an unconditional overwrite. On conflict the caller must reread and
recompute the intended change. Internal migrations have their own all-or-nothing
plan service; system-written session logs remain internal system operations.

Archives and system trails are protected from normal agents. Orion can maintain
its audit trail and repair live domain files, but cannot revive retired monoliths
or edit current/state records as arbitrary text. Owner text commits retain their
advisory structural policy for ordinary documents; managed surfaces cannot be
overwritten through that exception.

Use ledger_append for dated rows, registry_upsert for people/projects,
narrative_revise for biography chapters, and small anchored edits for references.
Generic events without a subject-specific log have monthly files under events/.
Dates must be real calendar dates; dated table rows must match their ledger key.
Compound moves store and verify the destination before removing source text.
Operator migrations preserve originals under `.archive/` and in revisions.

## Automatic intake

Fact extraction is durable post-turn work. Extraction jobs are distinct per
request, with the source owner message captured in the queue payload. A delayed
job cannot silently mine the latest turn instead. Model output must quote owner
text verbatim, and extracted names/numbers must occur in that quote. Assistant
text is excluded from the extraction prompt and grounding check. Evidence is
stored with the observation. Failed extraction propagates to queue retries and
failure reporting rather than masquerading as a completed job.

The quote check does not prove logical entailment. It prevents specific leakage
and fabrication classes; Orion still reviews claims and contradictory evidence.
Extraction supplements document/state maintenance; it does not automatically
rewrite domain documents or promote search observations into current states.

## Orion: measured review, not self-reported cleanliness

`memory_audit scan` and `GET /admin/memory/verify` combine structural findings with
semantic-review coverage. `memory_reviews` is an append-only table created by
normal schema startup. Reviews are bound to the SHA-256 of the exact document.
Any changed content re-enters `pending_review`. An unread document is pending,
not implicitly clean. Unresolved findings stay in the report; overdue states and
frozen countdowns are checked independently of a review attestation.

Orion reads pending documents and relevant evidence, checks subject boundaries,
time, competing editions, contradictory claims and related files, repairs narrowly,
then reviews the resulting fingerprint. A structural error prevents an empty
findings attestation. It must report remaining coverage and defects accurately.
An agent review is an accountable attestation, not a guarantee of truth.

Old scheduled intents cannot override this procedure: memory-audit triggers load
the currently shipped audit prompt. n8n owns scheduling; no new internal clock
was added. Routine unchanged audits remain quiet; material defects and required
owner decisions are surfaced by the existing audit notification policy.

## Migration and extension

`scripts/migrate_memory_contract.py` accepts a private reviewed JSON plan. It
defaults to dry-run, verifies original content hashes, validates changed document
structure and applies the complete set in one transaction. Existing bytes are
archived before replacement. Reapplying the same plan is a no-op; a stale plan
aborts. Before applying in production, take a SQLite backup using `backup()`
(never copy an active WAL database). Rollback is a new conditional plan using
archived content; no history rewriting is necessary.

Add a subject topic to its existing extensible domain. Add a new document family
by declaring its grammar, ownership and routing rule together, then test write,
read, audit and migration paths. Do not enable the retired flat observation
renderer or model biography composer to add a feature: the admin composition
endpoint refuses while composition is disabled.

Regression coverage includes state expiry, plans versus outcomes, invalid dates,
source existence, concurrency conflicts, protected paths, semantic review
invalidation, projection purity, dossier extension injection and legacy scheduler
compatibility. Private production memory and migration plans must never enter git.
