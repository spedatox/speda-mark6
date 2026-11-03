# Heartbreaker: source-informed live mission reliability

Implemented and reviewed locally on 27 October 2026. See [IGOR_FORGE.md](IGOR_FORGE.md)
for the execution, approval and recovery architecture. This review extends the
existing Heartbreaker and Striker clients; it introduces no competing orchestrator,
worker scheduler, or copied upstream execution engine.

## Upstream source inspected

Read-only sparse checkouts were inspected outside the application repository.
These are pinned source snapshots, not claims about proprietary desktop internals
or guarantees of upstream product equivalence.

| Source | Inspected behavior | Mark VI implementation |
| --- | --- | --- |
| [Codex turn processor](https://github.com/openai/codex/blob/f088e0c4ffdfab3a7d4480c14855a9db00450384/codex-rs/app-server/src/request_processors/turn_processor.rs) | Steering requires an expected active turn and preserves client input identity. | Optional expected AgentStep fence for compatible clients; Heartbreaker supplies it. Durable identical-input acknowledgment is resolved before checking the currently running step. |
| [Codex TUI user submissions](https://github.com/openai/codex/blob/f088e0c4ffdfab3a7d4480c14855a9db00450384/codex-rs/tui/src/app/user_turn_submission.rs) and [event handling](https://github.com/openai/codex/blob/f088e0c4ffdfab3a7d4480c14855a9db00450384/codex-rs/tui/src/app/app_server_events.rs) | Steering acknowledgment and visible chat routing have different lifetimes. | `useLiveSteering` captures the original session, specialist, endpoint, request, step and immutable input ID. Late acknowledgments cannot echo into a different chat. Ambiguous retries reuse the input ID; simultaneous identical sends share one request. |
| [Codex thread listeners](https://github.com/openai/codex/blob/f088e0c4ffdfab3a7d4480c14855a9db00450384/codex-rs/app-server/src/thread_state.rs) and [turn admission](https://github.com/openai/codex/blob/f088e0c4ffdfab3a7d4480c14855a9db00450384/codex-rs/app-server/src/turn_admission.rs) | Listener replacement, cancellation and admission are explicit lifecycle boundaries. | One abortable worker feed per selected session. Old asynchronous results are fenced before store updates. Coordinator/Workshop remain admission and effect authorities; transport cancellation does not restart execution. |
| [Open WebUI background tasks](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/tasks.py) | Persisted response snapshots and bounded delta accumulation survive client disconnection. | Authenticated bounded session snapshot plus durable room-event replay. The replay floor is captured before projection reads, producing safe overlap instead of a recovery gap. |
| [Open WebUI chat component](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/src/lib/components/chat/Chat.svelte) | Rejoin/reload on connection, chat-identity checks after awaits, merge live events during history loads, distinguish background task state from public response completion. | `useWorkerStreams` recovers before subscribing, checks selected session identity, merges by stable parent step and execution ID, and keeps worker activity independent of the public reply/composer. |

Codex revision: `f088e0c4ffdfab3a7d4480c14855a9db00450384`.
Open WebUI revision: `33c1978d5bad52a31df75ea55143025dfad18d16`.
The Codex sources inspected are its public Rust app-server and TUI. No upstream
source code was executed or copied into product implementation.

## Contracts now exercised

- **Background execution survives reply completion.** Both desktop clients use
  `GET /legion/sessions/{id}/snapshot` followed by
  `GET /legion/sessions/{id}/attach?after={sequence}`. Supervisor assessments
  arriving after worker completion remain reachable in the task journal and feed.
- **Recovery never dispatches work.** The feed performs authenticated GETs only.
  A stream failure recovers the canonical snapshot with bounded backoff. A 30-second
  inactivity watchdog recovers stalled connections; terminal permission/protocol
  failures are visible and do not enter a rapid retry loop.
- **History cannot erase newer execution evidence.** Parent AgentStep/message IDs,
  monotonic room sequences and event IDs govern merging. Events arriving before
  history loads remain pending. A stopped reply can have an explicitly operational
  worker anchor, which merges into the real reply when available; it is not a
  fabricated persisted assistant answer.
- **Steering cannot silently retarget.** The server rejects a mismatched expected
  step. The client retains unknown-outcome IDs, has a 15-second acknowledgment
  timeout, and keeps drafts on failure. A successful old acknowledgment clears
  only the exact submitted draft revision in the same chat and specialist. New
  instructions still enter the existing safe reasoning/tool boundaries.
- **High-rate output has bounded client cost.** Feed writes coalesce every 16 ms.
  The cache holds at most 128 execution records; each preview retains at most
  100 steps and 128 event IDs. Large text/previews are explicitly partial. The
  server snapshot is capped near 1 MB with an omitted count; detail replay reads
  the retained journal without restarting the task.
- **Visibility remains scoped.** Snapshot and attachment resolve ownership and
  native chat binding, reject hidden sessions, and omit hidden parent-message
  workers. Provider continuation state is never projected to the client.
- **Consequential publication retains owner control.** The existing approval tray
  presents exact release-plan hashes. Models can prepare and assess, but cannot
  approve; remembered publication approval is refused. Unknown effects remain
  held for explicit reconciliation.

Primary implementation: `app/services/mission_control.py`,
`app/services/worker_control.py`, `app/services/project_release.py`, and the
clients' `lib/useWorkerStreams.ts`, `lib/useLiveSteering.ts`,
`lib/workerTransport.ts`, `lib/workerState.ts` and `store/chat.ts`.

## Dependency and rendering hardening

The initial resolved npm graph reported 35 advisories, including two critical.
The final lockfile audit reports **zero known vulnerabilities** as of this review;
this is a registry-advisory check, not proof of security.

Both clients now pin MapLibre 6.4.1, Electron 42.11.18, electron-builder 26.18.0,
Vite 6.4.5, electron-vite 3.1.0 and KaTeX 0.19.1. The lockfile also contains
compatible transitive patches. Root overrides explicitly align rehype-katex and
micromark-extension-math with the patched KaTeX; the actual Markdown/math rendering
path is covered by a regression test, including refusal of untrusted HTML commands.
No esbuild override is needed for the upgraded Vite toolchain.

MapLibre's [attribution advisory](https://github.com/advisories/GHSA-jrc7-96c5-q579)
motivated the patched map runtime. The build chain also moved past affected
[node-tar releases](https://github.com/advisories/GHSA-23hp-3jrh-7fpw).
Map popup labels/subtitles now use DOM `textContent`/text nodes and `setDOMContent`
instead of interpolated HTML in both clients. MapLibre imports were adapted to
the new module exports without removing map or aircraft capabilities.

## Evidence and limits

`npm run test:workers` bundles the actual React hooks, store reducer and transport
for **both** clients, then uses the Node test runner and React test renderer.
Eighteen cases per client cover protocol fragmentation, malformed/oversized
frames, cleanup, reconnect, stale/history races, completion/composer independence,
hidden identity mismatch, replay, a 1,000-event burst, steering acknowledgment
races, draft preservation and math rendering. These are behavioral component
tests, not screenshots or a packaged desktop end-to-end certification.

Backend tests exercise the actual Coordinator, registry, provider adapter,
FORGE Warden and durable records with fixture providers and local deployment
subprocesses. Details and exact run results are in
[igor-forge-validation.json](igor-forge-validation.json).

Both clients pass TypeScript checks, Electron builds and web builds with the
updated dependencies. The downloaded Electron binary reports 42.11.18. Existing
Samsung font-resolution and large-bundle warnings remain; signed installers,
GPU/map rendering and visual interaction still need packaged acceptance.

Docker and Java are unavailable on this host. Live Docker isolation, real paid
provider behavior, PostgreSQL concurrency, Android visual acceptance and actual
external deployment/rollback remain explicit release gates. A broad backend run
also encountered two PDF-generation failures because the host lacks the native
Pango/GObject libraries. No production server or owner dataset was changed.
These changes improve the real execution paths; they do not justify a claim of
perfect behavior or complete industrial production certification.
