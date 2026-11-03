# Renovated Mark VI

This checkout starts from `first` commit b473404. Keep original reference
checkouts unchanged; the `final` branch retains the original final baseline.
Read `CLAUDE.md` for the inherited engine conventions. The current
renovation designs are `docs/architecture/memory/IMPLEMENTATION_DESIGN.md` and
`docs/architecture/iacon/ARCHITECTURE.md`. Iacon operations are in `docs/IACON.md`.
The current orchestration authority is `docs/architecture/orchestration/ARCHITECTURE.md`
with its implementation plan; `docs/ORCHESTRATION.md` records the implemented scope
and remaining release gates. It supersedes the inherited parked House Party and
obsolete microservice roster guidance in CLAUDE.md for this authorized renovation.

No generated test/cache folders in this checkout. Set PYTHONDONTWRITEBYTECODE=1;
pytest's cache provider stays disabled and test scratch belongs in OS temp.

Product acceptance target: the Superior Mark VI. Preserve every useful owner
capability from the original final application and improve its implementation.
Heartbreaker and SPEDA Go are one product on desktop and Android. Changes to
shared conversation, worker recovery, steering or presentation contracts must
include both clients and behavioral parity evidence; adapting the backend alone
does not complete the client work. Preserve platform layouts and native gates.
An architecture replacement does not authorize dropping the behavior it served.
Route registration, a wrapper, or a superseded label is not capability completion.
Require working owner flows, native permissions and deterministic effects,
configuration/recovery controls, and behavioral evidence before closing a gap.
Track unresolved equivalence honestly. Projects/canvas remain explicitly deferred
for the owner's later review, not removed. See docs/SUPERIOR_MARK_VI.md.

Orchestrator owns prompts. AgentContext owns request state and the memory budget.
CapabilityRegistry owns tools. Runtime instances live on app.state. No provider
calls on the foreground memory path; extraction runs in BackgroundTasks.

Original source rows remain immutable. Prepared memory is a disposable read view,
not an alternate canonical write surface. Do not regenerate biography or ledgers
from observations. Stable identities, attribution, temporal qualifiers and evidence
must survive selection. n8n is the only scheduler. No production deployment or
dataset replacement is part of local development.

Renovation modules:
- models/memory_serving.py: durable changes, prepared units, publication epoch.
- schemas/memory_runtime.py: evidence/packet/result contracts and request budget.
- services/memory_store.py: source capture, indexed reads, fenced publication.
- services/memory_runtime.py: local context selection and bounded lookup.
- services/memory_query.py: attributed conversation cues and bilingual identity routing.
- services/memory_prepare.py: durable bounded background capture.
- services/chat_turns.py: user/assistant persistence and turn lifecycle.
- skills/memory_lookup.py: registry-only read capability.
- scripts/import_memory_snapshot.py: offline, source-preserving final-copy import.

Iacon's only canonical writer is services/iacon_catalogue.py. Keep its evidence,
owner, version, revision and projection transaction intact. Models can only
propose; Orion independently reviews before applying. Native documents require
the owner writer. Imported original records remain retained and immutable.
services/iacon_worker.py replaces MemoryPreparer in the application lifecycle.
Every paid maintenance call goes through services/iacon_budget.py; do not add
unreserved repair, fallback, embedding or validation calls. Unknown outcomes
require reconciliation. The old memory_prepare module remains for baseline
compatibility tests and must not be wired back into production.
Hisar's library_* modules preserve original document/revision IDs and require live
access resolution. Their optional paid embeddings are not enabled. Telegram
state replies are owner/chat/version bound; unknown sends are never retried blindly.

Provider renovation:
- schemas/llm.py + services/model_policy.py: typed native thinking settings,
  immutable per-turn snapshots and revision-fenced owner defaults.
- services/llm_client.py + llm_protocol.py: provider transports, bounded streams,
  honest outcomes and origin-bound continuation. Internal text/tool blocks keep
  the inherited format; `_provider_state` is private protocol data, never memory
  evidence and never sent verbatim to an unrelated provider.
- services/task_runner.py: bounded read-only in-process workers, no nested Task.
  Durable project execution uses the separately adapted identity-free Forge runtime.
- Tool execution receipts commit before side effects. Unknown outcomes require
  reconciliation, never automatic replay. n8n remains the only scheduler.
- docs/PROVIDERS.md documents route configuration and acknowledged API controls.
  Model IDs and native capability declarations remain in profiles/speda.py.

Orchestration boundaries:
- Coordinator owns admission, rooms and decisions; Iacon owns knowledge. Agent
  contributions and project logs are not owner assertions. Pin persona, provider,
  thinking, prompt and budget authority for the run; aliases do not rewrite evidence.
- Every room/worker/Forge paid call uses the root coordination budget. Pre-effect
  receipts are mandatory, outside optional observers. Unknown outcomes stay held.
- Forge Workshop owns project checkpoints and claims. Workers get isolated bounded
  snapshots; canonical source integration is a separate operation. A stale lease
  never establishes that a Cell stopped. Do not silently enable subprocess fallback.
- Stop-reply, stop-task and stop-run have distinct scope. Later queued owner inputs
  must not leak into an earlier active round. n8n is still the only scheduler.
- `/rooms-ui` is a public static shell without private data; its API requests require
  authentication. This follows the inherited public docs/health shell convention.
