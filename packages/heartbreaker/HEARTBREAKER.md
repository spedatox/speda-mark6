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
| Voice canvas | `VoiceCanvas` (the board), `VoicePanelBody` (what a window looks like), `VoiceOrb`, `lib/voicePanels` (splitting + layout) |
| Rich content | `MapBlock`, `ChartBlock`, `CalendarBlock` — inline widgets rendered from fenced code blocks in a message |
| Roster & coordination | `AgentSwitcherOverlay`, `CommsTray`, `HousePartyModal`, `HousePartyWarning`, `PartyActivation`, `PartyRosterStrip`, `PartyStream` |
| Safety protocols | `LockdownModal`, `LockdownActivation`, `SkyfallCountdown`, `SkyfallProjects` |
| Screen lock | `LockScreen`, `LockScreensaver`, `ScreenLockSettings` (Interface tab), `lib/useScreenLock`, `lib/lock` |
| Status & telemetry | `HudFrame`, `SystemsBoard`, `TelemetryColumn` |
| Settings | `SettingsModal` (tabbed shell), `AutomationBuilder`, `McpServersPanel`, `PortalsPanel`, `PendingAsksTray`, `RosterModelWindow` |
| Delegation | `SubagentPanel`, `SubagentDetailView` |
| Ecosystem | `HisarBrowser` — the Hisar vault directory picker used for the Forge workspace |

---

## Voice mode is a presentation, not a talking chat window

Voice mode does not read a reply aloud. The agent **presents**: it narrates while
the screen carries the evidence it is narrating about — a figure as a stat tile,
a source as a cutting with its photo and its excerpt, a person as a file, a
sequence as a timeline — each in its own window on the board.

**The agent stages the board itself.** It authors each window, titles it, and
places it in its reply at the point its narration reaches it. Because the reply
already streams token by token, a window written between two spoken sentences
appears between those two sentences being heard — *writing order is the cue
track*, so the board assembles in step with the voice with no audio timestamps
to sync and nothing to drift. The brief that asks for this lives in Igor
(`core/surface.py` `_VOICE_BRIEF`), on the per-turn context line rather than in
the system prompt, because voice mode is toggled mid-conversation and a system
prefix that changes mid-session invalidates the prompt cache. Each agent adds
its own note on top (`Profile.canvas_brief`) — Sentinel turns every figure into
a tile or a chart, NightCrawler gives every source its own window.

This is the inverse of what it used to be. The canvas was once a *parser* of
chat output: the model wrote its usual markdown answer and `voicePanels` scraped
out whatever fenced blocks happened to be in it. If the model did not reach for
a chart unprompted, there was no chart — nothing had ever asked it to present.

**A window** is a fenced block whose info line is `kind | SCREEN TITLE`. Beyond
the renderer kinds already shared with chat (`chart`, `map`, `calendar`, `svg`,
`html`, `code`, `table`, `math`) there is a presentation vocabulary that exists
so a fact can be *shown* rather than said: `stat`, `image`, `article`, `card`,
`timeline`, `quote`. Their bodies are small forgiving formats parsed in
`VoicePanelBody` — written by a model mid-sentence under a word budget, so a
missing field degrades to a plainer window, never an empty one.

**Pictures never load from their origin.** A photo on a card or an article's
lead image is fetched by Igor (`GET /media/proxy`) with the client's normal
X-API-Key and handed to the tag as a `blob:` URL. Two reasons, both structural:
the renderer ships `img-src 'self' data: blob:`, so a remote `<img src>` is
refused before a request leaves — and on a research board, loading a picture
straight from its host would tell that host the owner is looking, which is the
one thing an OSINT board must not do. Anything that fails renders no picture at
all; a broken-image icon on a dossier is worse than a dossier without one.

**The transcript is a subtitle.** Narration runs along the bottom as a live
caption a few lines deep, tracking what is being said now. Prose is never a
window: anything worth reading twice was supposed to become one.

**Nothing staged, nothing shown.** A yes, a no, a thank-you, the time — the orb
keeps the screen and the words run underneath. The board opens on the first
staged window.

**The owner owns the board.** Windows glide as the layout packs them, and can be
dragged by the grip or resized from the bottom-right corner; either gesture pins
a window until `REFLOW` hands the board back. `EXTEND_` blows one up to fill the
board.

Everything tunable lives in **Settings → Canvas** (backend `canvas_*` settings):
the spoken word budgets that shape what the agent *writes*, and the window
ceiling, entrance stagger and caption depth that shape what the board *draws*.
Both halves come from one place so they cannot disagree — the client reads them
off `/voice/status` rather than holding constants of its own.

`canvasharness/` is a throwaway dev server (`canvas-harness` in
`.claude/launch.json`) that renders a staged presentation against the real
splitter, so the board can be worked on without a backend or a spoken turn.

---

## Screen lock

`Ctrl+L` covers the deck with `LockScreen` at any moment — the active agent's card over the passcode line; it also comes up on launch when *Interface → Screen lock → Ask when the app opens* is on, and after `lockIdleMinutes` of no input. The passcode is stored only as a SHA-256 (`lib/lock.ts`) in the same `localStorage` blob as the rest of the settings — a privacy lock against whoever walks past the desk, not a vault against someone holding the machine. With no passcode set the screen still raises, and any key lifts it.

After `lockScreensaverSeconds` idle on the lock screen, `LockScreensaver` takes over and parades the roster: per agent, the same `AgentCard` the keypad shows at rest, played with its reveal — the real wordmark (`AgentMark`) arrives, the name types itself out, the model number and tagline land, it holds for `lockSaverDwellMs`, and the card dissolves into the next — each beat lit in that agent's own brand accent. The card stays centred and still — an earlier pass drifted it against panel burn-in, but a wandering wordmark reads as sloppiness rather than care, and each beat repaints the whole area anyway. Any input brings the keypad straight back.

One trap worth knowing: the mark's entrance fills `backwards`, never `both`. An animation left holding a transform + filter keeps the SVG on a composited layer rasterised at the entrance scale, and the mark stays visibly soft for the rest of the beat.

All five values are settings, and the state machine (what raises the lock, what lowers it) lives in `lib/useScreenLock.ts` so both desktop clients mount it identically.

---

## Steering a running response

While a response is streaming, typing into the composer swaps the Stop button for Send. Submitting sends the text to `POST /chat/steer/{requestId}` instead of starting a competing turn — it lands as a follow-up inside the same run rather than a new message. If the run isn't steerable (already finished, or running out-of-process), the composer keeps the typed text instead of discarding it.

---

## Configuration

Server URL and API key resolve in order: environment variables → values baked in at build time by `build-app.ps1` → `connection.json` in the app's user-data directory, written by the in-app connection setup dialog → a hardcoded local default. Every request carries the resolved key as `X-API-Key`, matching the backend's `SPEDA_API_KEY`.

The browser-only build (`web:dev`/`web:build`) instead reads `VITE_API_BASE`/`VITE_API_KEY` plus `localStorage`.
