# Renovated SPEDA Mark VI

Iacon memory and the multi-provider answering layer, based on the original `first` checkout
(commit `b473404`). The original `first` and `final` folders remain unchanged.
The implementation blueprint is in [the memory design](docs/architecture/memory/IMPLEMENTATION_DESIGN.md).
The implemented Iacon architecture and operator controls are documented in
[Iacon](docs/IACON.md). Renovated was deployed on 2026-11-03 using a fresh,
reconciled production database and the existing private service configuration.

Deployment packaging, isolated server verification and the data-preserving
cutover procedure are in [deployment instructions](docs/DEPLOYMENT.md).
Production data is authoritative; the owner's local test database stays a
separate retained backup. The `final` branch preserves the original final
application, and the former remote `main` is retained separately before the
Renovated source transition. See the [release verification record](docs/deployment-validation-2026-11-03.json)
for the deployed image, validation and explicitly deferred product gaps.

## What runs

- Chat persists the original owner message and its durable memory intake in one
  transaction before contacting the answering model.
- One local memory runtime supplies source-linked context in the first request.
  It resolves person candidates, reads prepared sections, searches original owner
  evidence, includes recent corrections, and respects one combined context budget.
- SQLite FTS5 performs local search. Routine memory reads make zero model,
  translation or embedding requests. Scoped caches are bounded and versioned.
- `memory_lookup` provides one bounded batch for missing details. Its server
  deadline is 2.5 seconds, with SQLite VM interruption and bounded results.
  After that batch the registry removes it from the ordinary turn's tool list.
- The answering model streams text immediately as it generates it. HTTP SSE and
  authenticated WebSocket chat use the same persistence/orchestration path.
- Orion proposes typed records from bounded source slices, then reviews proposals
  against exact evidence. Invalid contracts are quarantined; successful paid
  responses survive publication retries. Historical imports never trigger bulk
  extraction, and every paid maintenance call requires a persistent reservation.
- Native images reach the answering model unchanged. Available attachment text
  can help routing; native vision can identify a name and request the one lookup.

Prepared memory is a disposable read view. It cannot edit a biography, merge
people, invent a date, or mutate accounting records. Original source references,
attribution and historical qualifiers accompany the selected text. The baseline
does not yet expose the final version's canonical domain writers or other full
application capabilities.

## Local run

This task's tested Python environment is outside the project:
`C:/Users/ahmet/.codex/environments/speda-renovation`.
Dependencies are pinned in `uv.lock`; an ordinary installation can use
`uv sync --frozen --extra dev` with `UV_PROJECT_ENVIRONMENT` pointing outside the
checkout.

Create a local `.env` from `.env.example` and configure the intended provider key
there. Do not put credentials in source control. This baseline retains the first
version's default Anthropic model choices. The renovated provider layer now
supports Anthropic, OpenAI Responses, native Gemini/Vertex, and compatible
connections for Kimi, DeepSeek, z.ai, NVIDIA and Ollama. No provider key is
required for local structural tests. See [provider setup and controls](docs/PROVIDERS.md).

