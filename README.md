# SPEDA Mark VI · Renovated

[![CI/CD](https://github.com/spedatox/speda-mark6/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/spedatox/speda-mark6/actions/workflows/validate.yml)
[![Android](https://github.com/spedatox/speda-mark6/actions/workflows/speda-go.yml/badge.svg?branch=main)](https://github.com/spedatox/speda-mark6/actions/workflows/speda-go.yml)
[![AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

**Personal assistance with persistent memory, coordinated agents, and recoverable execution.**

Renovated brings the backend, Heartbreaker desktop client and SPEDA Go Android
client onto shared conversation, memory, model-policy and worker contracts.
The backend is self-hosted and designed for one owner. Eight agent profiles work
through one application, with explicit attribution, scoped capabilities and
durable records of their work.

The **3.0 release series is live**. Production history and memory were carried
forward with their original source records. Credentials and owner preferences
remain in private persistent storage.

## Downloads

| Application | Platform | Release |
| --- | --- | --- |
| **Heartbreaker** | Windows x64 | [Windows installer](https://github.com/spedatox/speda-mark6/releases/latest) |
| **SPEDA Go** | Android 12+ | [Signed Android APK](https://github.com/spedatox/speda-go/releases/latest) |

The first Renovated releases are [Heartbreaker 3.0.0](https://github.com/spedatox/speda-mark6/releases/tag/v3.0.0)
and [SPEDA Go 3.0.0](https://github.com/spedatox/speda-go/releases/tag/v3.0.0).
Enter your backend URL and owner API key in the connection screen. Public builds
contain no owner API key. Windows installers are currently unsigned; Android
releases use the existing signing identity so installed release apps can upgrade.

## What Renovated contains

| Component | Responsibility |
| --- | --- |
| **Backend** | FastAPI, authenticated HTTP/SSE and WebSocket transports, native provider protocols, configuration and durable conversation state. |
| **Heartbreaker** | Primary Electron/React desktop client: conversations, participants, streamed output, worker controls, voice preferences and owner configuration. |
| **SPEDA Go** | Native Kotlin/Jetpack Compose Android client using the same conversation, reconnect, memory and worker contracts. |
| **Iacon** | Source-linked knowledge, local recall, typed records, immutable revisions and independently reviewed background changes. |
| **Coordinator / Legion** | Shared rooms, attributed contributions, bounded workers, pinned policies, steering, cancellation and recovery. |
| **IGOR / FORGE / Workshop** | Isolated execution, durable claims, versioned checkpoints, verifiable artifacts and explicit integration/publication boundaries. |
| **Hisar integration** | Document identities, editions and retrieval with live access-grant resolution. |
| **Solus** | Deterministic accounting and ledger operations with native records, invariants and an operator UI. |
| **n8n integration** | Business scheduling: automation definitions, authenticated callbacks, reminders and watches. |

Striker remains an additional desktop client. Shared conversation changes are
checked across clients; native platform layouts and permissions remain distinct.

## Architecture

~~~mermaid
flowchart TD
    H[Heartbreaker] --> API[Authenticated API · HTTP/SSE and WebSocket]
    G[SPEDA Go] --> API
    T[Telegram webhooks] --> API
    API --> C[Coordinator and AgentOrchestrator]
    C --> P[Versioned model and spending policies]
    C --> L[Native provider transports]
    C --> M[Iacon · local context and evidence]
    M --> D[(Persistent database · original sources and native records)]
    C --> R[CapabilityRegistry]
    R --> I[MCP · OAuth · Hisar · domain services]
    R --> F[Bounded workers and isolated Forge Cells]
    F --> W[Workshop · checkpoints and artifacts]
    N[n8n · schedules and events] --> API
~~~

The orchestrator owns prompts and the answering loop. `AgentContext` carries
request authority and state. `CapabilityRegistry` owns tool discovery and grants.
The Coordinator owns admission, rooms and durable work; Iacon owns knowledge.
n8n owns scheduling.

### Memory that keeps its evidence

- The original owner message and durable intake commit together before an
  answering-provider request begins.
- Foreground recall uses local indexed reads. Routine selection makes **zero
  model, translation or embedding calls**.
- Prepared context is a disposable read view. Original records, source spans,
  attribution and temporal qualifications remain the authority.
- Owner statements, quoted material and agent contributions retain distinct
  attribution. Assistant contributions do not become owner assertions.
- A bounded `memory_lookup` can supply missing evidence within the turn's
  shared retrieval allowance.
- Orion proposes and independently reviews typed background changes. Paid
  maintenance requires persistent budget reservations. Historical imports do
  not launch bulk extraction.

Corrections, revisions and source-level revocation have explicit records.
Semantic recall quality is evaluated separately from retrieval speed; a fast
query alone does not establish answer correctness. See [Iacon](docs/IACON.md)
and the [memory design](docs/architecture/memory/IMPLEMENTATION_DESIGN.md).

### Execution with accountable outcomes

Model routes, thinking settings, persona and budget authority are pinned for
admitted work. Room speakers and workers share the root execution allowance.
Chat, coordination, speech and Iacon maintenance have explicit paid-call controls.
A worker title cannot grant additional permissions.

Tool receipts commit before effects. Lost connections and expired executor
leases produce durable uncertainty for reconciliation. Provider calls, external
sends and project effects with unknown outcomes are not silently replayed.
Stopping a reply, task and run have separate scope.

Forge workers use bounded workspace snapshots and isolated execution. Source
integration and publication are distinct operations governed by trusted host
configuration. See [IGOR/FORGE](docs/IGOR_FORGE.md), [Legion](docs/LEGION.md) and
[orchestration](docs/ORCHESTRATION.md).

## Run a development instance

### Docker

Requirements: Git, Docker Engine and Docker Compose v2.

~~~bash
git clone https://github.com/spedatox/speda-mark6.git
cd speda-mark6
cp .env.example .env
~~~

Set a unique `SPEDA_API_KEY` and the intended provider credentials in `.env`.
Keep private env files and persistent data outside Git. Then start the application:

~~~bash
docker compose up -d --build
curl http://127.0.0.1:8000/health/ready
~~~

The image includes Python, Forge, native PDF libraries and built Solus assets.
Local compose binds port 8000 to loopback and persists the database and owner
configuration in a named volume. Optional sidecars and execution-host grants
are configured separately.

Fresh installations start with chat/coordination spending paused and Iacon's
paid allowance at zero. Configure routes and budgets through **Configuration**
before making paid requests. Production retains its existing owner settings.
See [configuration](docs/CONFIGURATION.md) and [providers](docs/PROVIDERS.md).

### Python

Use Python 3.11+ and `uv`. Keep development data separate from production and
retained snapshots. This Bash example keeps the environment and data outside
the checkout:

~~~bash
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/speda/venv"
export SPEDA_DATA_DIR="$HOME/.local/share/speda-dev"
export DATABASE_URL="sqlite+aiosqlite:///$SPEDA_DATA_DIR/speda.db"
export PYTHONDONTWRITEBYTECODE=1
export EXTERNAL_INTEGRATIONS_ENABLED=false
export HTTP_HOST=127.0.0.1
mkdir -p "$SPEDA_DATA_DIR"
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m app.supervisor
~~~

The managed launcher supports **Save → Restart backend**. Saved owner overrides
in `SPEDA_DATA_DIR/configuration.json` take precedence over deployment env values.
Native policy edits apply through their versioned writers; server settings apply
on restart.

Readiness and liveness endpoints are public; owner data requires authentication.
For a local streaming request:

~~~bash
curl -N http://127.0.0.1:8000/chat/speda \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <your-local-api-key>' \
  -d '{"message":"Hello","session_id":null}'
~~~

See [transports](docs/TRANSPORTS.md) for delivery and streaming behavior.

## Build the clients

Use Node.js 24 and the committed npm lockfile:

~~~bash
npm ci
npm run heartbreaker:dev
npm run heartbreaker:typecheck
npm run heartbreaker:build
~~~

Package a Windows installer with
`npm run dist --workspace=packages/heartbreaker` on a Windows build host.
Release builds supply the public API address and leave credentials for the
connection screen.

SPEDA Go uses JDK 21 for its Gradle build, Gradle 8.11.1, Android SDK 35 and the
committed Kotlin/Compose version catalog. Its minimum Android API is 31.
See the [Android build and signing guide](packages/speda-go/README.md).
Signing material remains outside the repository. Firebase wake support requires
an app `google-services.json`; the current APK retains the existing polling
fallback because that configuration is absent.

## CI/CD and releases

The [Renovated workflow](.github/workflows/validate.yml) runs backend contracts,
SQLite/PostgreSQL checks, real Docker isolation/teardown tests, desktop behavioral
checks and Windows packaging. After validation, deployment and installer
publication proceed independently.

1. **Verify the image.** Transfer the tested archive over SSH with a pinned host
   public key. Verify its checksum, rootfs layers, runtime configuration and source
   revision, then select the host's immutable local image ID.
2. **Preserve production.** Gate writes, drain active work and retain complete
   database/config backups. Rehearse additive schema changes and the previous
   image's compatibility. Update the application while retaining data, credentials,
   policies and the existing sidecar stack.
3. **Recover without discarding writes.** A failed candidate can return to the
   previous image against the current database. Automatic rollback never copies
   an old backup over current records or private settings. Unexpected source
   changes or failed recovery keep writers gated for operator inspection.
4. **Publish the clients.** Heartbreaker uploads the installer, SHA-256 checksums
   and source metadata when backend/client validation and Windows packaging pass.
   The [Android workflow](.github/workflows/speda-go.yml) tests and signs SPEDA Go,
   mirrors tracked Android source into [spedatox/speda-go](https://github.com/spedatox/speda-go),
   and publishes the APK there.

Main deployment runs are serialized. Documentation-only updates do not redeploy
production or issue another desktop installer. CI never supplies a replacement
production dataset. Orion Spark remains stopped and disabled at the owner's request.

The Renovated version floor is **3.0.0**. See [releasing](docs/RELEASING.md) for
credentials, artifacts and rollback details, and [deployment](docs/DEPLOYMENT.md)
for the original data-preserving cutover.

## Validation

Keep generated Python scratch outside the repository:

~~~bash
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen --extra dev python -m pytest tests -q \
  -p no:cacheprovider --basetemp "$(mktemp -d)"
npm run test:workers
node --test tests/test_heartbreaker_release.mjs
~~~

CI provisions disposable PostgreSQL and runs actual Docker Cell checks.
Passing fixtures, live integration checks and full product acceptance are
tracked separately. The [cutover record](docs/deployment-validation-2026-11-03.json)
contains memory-preservation evidence; [local testing](docs/LOCAL_TESTING.md)
covers the existing Windows development harness.

## Current boundaries

Renovated is deployed; the complete Superior Mark VI acceptance contract remains
open. The owner approved shipping with these items deferred:

- Six host protocols: **Doormat, Lifeboat, Lockdown, Octavius, Skyfall and system_ops**.
- Continuous-input voice. Bounded HTTP speech input/output and voice preferences
  are available.
- Remaining project/canvas client acceptance and comparative owner-workflow checks
  tracked in the product contract.

Source-level revocation is enforced; complete semantic erasure across independent
historical copies is not implemented. Context limits use conservative UTF-8 byte
bounds rather than measured provider-token counts. Optional paid Hisar embeddings
are not enabled by default.

See [Superior Mark VI](docs/SUPERIOR_MARK_VI.md),
[cross-client parity](docs/SPEDA_GO_PARITY.md) and
[integration status](docs/integration/STATUS.md) for the remaining work.

## Repository map

~~~text
app/                    Backend, profiles, capabilities, services and native models
forge/                  Isolated execution runtime
packages/heartbreaker/  Primary Electron/React desktop client
packages/speda-go/      Native Android client
packages/striker/       Additional desktop client
packages/solus/         Solus operator frontend
scripts/                Imports, audits, release checks and deployment tooling
tests/                  Backend, transport, preservation and execution contracts
docs/                   Architecture, operational guides and validation records
.github/workflows/      Validation, deployment and client delivery
~~~

`main` contains Renovated. The original Final application is retained on
[`final`](https://github.com/spedatox/speda-mark6/tree/final); the pre-cutover main
is retained on [`legacy-main-20261103`](https://github.com/spedatox/speda-mark6/tree/legacy-main-20261103).

## Documentation

| Topic | Guide |
| --- | --- |
| Owner settings and restart | [Configuration](docs/CONFIGURATION.md) |
| Models and native thinking | [Providers](docs/PROVIDERS.md) |
| Knowledge and evidence | [Iacon](docs/IACON.md) |
| Rooms and workers | [Orchestration](docs/ORCHESTRATION.md), [Legion](docs/LEGION.md) |
| Execution and artifacts | [IGOR/FORGE](docs/IGOR_FORGE.md) |
| Accounting | [Solus](docs/SOLUS.md) |
| Schedules and watches | [Automations](docs/AUTOMATIONS.md) |
| Desktop recovery | [Heartbreaker reliability](docs/HEARTBREAKER_RELIABILITY.md) |
| Shared client contracts | [SPEDA Go parity](docs/SPEDA_GO_PARITY.md) |
| Production operation | [Deployment](docs/DEPLOYMENT.md), [Releasing](docs/RELEASING.md) |

## License

[GNU Affero General Public License v3](LICENSE). Source files include their
copyright and SPDX notices.
