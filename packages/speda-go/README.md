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
