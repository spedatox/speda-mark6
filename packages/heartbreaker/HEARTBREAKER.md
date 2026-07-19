# Heartbreaker — the SPEDA Mark VI client

**Heartbreaker is the primary user interface of SPEDA Mark VI.** It is the
Stark-tech desktop app the owner actually lives in — a holographic, fluid-glass
command deck for the whole agent suite. What began as an experimental reskin of
`packages/desktop` is now the product: the shipped installer is literally
`SPEDA Mark VI` (`appId: com.spedatox.speda-mark6`), and `speda.ps1` boots this
app, not the old one.

> **Lineage note.** `packages/desktop` still exists as the original neutral
> client — kept as a stable reference and fallback, never themed. All product UI
> work happens here. If you are looking for "the app," it is Heartbreaker.

---

## What it is

An Electron app (main + preload + renderer, built with electron-vite) plus a
standalone marketing teaser. The renderer is React, and it talks to **Igor** —
the Mark VI backend (`packages/igor`, see `packages/igor/IGOR.md`) — over the same
HTTP + SSE and WebSocket surface every client uses — Heartbreaker owns **zero**
business logic. Heartbreaker renders the network; Igor runs it.

The whole app speaks one visual language: **Iron Man / Stark fluid-glass
holography** — volumetric frosted-glass panels, reactive depth, JARVIS-blue
light, per-agent signature colour. It is real futurism, not toy sci-fi.

### The surface

| Area | Component(s) | What it is |
|------|-------------|------------|
| Chat deck | `ChatMain`, `MessageList`, `Message`, `InputBar` | The core conversation; streaming answers, tool-call disclosure, rich blocks (code, charts, calendars, files) |
| Welcome | `WelcomeView` (in `ChatMain`) | Clock, agent identity, greeting + a **JARVIS remark** — a memory-aware one-liner in the agent's voice |
| Roster switch | `AgentSwitcherOverlay` | Cinematic agent picker across the Superior Six + SPEDA |
| Systems Board | `SystemsBoard` | Live telemetry: model-routing matrix, MCP toolset shards, token budget, RTT trace, memory data-banks, **Forge link** status |
| Comms | `CommsTray`, `CommBubble` | The inter-agent group channel — every `dispatch_agent` and its reply, with live "working…" state for background dispatches |
| War Room | `PartyActivation`, `PartyRosterStrip`, `HousePartyWarning`, `RosterModelWindow` | The House Party Protocol takeover — SPEDA as mission commander, the whole roster staged |
| Header | `Header` | Session state, and for Optimus the **FORGE LINK / IN-PROCESS** engine jewel + workspace picker |
| Settings | `SettingsModal`, `ConfigTab`, `AgentModelPicker` | Managed configuration, per-agent model pins, connections |
| Ambient | `HudFrame`, `NeuralBackground`, `WidgetFrame`, `InteractionPrompt` | The holographic chrome and interaction prompts |

---

## Design contract (non-negotiable)

The look is codified so it stays coherent. The recipe lives in
`src/renderer/src/theme/heartbreaker.css`.

- **`.hb-holo`** is the fluid-glass panel recipe — frosted volumetric depth,
  soft inner light, agent-tinted rim. Build panels from it; do not reinvent
  glass per component.
- **JARVIS-blue base, per-agent accent.** Colours come from each backend
  profile's `DocTheme` accent, mirrored in `profile/brands.ts`.
- **Banned props** (these read as toy sci-fi and are forbidden): background
  grids, corner brackets, ruler ticks, scanlines. If a mockup has them, drop
  them.
- Theme lives entirely in Heartbreaker. **Never** theme `packages/desktop` —
  it stays clean and neutral.

---

## Running

`speda.ps1` at the repo root is the one-command launcher: it starts Igor,
waits for the API handshake, reports the Forge link, and opens this app. That is
the normal way to run the whole system.

To run the client alone (backend already up):

```bash
npm install                  # once — registers the workspace
npm run heartbreaker:dev     # Electron app (renderer on :5274)
npm run heartbreaker:web:dev # browser-only, on :5273
```

The app reads its API base + key from the Electron main process (`get-config`
IPC) with a `http://localhost:8000` / `dev-key` default, so local dev needs no
configuration.

---

## Building & shipping

`build-app.ps1` at the repo root produces a Windows installer with the server
URL and API key baked in (via electron-vite `MAIN_VITE_*`), so the installed app
talks to your Contabo server out of the box:

```powershell
powershell -File build-app.ps1 -ApiBase https://speda.yourdomain.com -ApiKey <SPEDA_API_KEY>
powershell -File build-app.ps1 -Agent ultron -ApiBase https://... -ApiKey <key>
```

`-Agent` is the key trick: **one codebase, any brand.** It sets both the visual
identity (name, model number, colour — see `profile/brands.ts`) and which
backend agent the app addresses (`/chat/{agent}`). So the same Heartbreaker
build ships as SPEDA, Ultron, Centurion, Sentinel, Atomix, NightCrawler, or
Optimus — each a fully-branded standalone app pointed at its own agent.

The **teaser** (`src/teaser`, `npm run heartbreaker:teaser:dev`) is a separate
cinematic landing page — marketing, not the app.

---

## Architecture at a glance

```
src/
├── main/        Electron main — window, IPC (get-config, window controls,
│                open-external, select-directory for the Forge workspace)
├── preload/     the contextBridge api surface exposed to the renderer
├── renderer/    the React app — components, lib (api client, hooks), store
│                (chat + settings reducers), profile (brands/theme), theme (css)
└── teaser/      standalone marketing scene (own vite config)
```

The renderer never reaches Igor directly from components — everything
goes through `src/renderer/src/lib/api.ts` (SSE chat, re-attach, active-run
polling, cancel, models, memory, connections, welcome, agents). State is two
reducers in `src/renderer/src/store` (chat, settings). Igor's survivability
(detached turns that outlive navigation/reload) is honoured here: the client
re-attaches to a live run on entering a session and cancels via the backend, not
by dropping the socket.

---

## Status

**Primary, production client.** Fully redesigned into the Stark command deck —
chat, roster, systems board, comms/war-room, settings, Forge integration, and
the JARVIS welcome all shipped. Ongoing work is feature depth and polish, not a
prototype anymore.
