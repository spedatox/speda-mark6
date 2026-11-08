# Renovated deployment and data preservation

Build from the flat Renovated checkout. The original monorepo deployment script
and its automatic SSH workflow must not deploy this layout. CI validates the
candidate; switching the live service is a separate operation.

## Build and validate

`scripts/package_release.py <new-external-directory>` creates an allowlisted
source archive and a SHA-256 manifest, including uncommitted application files.
It excludes owner databases, environment files, keys, links, dependencies and
build outputs. Retain the manifest with the resulting immutable Docker image ID.

Build `Dockerfile` with `--target production`; `--target test` adds the test
dependencies and cases to the same runtime. Python, Node and uv bases are pinned
by verified digests. The image contains Forge, native PDF libraries and built
Solus assets. Health checks use the database readiness endpoint. The default
Compose binding is loopback.

Run the full suite in the Linux test image. Enable `SPEDA_TEST_POSTGRES_URL`
against a disposable loopback PostgreSQL database to exercise the concurrency
contracts. Tests create and drop their own schemas. Real Docker release tests
require `SPEDA_TEST_CELL_ROOT` at an identical host/container path and
`SPEDA_TEST_CELL_IMAGE` pointing to an already provisioned image. Give only that
trusted test process the Docker socket. Workers receive neither the socket nor
owner data. Do not treat passing fixture providers as live model acceptance.

Native coordination write transactions acquire SQLite's writer slot before
reading mutable run/worker state. This avoids stale WAL read upgrades and waits
up to five seconds for another short writer before any effect is dispatched.
Foreground memory reads keep their independent read-only transactions and
deadlines. An uncertain effect still requires reconciliation; the application
does not replay a failed write transaction around a provider or tool call.

## Memory preparation

Use the latest production database as the authoritative source. The owner chose
to retain the local Renovated testing database as a separate backup, rather than
merge its conversations and preferences into production.

1. Capture SQLite with `python -m scripts.rehearse_sqlite snapshot <live-db>
   <new-retained-snapshot>`. This uses SQLite's backup API and includes committed
   WAL changes. It checks snapshot integrity and refuses an existing destination.
2. Run `python -m scripts.prepare_cutover --snapshot <retained-snapshot>
   --destination <new-candidate-db> --report <new-private-report>` in the
   production image with only the rehearsal directory mounted. Network access
   can be disabled. It clones every legacy table, applies reviewed additive
   schema changes, adds source-linked native memory and retains all originals.
   It performs no model extraction, historical tool replay or delivery.
3. Check the report's original-table row digests, memory counts and review
   exceptions. Compare hashes and source references, rather than substituting
   an older full database. Native memory includes original documents, entity
   identities, typed states, owner conversation evidence and attributed
   observations. Derived, archived, inferred and invalid temporal material retain
   their existing serving restrictions.
4. Replay actual owner questions with the candidate. Verify identity, attribution,
   chronology, corrections, source exclusions and automatic/lookup byte ceilings.
   The legacy user display name may name the assistant; relative family routing
   uses the explicitly identified canonical owner dossier without rewriting it.

Reports, memory packets and snapshots contain private information. Keep them
outside Git and the source build context. A snapshot captured during preparation
is rehearsal evidence; it must not replace a database that has received later writes.

## Isolated startup and rollback rehearsal

Run the production image with the candidate DB, a new state directory, no real
provider credentials, external integrations disabled and memory capture disabled.
No production paths or sidecar volumes should be writable from this container.
`python -m scripts.release_smoke --exercise-candidate` checks authenticated
configuration, original history reload/export, models, memory and static UIs,
then performs a synthetic memory write, idempotent replay and managed restart.
Use the write/restart option only on an isolated disposable candidate.

Retain the current production image ID, deployment configuration, complete
database snapshot/journals and configuration/output directories. Rehearse restoring
a separate copy of the original snapshot and booting the retained old image
against that copy. Verify health and original history. Never run either image
against the other's open database.

## Final cutover

Inspect the actual deployed database path, all database writers, mounts, reverse
proxy and sidecar network. Prepare a private env file for native settings and
review model/thinking policies, OAuth credentials and scheduler callbacks.
Do not copy the old managed configuration file into the new format unexamined.
Start with paid work and project execution paused until their activation checks pass.

