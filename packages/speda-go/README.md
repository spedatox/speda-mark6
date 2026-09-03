# Speda GO

The native Android client. Kotlin, Jetpack Compose. Package id `com.speda.heartbreaker` — intentional, matching the desktop client's Electron app identity rather than the "Speda GO" branding; it doesn't get renamed.

---

## Contents

- [Directory structure](#directory-structure)
- [Build setup](#build-setup)
- [Networking](#networking)
- [Key screens](#key-screens)
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
- **Skyfall** — the full-screen arm/fire/abort countdown.

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

**Still missing: the orb and a dedicated voice surface.** Replies are spoken in
the ordinary chat screen, with the staged windows rendering inline as they always
do. Two constraints shape whatever comes next: the desktop orb is a Three.js
WebGL scene with custom shaders rather than a 2D drawing, and this app declares
no `RECORD_AUDIO` on purpose — Android's `Visualizer` needs it, so there is no
output amplitude to drive a reactive orb without adding that permission.

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
