# Heartbreaker

The full-roster desktop client. Electron, React, TypeScript. Every agent persona, the whole systems/protocols UI, House Party — the complete surface of the backend, in one app.

---

## Contents

- [Directory structure](#directory-structure)
- [Dev workflow](#dev-workflow)
- [Profile and branding system](#profile-and-branding-system)
- [Key components](#key-components)
- [Steering a running response](#steering-a-running-response)
- [Configuration](#configuration)

---

## Directory structure

Standard `electron-vite` split:

| Path | Contents |
|---|---|
| `src/main/index.ts` | Electron main process — window creation, a custom `app://bundle` protocol (needed so MapLibre GL's workers don't hit `file://` origin restrictions), config IPC persisted to `connection.json`, the Forge workspace directory picker |
| `src/preload/index.ts` | Exposes `window.api`: `getConfig`, `setConfig`, `selectDirectory`, `openExternal` |
| `src/renderer/src/components/` | 40+ UI components — see below |
| `src/renderer/src/profile/` | The branding system — see below |
| `src/renderer/src/store/` | Chat reducer/context, local transcript cache, settings context |
| `src/renderer/src/theme/` | `heartbreaker.css` — the actual stylesheet in use |
| `src/renderer/src/lib/` | All backend calls (`api.ts`), the agent roster/colors, connection resolution, mic/TTS, i18n (en/tr) |
| `src/teaser/` | A separate marketing/landing build with its own Vite config — not part of the app |

---

## Dev workflow

```bash
# from packages/heartbreaker
npm run dev          # electron-vite dev
npm run build         # electron-vite build
npm run typecheck     # tsc across both the node and web tsconfig projects
npm run web:dev        # browser-only build, no Electron main/preload
npm run dist            # electron-vite build + electron-builder --win
```

From the repo root, the same commands are exposed workspace-scoped: `npm run heartbreaker:dev`, `:build`, `:typecheck`, `:web:dev`, `:web:build`. `heartbreaker:dist` is not exposed at the root — run it from inside the package, or use `build-app.ps1` at the repo root, which installs dependencies and builds a Windows installer with a server URL, API key, and default agent brand baked in via `-Agent`.

---

## Profile and branding system

`src/renderer/src/profile/`:

- **`brands.ts`** — `BRANDS`, a map of eight personas (`speda`, `ultron`, `centurion`, `sentinel`, `atomix`, `nightcrawler`, `optimus`, `orion`), each with a name, tagline, avatar initial, and accent color.
- **`theme.ts`** — derives the entire CSS custom-property palette from one accent color, preserving each token's saturation and lightness. Also owns House Party's palette-cycling animation across the roster.
- **`warroom.ts`** — a ninth profile, deliberately excluded from `BRANDS` so it never appears in the agent switcher. Swapped in only when House Party engages.
- **`index.ts`** — picks the active brand from `BRANDS` via a build-time environment variable, defaulting to Speda.

---

## Key components

| Group | Components |
|---|---|
| Chat core | `ChatMain`, `MessageList`, `Message`, `InputBar`, `VoiceMode` |
| Rich content | `MapBlock`, `ChartBlock`, `CalendarBlock` — inline widgets rendered from fenced code blocks in a message |
| Roster & coordination | `AgentSwitcherOverlay`, `CommsTray`, `HousePartyModal`, `HousePartyWarning`, `PartyActivation`, `PartyRosterStrip`, `PartyStream` |
| Safety protocols | `LockdownModal`, `LockdownActivation`, `SkyfallCountdown`, `SkyfallProjects` |
| Status & telemetry | `HudFrame`, `SystemsBoard`, `TelemetryColumn` |
| Settings | `SettingsModal` (tabbed shell), `AutomationBuilder`, `McpServersPanel`, `PortalsPanel`, `PendingAsksTray`, `RosterModelWindow` |
| Delegation | `SubagentPanel`, `SubagentDetailView` |
| Ecosystem | `HisarBrowser` — the Hisar vault directory picker used for the Forge workspace |

---

## Steering a running response

While a response is streaming, typing into the composer swaps the Stop button for Send. Submitting sends the text to `POST /chat/steer/{requestId}` instead of starting a competing turn — it lands as a follow-up inside the same run rather than a new message. If the run isn't steerable (already finished, or running out-of-process), the composer keeps the typed text instead of discarding it.

---

## Configuration

Server URL and API key resolve in order: environment variables → values baked in at build time by `build-app.ps1` → `connection.json` in the app's user-data directory, written by the in-app connection setup dialog → a hardcoded local default. Every request carries the resolved key as `X-API-Key`, matching the backend's `SPEDA_API_KEY`.

The browser-only build (`web:dev`/`web:build`) instead reads `VITE_API_BASE`/`VITE_API_KEY` plus `localStorage`.
