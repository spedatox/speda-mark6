# Speda GO

The native Android client. Kotlin, Jetpack Compose. Package id `com.speda.heartbreaker` — intentional, matching the desktop client's Electron app identity rather than the "Speda GO" branding; it doesn't get renamed.

---

## Contents

- [Directory structure](#directory-structure)
- [Build setup](#build-setup)
- [Networking](#networking)
- [Key screens](#key-screens)
- [Projects](#projects)
- [Surviving a dropped connection](#surviving-a-dropped-connection)
- [Health sync](#health-sync)
- [Push notifications](#push-notifications)
- [Signing and release](#signing-and-release)

---

## Directory structure

Under `app/src/main/kotlin/com/speda/heartbreaker/`:

| Path | Contents |
|---|---|
| `data/` | `IgorApi.kt` (the backend client), config storage, offline message cache, Android Keystore-backed credential encryption |
| `domain/` | Pure Kotlin chat models and state, ported from the web client — markdown prep, streaming segmenters, spec parsers for the map/bus/chart/calendar/aircraft rich-content blocks |
| `health/` | Health Connect integration |
| `i18n/` | English and Turkish locale strings |
| `push/` | Firebase Cloud Messaging service and device registration |
| `ui/chat/` | The chat screen and its view model |
| `ui/comms/` | Inter-agent traffic viewer |
| `ui/prose/` | Rich-content renderers — SVG, map, chart, calendar, bus, aircraft, code, and math blocks |
| `ui/settings/` | One tab per settings area — account, automations, connections, protocols, reminders, voices, health, interface |
| `ui/projects/` | The projects surface — the workspace grid and one project's detail pane |
| `ui/shell/` | App chrome — header, sidebar, welcome view |
| `ui/skyfall/` | The Skyfall protocol's full-screen countdown |
| `ui/switcher/` | The agent switcher overlay |

---

## Build setup

Kotlin 2.1.0, AGP 8.9.0, Compose BOM 2025.01.00. `compileSdk`/`targetSdk` 35, `minSdk` 31, Java 17 target. Networking is plain OkHttp — no Retrofit, no Ktor.

```bash
./gradlew :app:assembleDebug     # debug APK
./gradlew :app:assembleRelease    # release APK — needs signing env vars, see below
./gradlew :designsystem:testDebugUnitTest :app:testDebugUnitTest   # unit tests
```

---

## Networking

`data/IgorApi.kt` builds every request against a configured base URL and sends `X-API-Key`. Server-sent events are read line-by-line over a client with no read/call timeout — a watchdog owns liveness instead, matching how the desktop client handles long-running streams.

It covers the full backend surface: chat streaming and cancellation, sessions, budget mode, OAuth connections, automations, inter-agent comms, the named host protocols, pending owner approvals, per-agent and per-worker model routing, memory files with conflict detection, chat history import, Atomix health sync, custom MCP servers, web portals, and voice tuning.

---

## Key screens

- **Chat** — the primary surface; a Kotlin port of the same send/stop/reattach pipeline the desktop client uses, including a pending-asks tray for owner approvals surfaced directly to the phone.
- **Agent comms** — inter-agent dispatch traffic.
- **Settings** — one tab per area, mirroring the desktop settings modal.
- **Projects** — named workspaces, each owning its own chats, standing
  instructions and knowledge base. A full-screen sheet over the transcript that
  back closes, exactly like settings; reached from the Projects row in the sidebar
  drawer, or straight into one project from the pinned shelf beneath it.
- **Skyfall** — the full-screen arm/fire/abort countdown.

---

## Projects

Ported from the desktop's `ProjectsView` under the cross-client parity rule, into
one column because the phone has one. The contracts are Igor's and are documented
there ([IGOR.md](../igor/IGOR.md#projects)) — a project belongs to one agent and
is invisible to the rest of the roster, and a chat's project is fixed at birth.

Two client-side notes:

- `ChatViewModel.newChat(projectId)` treats a null id as a LOOSE chat, never as
  "keep the current one". The send path reads `activeProjectId` off the STORE
  rather than from its caller, so one place decides which workspace a turn is
  written into.
- The offline session cache (`MessageCache.saveSessions`) persists `project_id`
  and `project_name` alongside the title. Without them the cached list silently
  claims every chat was had outside a workspace.

---

## Presentation windows

An agent that is presenting rather than answering stages its facts as windows
instead of speaking them — a figure as a tile, a source as a cutting with its
photo, a person as a file, a sequence as a timeline. Each is a fenced block whose
info line is `kind | SCREEN TITLE`; the vocabulary and the brief that produces it
live in Igor (`core/surface.py` `_VOICE_BRIEF`).

The desktop floats these on a board beside a docked voice orb. **There is no
voice mode on this client yet**, and floating, hand-resized windows are a mouse
gesture anyway — so here the board IS the message flow: the windows render
full-width, in the order the agent staged them, each under the same panel header
every other rich block wears. Same content, same order, laid out the way a phone
reads.

| Piece | Where |
|---|---|
| Parsing — kinds, titles, the small forgiving body formats | `domain/BoardPanels.kt` |
| Rendering — stat, image, article, card, timeline, quote | `ui/prose/BoardBlocks.kt` |
| Pictures | `ui/prose/BoardImage.kt` + `IgorApi.fetchBoardImage` |

`chart`, `map`, `calendar`, `svg`, `html`, `code` and `math` were already
renderers here and are unchanged; the fence dispatch in `ui/prose/Prose.kt` now
splits the title off the info line before matching, so a titled chart is still a
chart.

**Pictures are never fetched from their origin.** `LocalBoardImageResolver` asks
Igor (`GET /media/proxy`), which fetches server-side and returns bytes. On a
board about a person, loading a photo directly would tell that person's server
the owner's IP and the moment he looked. Anything that fails renders no picture
at all — a window with its fields and no photo, rather than a broken placeholder
on a dossier.

Every parser is deliberately forgiving: these bodies are written by a model
mid-sentence, under a word budget, so a missing field makes a window plainer,
never empty and never a crash.

---

## Spoken replies

*Speak replies* in the composer's "+" overflow turns the agent's side of the
conversation into speech. It is not a playback preference: a turn sent with it on
carries `voice: true` in its client context, which swaps the backend's whole
brief — plain spoken prose, and anything that can be SHOWN staged as a window
instead of said. So switching it on changes what comes back, not just whether it
is read out.

| Piece | Where |
|---|---|
| What is speakable, and where a sentence ends | `domain/Speakable.kt` |
| One turn's speech — queue, synthesis, ordered playback | `data/VoiceSpeaker.kt` |
| The call | `IgorApi.speak` → `POST /voice/speak` |

Three things shape `VoiceSpeaker`, and every decision in it follows from one:

- **Deltas are not lines and lines are not sentences.** Whether a line sits
  inside a ``` fence cannot be judged until the line is complete, so text is held
  to the last newline before being filtered. A sentence is not spoken until it is
  terminated — half an utterance is worse than a whole one a moment later. The
  splitter carries the Turkish guards the desktop's does: `3.` is an ordinal, not
  a full stop.
- **Synthesis must not be serial with playback.** Sentence N+1 is generated while
  N is still being heard; that overlap is why a spoken reply starts promptly
  rather than after the last word has been written.
- **Order survives concurrency.** Sentence 3 finishing first must not let it
  speak first, so playback awaits each job in sequence.

The speaker lives on `viewModelScope`, not the turn's stream scope: speech
outlives the stream by design, and parenting it to the stream would have held the
turn open until the last clip finished.

This is the per-sentence HTTP path, which every engine supports. The desktop also
has a WebSocket path that keeps one prosodic context across a whole turn, so
intonation carries across a sentence boundary; here each sentence is a standalone
utterance with its own terminal contour. That seam is the cost of this path.

### The surface

Switching voice on REPLACES the transcript with `ui/voice/VoiceModeScreen` — the
point of the mode is that the owner is listening, not reading a scrollback. It
carries three things: the staged windows as a column in the order the agent
staged them, the orb, and a live caption of what is being said.

Windows are rendered through `FenceBlock`, the same dispatcher chat uses, so
every kind works there and a chart on the board is literally the chart from the
transcript rather than a second implementation that can drift from it.

The caption is a **subtitle, not a transcript**: a few lines deep, riding its own
tail, capped by `caption_lines` from Settings → Canvas. Anything worth reading
twice was supposed to become a window.

### What the machine is doing

`ui/voice/VoiceActivityCard` leads the board while a turn is working. Voice mode
otherwise shows a spinner and nothing else, and a turn that is browsing six pages
looks exactly like one that has hung. The card never collapses, shows each call's
arguments inline rather than behind a tap, and times every step live — the timer
being the part that actually distinguishes "slow website" from "crashed".

It appears on evidence of work: a tool firing, or the turn staying silent past
`activity_after_ms` from Settings → Canvas. Always-on would dock the orb for a
one-word answer, which is the case the mode should leave alone.

The step labels come from `ToolStatus.statusLabel`, the same localized
present-progressive map the transcript uses, and `stepState`/`resultSummary` are
the existing extension functions rather than new ones.

### Narration, and stepping back through answers

The narration is a `NARRATION_` card at the head of the column, not a strip along
the bottom — a caption only works if the words track the voice, and they trail
it. The bottom strip survives only when there is no board at all.

A stepper in the top-right corner walks the session's answers. The position is
held as *null = follow the newest* rather than as an index, so a live turn keeps
arriving; only a deliberate step back pins it. An older answer is never rendered
as streaming, or its activity card would spin for ever.

### The orb, and what it honestly is not

The desktop orb is a Three.js scene — an icosahedron under custom GLSL wrapped in
a particle membrane. `ui/voice/VoiceOrb.kt` is **not** that and does not pretend
to be: it is a 2D reading of the same idea (a lit core, a breathing halo, a ring
that deforms with the voice). It keeps the two behaviours that mean something —
it reacts while speaking, and it shrinks aside when there is something to present
— and gives up the ones that are only spectacle.

The orb is sized by a **width fraction**, not `Modifier.scale`. Scale is a
draw-time transform: it shrinks the pixels and leaves the layout box alone, so a
docked orb scaled to a third still occupied most of the width and was drawn small
in the middle of that box — nowhere near the corner, and sitting on top of the
board. Sizing the box is what makes `align(BottomEnd)` mean the corner.

**It needs a microphone permission to react, for playback.** Android exposes no
"what am I playing" meter; the only route to the samples is `Visualizer`, which
is gated behind `RECORD_AUDIO`. So the app now declares it — not to record
anything (dictation still goes through the system recognizer, which carries its
own permission) but to read the amplitude of its own output. It is requested the
first time voice is switched on, never at launch, and **declining costs nothing
but the reactivity**: `VoiceLevels` simply never emits, and the orb falls back to
its idle breath.

---

## Surviving a dropped connection

A turn runs **detached** on the backend (`app/core/turn_runner.py`) — the HTTP
response is only a subscriber, and dropping it never cancels the run. So a
dropped socket is a reconnect, not a failed answer. The client mints the turn's
`request_id` itself and sends it in the `POST /chat/{agent}` body (so the turn is
recoverable from the instant Send is pressed, before any `start` event has
arrived), re-attaches on `GET /chat/attach/{request_id}` when the stream drops,
and checks `GET /chat/active` before ever telling the owner the backend is
unreachable. Attach replays the whole event buffer, so a reconnect rewinds the
bubble and lets the replay rebuild it.

Reconnect count and give-up timeout are owner settings, not constants. Full
explanation: [HEARTBREAKER.md](../heartbreaker/HEARTBREAKER.md#surviving-a-dropped-connection).

---

## Health sync

Two independent WorkManager schedules:

- A **trickle sync** every four hours, network-connected and battery-not-low, reading steps, distance, sleep (with stage breakdown), heart rate, exercise sessions, weight, body fat, and oxygen saturation from Health Connect.
- A **demand poll** every fifteen minutes (WorkManager's floor) that checks whether the backend is waiting on fresher data and syncs immediately if so.

The first sync backfills 243 days; every sync after that is differential, using Health Connect's Changes API. It never writes to Health Connect — read-only.

---

## Push notifications

Firebase Cloud Messaging, data-only payloads (never notification payloads). The one handled message type triggers an immediate health sync when the backend needs current biometrics and none are fresh enough. Devices register by Firebase Installation ID. Push is opt-in at build time — if the Firebase config isn't present, the app falls back to the fifteen-minute demand poll instead.

---

## Signing and release

The release workflow decodes a keystore from a repository secret, runs the unit tests, builds a signed release APK, verifies the signature, and publishes it as a GitHub Release. Signing requires `SPEDA_GO_KEYSTORE_BASE64` plus the corresponding password and alias secrets — without them, `assembleRelease` produces an unsigned APK.

## Replying to selected text

Long-press and select native chat text, then choose **Add selection context** below the message. The composer previews each selected excerpt and lets you remove it before sending. Write your reply and send; the excerpts travel as quoted text with the reply and remain in chat history. Quotes clear after sending or switching conversations, projects, or agents. English and Turkish labels are supported.
Selection applies to native text; embedded WebViews and graphical content use their own interaction controls.
