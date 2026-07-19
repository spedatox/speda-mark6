# Background Ops — Non-Blocking Dispatch & Survivable Turns

**Goal (the owner's two asks):**

1. **Background agent dispatch.** When SPEDA dispatches a task to another agent,
   the dispatch can run in the background — SPEDA finishes its own turn
   immediately and stays fully usable while the specialist works.
2. **Turns survive navigation.** A running turn (any agent — SPEDA chat, Optimus
   coding via the Forge, a dispatched chain) is never killed by switching
   sessions, switching agents, the stop-watchdog, or a renderer reload. The
   result is always persisted, and returning to the session re-attaches to the
   live stream or shows the finished answer.

**Repo:** `C:\Users\AREL TARIM\speda-mark6` (backend `packages/igor`, UI
`packages/heartbreaker`). CLAUDE.md rules apply throughout — especially Rule 1
(no logic in routers), Rule 6 (everything on `app.state` via lifespan), Rule 7
(background work never blocks the stream).

---

## 0. Established facts — do NOT re-derive these

1. **Dispatch is synchronous today.** `dispatch_agent`
   (`app/skills/dispatch.py`) calls `AgentDispatcher.dispatch()`
   (`app/core/dispatch.py`) and **blocks the caller's tool call** until the
   target agent's full orchestrator run completes (or, for external peers, until
   the `task_result` frame / timeout — Optimus gets 600 s). Parallelism exists
   only *within* one turn (multiple `dispatch_agent` calls in one assistant
   turn run concurrently). The caller's turn cannot end while a dispatch runs.

2. **Dispatch telemetry already exists and is the right substrate.** Every
   dispatch writes an `agent_messages` row: status `"running"` at start
   (`_log_start`), then `ok`/`error`/`timeout` + result + duration at finish
   (`_log_finish`). The comms tray polls `GET /agents/comms` incrementally
   (`after_id`), and `channel_transcript()` already renders running entries as
   `"… (still working)"`. Background mode mostly needs a *spawn* path, not new
   plumbing.

3. **The fatal flaw for ask #2 is server-side: persistence lives inside the SSE
   generator.** In `app/routers/chat.py::_run_chat`, `generate()` consumes the
   engine and only after the loop ends calls `session_manager.save_message(...)`.
   When the HTTP client disconnects (renderer reload, app close, watchdog abort,
   stop button), Starlette cancels the generator → the orchestrator run is
   cancelled mid-flight → **nothing is saved**. The turn is genuinely lost.

4. **The frontend does NOT abort on switching — but it cannot re-attach.**
   - `SELECT_SESSION` / agent switch never call `abort()`; the fetch loop in
     `ChatMain.send`'s closure keeps consuming the stream invisibly, and the
     reducer no-ops events for message IDs no longer in view (`FINISH_MESSAGE`
     has an explicit absent-message guard).
   - So a switched-away turn *completes* today **if** the renderer stays alive
     and the watchdog doesn't fire — but the user sees no live progress, no
     "busy" indicator on the session, and must manually re-enter the session
     after completion to load the saved answer. Any reload kills it entirely
     (fact 3).
   - `ChatState.isStreaming` is a **single global flag** and `abortRef` a single
     ref: `SELECT_SESSION` resets `isStreaming:false`, so a second concurrent
     send is already possible, but the stop button then only controls the newest
     stream. There is no per-session run tracking.
   - The stream watchdog (`DEAD_MS` = 5 min idle) aborts the fetch — with fact 3
     that currently kills the backend run too.

5. **SSE events already carry `session_id` and `request_id`**
   (`app/schemas/sse.py`, the `start` event includes the session id — the UI
   uses it in `FINISH_MESSAGE`). Correlating streams to sessions needs no new
   event vocabulary.

6. **DB session lifetime pitfall:** `_run_chat` uses the request-scoped
   `Depends(get_db)` session inside the generator. A detached run **must not**
   use it — open its own `AsyncSessionLocal()` (exactly as
   `AgentDispatcher._run_in_process` already does).

7. **`schedule_background_tasks` (title, memory, maintenance)** is attached to
   the router's `BackgroundTasks`, which fire when the *response* finishes —
   after detachment that's before the turn completes. It must move to
   turn-completion.

8. **Dev-mode caveat:** `speda.ps1` runs uvicorn with `--reload`; a source-file
   change still restarts the process and kills every run. That is dev-only and
   out of scope.

---

## Phase 1 — Backend: detached turn runner (the survivability core)

New module `app/core/turn_runner.py` — a `TurnRegistry`, one instance on
`app.state.turns`, created in the lifespan (Rule 6).

**Contract:**