From this directory in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:EXTERNAL_INTEGRATIONS_ENABLED = 'false'
$env:HTTP_HOST = '127.0.0.1'
& "$env:USERPROFILE/.codex/environments/speda-renovation/Scripts/python.exe" -m app.supervisor
```

The managed launcher enables **Configuration → Restart backend** after saving.
Every declared backend setting and the native model, worker, spending and Iacon
policy documents are available there. See [configuration controls](docs/CONFIGURATION.md)
for persistence, activation, secret handling and restart behavior.

`POST /chat` accepts `{"message":"...","session_id":null}` with `X-API-Key`.
Optional `model` and typed `thinking` fields select a route and native effort
for that turn. The default model is unchanged unless explicitly configured.
The message may also be native text/image content blocks. `/ws` accepts the same
JSON request after API-key authentication. `/health` is public. API documentation
is available only in debug mode.

The default SQLite database is `./speda.db`, which is ignored by Git. To evaluate
the imported offline serving database, explicitly set `DATABASE_URL` to that
separate database before starting the process. Do not point the renovation at a
production or original migration database.

Background work drains after a turn and at startup. Existing n8n invokes
`POST /iacon/drain` with both `X-API-Key` and `X-N8N-Secret` for maintenance and
Telegram follow-ups. `/admin/tasks/drain` remains a capture-only compatibility
endpoint. There is no new scheduler. Expired work leases reuse saved responses;
unknown provider outcomes require reconciliation and are not automatically replayed.

## Offline snapshot import

`scripts/import_memory_snapshot.py` accepts a consistent, uncompressed **offline**
SQLite snapshot, a new destination path, and a new report path. It refuses an
existing destination and an active WAL/journal. Original files and source hashes
are preserved; no original table is rewritten.

It creates a serving index of the original documents, owner conversation text
and observation claims. Archived/derived surfaces and superseded claims remain
preserved with restricted serving eligibility. Inferences are not injected as
explicit facts. Original session/message IDs are provenance locators, never
misused as foreign keys into newly created chat tables. The original snapshot
must remain retained: this serving import is not a complete restore or a live
cutover procedure.

The owner-supplied October 10 snapshot has been exercised in a separate private
replay directory under `.codex/research/dreaming-v3-20261016/private-replay`.
Source-bearing databases, import ledgers and captured memory packets stay outside
this checkout. The public research reports contain aggregate timings and counts.

## Collaborative rooms and durable project work

IGOR/FORGE now supports asynchronous dispatch during conversation, durable live
steering, cross-turn mission continuity, bounded supervisor acceptance, and
owner-approved publication through trusted deployment drivers. See
[IGOR/FORGE architecture and operation](docs/IGOR_FORGE.md) for the implemented
paths, approval contract, recovery semantics and live release gates.

The new `/rooms-ui` shell uses eight distinct profiles, attributed shared-room
turns, queued owner messages, decision records and scoped stop controls. Room
speakers and project workers share durable spending reservations. Project work
uses isolated Forge artifacts and versioned Workshop checkpoints.

Spending starts paused and project execution disabled. Configuration, recovery,
component ownership and remaining release gates are in
[docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). Live Hisar execution and owner-reviewed
persona fidelity remain unverified. Legacy `/chat` does not use the new coordination
budget; Iacon maintenance retains its separate limits.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$testScratch = Join-Path "$env:USERPROFILE/.codex/tmp" ('speda-memory-' + [guid]::NewGuid().ToString('N'))
& "$env:USERPROFILE/.codex/environments/speda-renovation/Scripts/python.exe" -m pytest tests -q -p no:cacheprovider --basetemp $testScratch
```

Tests cover source/owner isolation, atomic intake, invalid/truncated capture,
exact chunk coverage, publication retry/CAS, contextual answers, chronology,
revocation, alias ambiguity, bounded results, real SQL deadline interruption,
HTTP/WS chat, streaming and startup/shutdown. Test provider responses are mocks.

`scripts/benchmark_memory.py` measures synthetic local selection at two corpus
sizes. `scripts/replay_memory.py` measures targeted selection against a serving
copy of the real snapshot. Neither benchmark measures answering-model quality.

## Remaining gates

The runtime is implemented and exercised locally. An identical ChatGPT feel is
not established by retrieval timing alone. The broader held-out first-answer
quality suite, >=90% correct tool avoidance, actual provider latency, cold-image
OCR and full client/provider compatibility remain to be validated or completed.

Iacon now supports typed entities, claims, preferences, episodes, states and native
documents, with reviewed background proposals, linked records, immutable revisions,
Telegram follow-ups and Hisar retrieval. Broad semantic paraphrase coverage and
answering-model quality still require evaluation. Source-level revocation is
enforced; complete semantic erasure across independent historical copies is not
implemented. Detailed operational limits are listed in docs/IACON.md.

Context budgets currently use a conservative UTF-8 byte upper bound, not measured
provider tokens. Serving traces separately report complete provider-payload bytes.
Real model/token-budget tuning must preserve evidence adequacy rather than merely
make the context smaller. Docker configuration uses persistent SQLite storage.
The 3 November release preparation builds and exercises the production image,
including native PDFs and Solus, on an isolated server candidate. The live
application and its database have not been replaced.
