# Owner configuration

Configuration is available in Heartbreaker, Striker and SPEDA Go. GET `/config`
builds its catalog directly from `Settings.model_fields`: all 121 current fields
appear exactly once, and future declared fields are included automatically.
The catalog includes provider credentials/endpoints, startup model defaults,
MCP and OAuth application settings, Iacon/Hisar, storage and network bindings,
Telegram/n8n, speech, domain providers and existing execution settings.
This does not implement the deferred project/canvas UI or revive retired engines.

The model and runtime settings section presents the four native policies as
normal settings: labeled number and text fields, switches, dropdowns, and
add/remove controls for named entries and ordered lists. No JSON editing is
required. Nested model capabilities and price entries remain available.

- Models: routes, per-agent/role defaults, native thinking, output and tool limits.
- Coordination: rooms, workers, run budgets and admitted model prices.
- Global: daily/lifetime spending, concurrency and speech resource prices.
- Iacon: extraction/review/maintenance limits and declared prices.

Policy writes use the existing native writer and expected revision. They apply
without a backend restart; previously admitted work keeps its pinned policy.
Run and worker limits includes a Per-run limits group with a Reserved token
limit field. Its help explains that reservations include request size, maximum
response and reasoning allowance, and that per-run bounds still apply with
spending checks disabled. Currency fields state their microdollar unit.
Optimus and Scourge runs, including their delegated workers, skip the shared
per-run model call cap. Token, time, concurrency, and configured spending
limits still apply. The exemption is pinned when a run is admitted.
Completed calls with a full provider-reported chat usage total count at that
total toward an exempt run's token limit. In-flight and unconfirmed calls,
daily allowances, and spending checks use the conservative reservations.
Invalid numbers cannot be saved. Failed saves and reloads retain drafts together
with their original revision; a revision conflict requires discarding the stale
draft and using the latest saved values, so a reload cannot silently authorize
overwriting another client's changes. English and Turkish controls are available
in Heartbreaker, Striker and SPEDA Go.
Configuration links to connection grants, personalities, automations, voice pins
and tuning, reminders and general client settings. Striker now exposes the
existing personality, voice and reminder editors too. Canonical memory/accounting
records retain their dedicated domain writers.

## Save and activate

Server settings use one atomic, versioned private file:
`SPEDA_DATA_DIR/configuration.json` (default `~/.speda/configuration.json`).
Source precedence is explicit constructor values, owner overrides, environment,
`.env`, secret files, then defaults. Constructor overrides are intended for tests.
Docker persists owner overrides in `/data` on the existing data volume.

PUT `/config` accepts `values`, optional `reset`, `expected_revision` and a stable
`request_id`. The whole change is validated before writing. A stale revision
returns 409; duplicate request IDs cannot overwrite a different payload. The
clients retain failed drafts, offer a reload, and never resend unchanged secrets.
New saved settings take effect together on restart; existing service clients
keep their original configuration until then. The panel shows saved desired
values and tracks which differ from the running instance.

Secrets are returned only as a set/unset flag and a fixed mask. No last characters
or submitted values appear in configuration validation errors. An explicitly
empty credential disables it. **Reset saved override** removes the owner value
and falls back to the deployment environment/default after restart. Prior file
revisions are retained under `configuration-history/`; those backups contain
credentials, so keep the entire data directory private and outside source control.
On POSIX, configuration files are created with mode 0600 and directories 0700;
Windows uses the private data directory's inherited ACL. Stored values are not
encrypted by this feature. Malformed stores fail closed for operator repair.

Changing the database URL switches the selected data source; it does not migrate
or replace data. Changing the API key, address, port or CORS origins may require
updating the client connection or container/reverse-proxy network mapping.
The language switches stage `agent_language`; backend prose changes on restart.
Blank follows the conversation; `en` and `tr` pin English and Turkish prose.
Per-call speech locale and per-agent voice preferences remain separately editable.

## Restart

Run `python -m app.supervisor`, as the Docker image now does. Direct uvicorn
launches report restart unavailable. POST `/config/restart` requires owner auth,
the saved revision and a stable request ID. It is not exposed as an agent tool.

Restart refuses known active rooms, Forge tasks, running Iacon jobs and in-flight
paid calls. Saved settings remain intact on refusal. Restart and room/paid-call
admission share a lifecycle lock. After acceptance new mutating requests and new
paid admissions are refused. The response schedules graceful uvicorn shutdown;
the child exits 75 and the managed parent starts a fresh child. Ordinary crashes
are not silently retried. Existing durable uncertainty/receipts remain in place.

The UI polls only readiness, never repeatedly POSTs restart. Success requires
`/health/ready` to return a **different instance ID** and a working database.
A lost-response retry with the same restart ID after boot returns
`already_restarted` and does not restart the new instance again. Readiness has a
bounded wait and shows an actionable connection error if confirmation fails.
Supervisor stop signals are forwarded to its owned child with a bounded wait.

Local validation includes full catalog coverage, masking, atomic concurrent
saves, stale revisions, restart deduplication/busy refusal, native policy writes
and a real disposable child restart with settings surviving the new process.
No existing owner backend or production service was restarted during development.