```python
class TurnRegistry:
    def start(self, *, context, engine_factory, on_finish) -> TurnHandle
        # Runs the engine in an asyncio.create_task, DETACHED from any HTTP
        # request. Events are appended to a bounded replay buffer (deque,
        # cap ~2000 events / 2 MB) and fanned out to live subscriber queues.
    def subscribe(self, request_id) -> AsyncIterator[SSEEvent]
        # Replays the buffer from the start, then tails live events. Multiple
        # subscribers allowed; unsubscribing NEVER affects the run.
    def active(self, *, agent_id=None, session_id=None) -> list[TurnInfo]
        # {request_id, agent_id, session_id, started_at, last_event_at}
    async def cancel(self, request_id) -> bool
        # Cooperative task cancel. Persists partial text with a
        # "[cancelled by owner]" marker before finishing.
```

**Rules for the runner task:**
- Opens its own `AsyncSessionLocal()` for persistence (fact 6). The
  `AgentContext.db` passed to the engine must also be a runner-owned session,
  not the request's.
- Persistence (the `collected_chunks` / `_speda_meta` assembly currently in
  `generate()`) moves verbatim into the runner's completion path — it runs
  whether or not anyone is subscribed.
- `schedule_background_tasks` moves to the runner's `on_finish` (fact 7).
- Finished turns stay in the registry for a short grace window (~60 s) so a
  client that reconnects right after completion still gets the replay + `done`
  instead of a 404; then evicted (the DB is the source of truth).
- Registry is bounded (max ~8 concurrent turns); exceeding it returns a
  friendly SSE error. Lifespan shutdown cancels all tasks gracefully.

**Router changes (`app/routers/chat.py`) — thin, per Rule 1:**
- `_run_chat` builds the context exactly as now, then:
  `handle = app.state.turns.start(...)` and returns
  `StreamingResponse(app.state.turns.subscribe(handle.request_id))`.
- New `GET /chat/attach/{request_id}` → `StreamingResponse(subscribe(...))`
  (replay + live tail; 404 after eviction).
- New `GET /chat/active` → `registry.active()` filtered by optional
  `agent_id` / `session_id` query params.
- New `POST /chat/cancel/{request_id}` → `registry.cancel(...)`. The stop
  button must use this — dropping the socket no longer cancels anything.

**Acceptance (Phase 1):** start a turn, kill the client fetch mid-stream →
backend log shows the run completing and the assistant message is saved;
`GET /chat/attach/{request_id}` from `curl` replays the whole stream;
`POST /chat/cancel` stops it and persists the partial.

## Phase 2 — Backend: background dispatch mode

**`dispatch_agent` gets a `background: boolean` param (default `false`).**
Schema + description updated in `app/skills/dispatch.py` (keep Rule 11 quality:
say when to use it — long research, coding jobs, anything the owner shouldn't
wait for — and that the result lands in the agent channel / comms tray).

**`AgentDispatcher.spawn()`** — new method next to `dispatch()`:
- Writes the `agent_messages` row first (status `"running"`, existing
  `_log_start`), then `asyncio.create_task(self._run_and_finish(...))` which
  reuses the *existing* `dispatch()` internals and `_log_finish`.
- Tasks tracked in a `self._background: set[asyncio.Task]` (discard-on-done);
  cap ~5 concurrent background dispatches — over the cap, return a refusal
  string the model can relay. Lifespan shutdown cancels them.
- Returns **immediately** with a ticket string:
  `"Background dispatch #<msg_id> → NIGHTCRAWLER started. It keeps working
  after this turn ends; check with dispatch_status(<msg_id>) or the agent
  channel. Tell the owner it's running."`
- `broadcast()` honours the same flag (one background task per target).
- External peers (the Forge) need zero changes: `_run_external`'s
  future/timeout simply lives inside the background task instead of the
  caller's turn.

**New `dispatch_status` skill** (same file):
- Input: optional `id` (the ticket) or `agent`; no args = all non-final +
  recently-finished dispatches initiated by the calling agent.
- Reads `agent_messages` and returns status/duration/result (result truncated
  to the existing `MAX_RESULT_CHARS` discipline). Read-only annotated.

**Completion visibility — deliberately minimal in v1:**
- The comms tray already shows the row flipping `running → ok` (fact 2) — the
  owner sees completion without any new backend surface.
- SPEDA learns results by calling `dispatch_status` / `read_agent_channel` on
  a later turn. **Do NOT auto-inject "since your last turn" blocks into the
  conversation history** — SessionManager's byte-identical prompt-prefix
  reconstruction is what keeps the prompt cache valid; injected ephemera would
  bust it every turn. If proactive delivery is wanted later, it's a push
  notification (`NotificationsSkill`) fired from `_run_and_finish`, never a
  history mutation.