Stop every writer for the final snapshot and delta check. Repeat preparation
from that final snapshot, validate preservation and make the same-volume atomic
switch while writers are stopped. Preserve the prior DB and journals together;
never leave stale WAL/SHM files next to a replacement. Retain the failed candidate
as well if rollback becomes necessary. Keep the write window closed until health,
history, memory reads and a controlled new write pass. Rollback after reopening
writes requires reconciling those new writes first.

`docker-compose.production.yml` connects the verified image to an existing
sidecar network and mounts explicitly selected database/state/output directories.
It does not recreate or prune the existing sidecars or attach the host Docker
socket. Project execution requires a separately reviewed execution-host grant,
identical host/container artifact paths and verified Cell teardown. Verify TLS,
SSE/WebSocket proxying and the deployed release identity before opening traffic.

`docs/SUPERIOR_MARK_VI.md` remains the product acceptance contract. Packaging,
schema preservation and local tests do not waive incomplete owner workflows,
external service activation or the comparative acceptance gate.

## Production release, 2026-11-03

The owner authorized deployment with the six host protocols and continuous-input
voice deferred. The initial cutover image was
`sha256:70b5737b9588cb2544dd0e37557b3afd55ae398bc230aaa4d90dd61d369b4970`.
The private release directory is
`/opt/speda-operations/releases/renovated-20261103-05`; its `deploy.env`,
`compose.yml` and `execution-host.yml` describe the inspected live deployment.
The separate execution override retains the previous Docker/workspace grants
and pins each worker role to its existing installed image digest.

Public writes were gated at Caddy before stopping n8n, the old application and
its host watchdog. A fresh SQLite backup retained 104 original tables and 484,398
rows unchanged, including four messages added after the initial rehearsal.
All 207 original memory files remain; native views contain 207 documents,
60 entities and 28 states. Local testing memory was retained separately.
The original `/opt/speda/speda.db`, container and image remain available.

Private settings, OAuth credentials, FCM credentials, model/voice pins and all
15 automation definitions were carried over. Four definitions already referred
to deleted n8n workflows and are marked for reconciliation without recreation.
Two existing mailbox workflows received native callback identities; their
schedules, connections, names and IDs were retained. Existing Gmail seen labels
remain excluded, preventing historical mail replay. Eight Telegram bots use
authenticated webhooks with retained polling cursors and no pending-update drop.

Chat and coordination retain production's active operation without a newly
imposed admission cap. Iacon's separate paid maintenance allowance is 200 calls,
2,000,000 conservatively reserved tokens and $1 per day; historical imports do
not enqueue bulk extraction. Maintenance Telegram prompts remain disabled.

Orion Spark was stopped and disabled at the owner's request. Leave it disabled.
Its retained systemd configuration and release files must not be used to restart
it without a later owner instruction. Docker's application restart policy remains
enabled. Health, authenticated history, live memory-backed chat, arithmetic,
speech and restart persistence were verified before reopening public traffic.

The private `rollback-before-opening.sh` refuses rollback once `traffic-opened`
exists. Production writes are now open: reconcile all new writes before restoring
the legacy dataset. Keep both database generations and private configuration
snapshots. The release record is `deployment-validation-2026-11-03.json`.

## Damage Control Protocol

Set `DAMAGE_CONTROL_ENABLED=true` and configure the SPEDA Telegram bot. The backend
captures agent failures, interruptions, recognized tool errors, agent-reported
anomalies, application warnings/errors and selected container lifecycle failures.
Turn-ending incidents take priority. The configured background model receives
structured incident facts under the global spending policy. Reports go to the
paired private owner chat through the SPEDA bot.

The local journal is `$SPEDA_DATA_DIR/damage-control/incidents.sqlite3`. On backend
restart it recovers unfinished executions and queues their reports. Reports use
recorded facts if model analysis is unavailable. Telegram uploads without a
verified acknowledgement remain `unknown` for inspection. A missing owner link
holds the report until pairing is available. Set `DAMAGE_CONTROL_ENABLED=false`
and restart to disable capture and delivery.
