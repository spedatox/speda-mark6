# Superior Mark VI acceptance contract

The owner's direction is explicit: rebuild Mark VI to be substantially better
while lacking none of the original application's useful capabilities. The
original application is the behavioral baseline. A smaller endpoint count is
not a product objective.

## What completion means

For each original capability, establish the owner outcome, its original entry
points and data, the native implementation, the usable client path, and tests
that exercise the outcome. Renaming an endpoint is acceptable when the clients
and outcome work. Removing the outcome requires an explicit owner decision;
calling the old architecture superseded is insufficient.

Keep these dimensions separate:

1. Implementation: absent, partial, working or explicitly deferred.
2. Verification: registered only, contract tested, workflow tested or live tested.
3. Comparative acceptance: original outcomes preserved, improvement demonstrated,
   or an unresolved gap. Fixture verification does not establish live acceptance.

The existing capabilities.json is an interface inventory and migration audit.
Its 43 unresolved original HTTP operations are not 43 independent missing
features. Conversely, its verified routes and registered tools cannot establish
that the whole product meets this contract. Superseded interfaces still need a
working, behaviorally verified replacement for each useful outcome.

## Required owner outcomes

- Conversation: all identities and their styles, history/import/export/search,
  contextual welcome, attachments, reconnects, steering and stopping, and correct
  attribution in private chats and shared rooms.
- Collaboration: Legion workers, Autobots and Decepticons, their configurable
  model/thinking choices, permissions, progress, input, cancellation, durable
  claims and verifiable artifacts. Worker titles must never expand authority.
- Knowledge: Iacon memory with source evidence, original records, corrections,
  temporal qualifiers, independently reviewed changes and readable history.
  Retire old writers only through replacements that preserve their useful owner
  outcomes; do not silently treat lexical recall as proof of semantic parity.
- Documents/library: Hisar access and edition identity, extraction/OCR/semantic
  capabilities, citations, synchronization, progress, configuration and recovery.
- Finance: Sentinel can capture ordinary statements and receipts into Solus;
  identity, dates, allocation, double entry, defaults, duplicates and correction
  handling are deterministic code. No invented balancing or ledger facts.
- Integrations: all original domain tools, MCP/OAuth connections and grants,
  browser/research capabilities, mail/calendar/tasks, news, navigation, academic,
  health/training, notifications and file production/delivery.
- Automation: n8n definitions and triggers, standing reminders, watchers,
  delivery receipts and retries, quiet hours and recovery. n8n remains the sole
  business scheduler. Unknown deliveries are reconciled, never blindly resent.
- Speech: original provider/voice/tuning/language choices, HTTP and continuous
  input, interruption and bounded transport recovery, with paid-attempt admission.
- Operations: Doormat domain migration, Lifeboat resource watch/reclamation,
  Lockdown containment/release, Octavius verified backup/restore, Skyfall owner
  countdowns and system maintenance. Preserve their real outcomes through native
  controls; do not substitute a flag or an unimplemented sidecar wrapper.
- Configuration: all implemented settings and policies are owner configurable,
  with validation, masked secrets, revision checks, honest activation/restart
  behavior and recoverable retained state. New features add their controls too.
- Projects/canvas: retain the original outcomes in the acceptance scope. Canvas
  UI and visual-first agent guidance are now owner-authorized local work; live
  provider and production acceptance remain open. Remaining project work retains
  its separately tracked release gates.
- Release: installation, additive schema changes, live provider/sidecar/client
  workflows, data reconciliation, deployment and verified rollback. A passing
  local suite alone does not close these gates.

## Improvements that must be demonstrated

Behavior tests should cover success plus the failures relevant to each feature:
duplicate/concurrent input, a stale version, an interrupted stream, a lost
response, restart/reconnect, timeout and ambiguous external outcomes. Assert the
durable state, number of effects and paid admissions, not just response status.
Measure applicable latency and context/call ceilings. Do not add paid fallback
calls, timing guesses or bookkeeping decisions that depend on model memory.

Owner configuration and recovery must accompany a feature before it is called
complete. An operation refused because a dependency is not connected must say
what remains unavailable; it cannot count as a successful implementation test.

## Current continuation

The configuration work is locally verified. Remaining work is recorded in
integration/HANDOFF-2026-10-26.md: operational protocols, supporting adapters,
continuous speech, and release/live acceptance. Re-audit existing native
replacements for behavioral equivalence while completing those gaps. Preserve
the original first/final reference checkouts and all authoritative owner data.
Do not declare Superior Mark VI complete while any required outcome is missing
or has unresolved comparative acceptance.

## Enforced audit gate

`scripts/audit_integration.py --check-superior` now fails while this acceptance
is incomplete. Supply its existing `--scratch` directory outside the checkout.
The interface statuses remain separate from the `product_acceptance` report.

`integration/outcome-acceptance.json` records comparative owner workflows. Each
record can cover multiple original `route:METHOD /path` or `skill:name` points.
It must identify the original and native workflow, a demonstrated improvement,
and pass the preservation, owner-flow, deterministic-effect, configuration/
recovery and behavioral checks. External dependencies also require live proof.
Original-baseline and native-source fingerprints bind the evidence to the code.

Each evidence entry names a concrete `tests/test_file.py::test_case`. Its
`test_run` includes `status: passed`, zero failures/skips, the retained report's
filename and SHA-256. Pass `--acceptance-artifacts` with the report directory
outside the checkout. The gate reads the actual JUnit report, checks its digest
and checks that those exact cases executed successfully. Naming a test file or
claiming a pass cannot substitute for the run. Changed code invalidates stale
evidence. Deferred projects/canvas and the migration/rollback/live end-to-end
release checks remain visible gates rather than disappearing from scope.

GET `/admin/evals/behavior-config` is authenticated runtime evidence for these
comparisons. It exports native persona/alias and Legion/Autobot/Decepticon
roles, effective model/thinking routes, actually authorized tool definitions,
versioned policy documents, running public settings and source digests. It
performs no provider call and never labels a runtime snapshot production
verified. Saved-but-unapplied configuration is distinguished from running state.