**Acceptance (Phase 2):** SPEDA dispatches NightCrawler with `background:true`
→ SPEDA's answer streams to completion within seconds while the comms tray
shows NIGHTCRAWLER running; a follow-up "ne durumda?" turn gets the real result
via `dispatch_status`; five concurrent background dispatches work, a sixth is
refused politely.

## Phase 3 — Frontend: per-session runs + re-attach

All in `packages/heartbreaker` (never `packages/desktop`).

**Store (`store/chat.ts`):**
- Replace the global `isStreaming` with
  `activeRuns: Record<number /*sessionId*/, { requestId: string; assistantId: string }>`
  plus a transient slot for a not-yet-numbered new chat (the `start` event
  delivers the real `session_id` — fact 5 — move the entry under it then).
- `SELECT_SESSION` stops resetting streaming state; the send-guard becomes
  per-session ("this session is already working"), so working in another chat
  while one runs is a feature, not an accident.

**ChatMain:**
- `send` registers the run in `activeRuns` when `start` arrives and clears it
  on `done`/`error`. Reducer event routing is already id-based and
  switch-safe (fact 4) — keep it.
- On entering a session (`handleSelectSession` in Layout): after
  `fetchMessages`, call `GET /chat/active?session_id=` — if a run is live,
  append a streaming assistant bubble and consume `GET /chat/attach/{request_id}`
  through the same event loop as `send` (factor the for-await body into a
  shared `consumeStream(assistantId, sessionId, response)` helper; replayed
  chunks arrive in one burst, the existing rAF coalescing absorbs that).
- Stop button → `POST /chat/cancel/{request_id}` for the *visible* session's
  run (then also abort the local fetch). The watchdog's give-up path calls
  cancel too — with Phase 1, an abandoned fetch alone no longer kills the run,
  so the watchdog must be explicit about intent.
- Agent switch needs no special handling: runs are server-side now; the
  closure-kept fetch can simply be dropped (abort locally WITHOUT cancel) since
  re-attach recovers it.

**Sidebar / visibility:**
- Sessions with an entry in `activeRuns` (or reported by a light
  `GET /chat/active` poll, ~8 s, piggybacked where sessions are fetched) get a
  small pulsing jewel in the session list — the Stark cue that something is
  cooking there. Same jewel on the agent cards in `AgentSwitcherOverlay` for
  agents with active runs.

**Acceptance (Phase 3):** start a long Optimus/Forge job → switch to SPEDA,
chat normally (both streams alive) → switch back: the Optimus session shows the
live stream continuing (replayed seamlessly); reload the entire app mid-run →
re-enter the session → stream re-attaches; stop button cancels only the visible
session's run.

## Phase 4 — Comms tray polish (small)

- `CommsTray` already renders rows; give `status === "running"` entries a
  pulsing amber state and a live duration counter so background dispatches are
  visibly *alive*, and flip to the result inline when the poll delivers it.
- Optional: a compact "N running" badge on the COMMS header button while any
  dispatch is running (data already in the poll).

## Phase 5 — Verification (all must pass)

1. `pytest` green in `packages/igor`; new registry unit tests: replay ordering,
   subscriber isolation, cancel-persists-partial, eviction, concurrency cap.
2. Client-disconnect test (curl, kill mid-stream) → answer still saved.
3. The Phase 2 and Phase 3 acceptance scenarios, driven end-to-end in the
   preview/Electron app.
4. Dispatch depth guard still holds in background mode (a background-dispatched
   agent dispatching further respects `MAX_DISPATCH_DEPTH`).
5. Prompt-cache identity: a session's reconstructed history is byte-identical
   before/after these changes (no ephemeral injection crept in).

---

## Risks & guardrails

- **Memory:** replay buffers are bounded and evicted; never buffer attachments
  (they're in the request, not the stream).
- **Two subscribers, one truth:** the reducer's id-based no-op routing is what
  makes duplicate/stale events harmless — don't "optimize" it away.
- **Don't cancel on unsubscribe.** The single most important behavioural change:
  connection teardown is an *unsubscribe*, cancellation is only ever the
  explicit endpoint.
- **Rule 7:** title/memory extraction stays out of the stream path — it moves to
  the runner's completion callback, still as fire-and-forget tasks.
- **No new `triggered_by` values, no internal schedulers** — background
  dispatches are still `triggered_by="agent"` turns; n8n remains the only
  scheduler.
- **Windows dev:** all asyncio-task based; nothing platform-specific. Don't
  touch `packages/desktop`.
