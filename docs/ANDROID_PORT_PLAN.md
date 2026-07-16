# ANDROID PORT PLAN — Heartbreaker Droid

**Goal:** a native Android (Kotlin + Jetpack Compose) port of Heartbreaker with a **1:1 visual and UX experience** against the desktop/web client, plus Android-exclusive features layered on top. The Stark fluid-glass hologram language is non-negotiable: same tokens, same material, same motion, same copy.

**Source of truth for parity:** `packages/heartbreaker/src/renderer/` — every value in this plan traces back to a file there. Where this plan and the code disagree, the code wins.

---

## 0. Governing principles

1. **The sub-768px web layout IS the mobile spec.** Heartbreaker already ships a complete mobile adaptation (`useIsMobile.ts` + the `@media (max-width: 767px)` block in `heartbreaker.css`): off-canvas frosted sidebar drawer, HUD strip with DIAG dropdown, composer "+" overflow menu, sticky composer above the keyboard, 44px touch targets, single-column Systems Board, full-width Comms Tray. The Android app replicates *that* layout — not a reinterpretation of the desktop one. Every mobile-mode decision has already been made and reviewed on the web build; we inherit it.
2. **Port the logic, don't reinvent it.** The chat reducer, segment builder, theme math, partial-JSON detector, markdown pre-processors, and watchdog copy are small pure functions. They get transliterated to Kotlin verbatim and locked with fixture tests generated from the TypeScript originals.
3. **One glass material** (`heartbreaker.css` "THE ONE GLASS MATERIAL"). Exactly one `Modifier.hbGlass()` implementation; every surface uses it with thin state modifiers (tint / active / amber / ghost / round), never a per-component recipe. The CSS already documents the blur-less fallback (occluding dark fill + milky tint, used wherever nested backdrop roots cancel blur) — that same fallback is our sub-API-31 / performance path, so degradation is *by design*, not a compromise we invent.
4. **Zero identity strings in shared code** (mirror of backend Rule 10): brands, accents, taglines live in one `Brands.kt` mirroring `profile/brands.ts`.
5. **Android exclusives are additive.** They live behind the same design language and never fork the shared surfaces. Anything that changes a shared screen must also make sense on desktop.

---

## 1. Current-state inventory (the parity contract)

### 1.1 Design system (`theme/heartbreaker.css`, `profile/theme.ts`)

- **Fonts:** Rajdhani (HUD chrome, all-caps letter-spaced labels), Inter (`--font-read`, body + "mono" readouts — display monospace was deliberately killed), JetBrains Mono/Fira Code (code blocks only). `SamsungOne`/`SamsungSharpSans` are declared but the TTFs are missing from the repo (flagged as a separate task) — today they silently fall back, so **Rajdhani + Inter + JetBrains Mono is the actual shipping stack** and what the port bundles (all OFL-licensed, bundleable in `res/font`).
- **Palette:** near-black petrol void (`--hb-void #04080a` → `--hb-steel #13303b`), one accent family (`--hb-cyan/-bright/-dim`), semantic amber/red/green, cool text scale, dim icon scale. **Everything except semantic colors re-hues per agent.**
- **Theme engine** (`theme.ts`): one accent hex in → the entire palette out. `deriveAccents` (bright = mix 28% white, dim = mix 62% void), `rehue()` (keep S/L, swap hue) applied over `BASE_HEX` + `BASE_RGBA` token tables, exact accent kept for the accent family. `morphTheme(from, to, 500ms, easeInOutQuad)` rebuilds the palette every frame during agent switch. `startPartyCycle` (war room): palette parades through 8 roster colors, 3s per stop, 700ms lead-in, smoothstep mixing, ~12Hz update throttle.
- **Glass material:** radius 14dp (pill/12/9 variants), fill = `rgba(8,16,24,0.62)` + milky tint `rgba(190,215,235,0.06)` layered on top, backdrop `blur(28px) saturate(140%)`, 1px rim `--hb-edge`, and a 5-part shadow stack: top specular (white .28), left light edge (.10), bottom rim (.06), inner glow (2px, .05), drop `0 8px 32px black .35`. Active state brightens all of it. Menus/dropdowns use near-opaque `--glass-menu rgba(10,20,27,0.94)` because nested blur is cancelled.
- **Etched seams** (`hb-seam-r/b/t`): structural boundaries drawn as TWO 1px lines — black .4 groove + white .08 light catch — dissolving toward the ends via gradients.
- **Sharp world:** global `border-radius: 0` on chrome; only glass layers opt into radii. Body background = static 160° gradient (#03070a → #060d14 38% → #08131d 62% → #040a10).
- **Ambient background** (`NeuralBackground.tsx`, actually `AmbientBackground`): 3 blurred radial-gradient blobs (55vw/42vw/28vw, blur 80px, alphas .24/.16/.12) orbiting on 22s/16s/12s keyframed paths + 2 rotating volumetric light sweeps (blur 40px, 28s/20s). All in `--hb-accent-rgb`, so it re-hues with the agent.
- **Animations to reproduce exactly:** `hbRise` (10px rise + fade), `hbFadeIn`, `hbBlink`, `hbPartyCycle` (21s, 8 colors), `caretBreathe` 1.15s + `caretSheen` 2.4s (streaming cursor), `textShimmer` 1.6s (thinking label), `widgetEntrance`, `modalIn`, `dropDown`, `skeletonPulse`, `fadeSlideIn`, plus the House Party (`hbHpp*`) and Agent Switcher (`sw*`, `podRise`, `ringSpin`) cinematic keyframe sets with their exact delays.
- **Prose (markdown) style:** H1–H4 as frosted accent header plates with left accent border and `MAIN_SUB` underscore split (white main, cyan `_SUB`); `strong` → white; `em` → amber; `▸` cyan list markers (dim `·` nested); inline code as cyan text on accent-tinted chip; blockquote panel inset with gradient top hairline; etched-seam `hr`; data-grid tables (Rajdhani caps headers, mono cells, zebra + hover); `Sources:` paragraphs restyled as boxed source chips; KaTeX math with currency `$` protection.

### 1.2 App structure & behaviors

| Area | File(s) | Must-keep behaviors |
|---|---|---|
| App shell | `App.tsx` | Profile switching w/ 500ms palette morph + text dissolve at midpoint (200ms); war-room 3-state (`off`/`standby`/`engaged`); backend house-party flag poll (4s) + instant `hpp-engaged` event; transcript mirrored to local cache when a turn settles; config load |
| Chat state | `store/chat.ts` | Reducer with 17 actions; SELECT_SESSION preserves in-flight streaming bubble (reattach/history race); FINISH/ERROR ignore missing messages; error kept as separate banner, never erases streamed text |
| Streaming | `ChatMain.tsx` | Chunk coalescing (≤1 flush/frame); `afterChars` stamped on tools; watchdog: 15s stall → status line naming the model, 300s dead → abort with phase-specific diagnostic (no-start / tool-stuck / no-tokens); stream-closed-without-terminal → finalize; reattach to detached runs on session entry (`/chat/active` → `/chat/attach/{id}`); abort local fetch on session/agent switch (run keeps going server-side); Stop = `POST /chat/cancel/{id}` + local abort; title polling (12 × 1.5s); regenerate (`keep_messages` + `regenerate`) and edit-and-resend (truncate + resend) |
| Welcome view | `ChatMain.tsx` | Live clock (1s), caps date line, agent hero (name + mark, accent glow), tagline, typewriter greeting (42ms/char, blinking block caret), async JARVIS remark from `/welcome/{agent}` typed at 26ms/char; war-room copy variant ("ALL HANDS ON DECK") |
| Message render | `Message.tsx` | Typewriter reveal: rAF, `speed = max(45, remaining × 7)` chars/s exponential catch-up; skipped entirely when a code fence is present; markdown re-parse debounced 80ms; segments interleave tools at the char offset they fired (groups at equal offsets); streaming cursor rides last text segment; WorkingStatus (spinner + shimmer label from TOOL_STATUS map / live phase); tool feed (verb+target rows, expandable: red/green diff for edits, `$ command` + output, generic key:value + result); file cards (glass, amber DOWNLOAD chip); upload chips; image thumbnails + lightbox; hover action bar (copy, thumbs, read-aloud via TTS, regenerate, delete); user bubble = `hb-holo` glass, `overflowWrap anywhere`; edit-in-place textarea |
| Special fences | `ChartBlock/CalendarBlock/CodeBlock/WidgetFrame/HousePartyWarning` | ` ```chart ` → Stark Recharts (line/area/bar/pie, exact axis/tooltip/legend styling); ` ```calendar ` → holographic week (HUD ring SVG, glowing today numeral, event chips, stacked glass ghosts); ` ```html/svg ` → sandboxed live widget (injected base styles + resize script; SVG stroke draw-in cascade; only commit *complete* markup — no partial-frame flicker; hover Code/Copy buttons); other langs → glass code block (vscDarkPlus, header w/ lang + copy); `hpp-warning` (+ alias/content detection) → amber banner that auto-opens the authorization modal once (2.5s debounce); streaming partials for chart/calendar → `looksIncomplete()` balance check → quiet "MATERIALIZING" placeholder vs real parse error |
| Composer | `InputBar.tsx` | Auto-grow textarea (max 200px); attach (images downscaled ≤1568px long edge, JPEG q0.9 / PNG; other files as base64 doc blocks + display chips); paste images; drag-drop w/ amber overlay (desktop-only affordance); budget mode toggle (green BUDGET / amber FULL, optimistic, re-synced after each turn — SPEDA can flip it itself); model picker (provider-grouped glass dropdown, incl. "DEAD ZONE PROTOCOL" for ollama); voice input (speech recognition, en-US, appends transcript); send/stop button states; mobile: "+" overflow menu holds attach/budget/voice, model picker keeps its slot; status strip ("<AGENT> can make mistakes · …") |
| Sidebar | `Sidebar.tsx` | Brand header = agent dropdown switcher; search; NEW CONVERSATION; time-grouped sessions (Today/Yesterday/This week/This month/Older) with `>>:` group labels; amber selected row; hover rename/delete; blinking live-run jewel from `/chat/active` poll (8s); footer avatar + settings popup; mobile: fixed drawer `min(84vw, 330px)`, slide `0.28s cubic-bezier(0.32,0.72,0.33,1)`, frosted backdrop sheet, starts closed |
| Header | `Header.tsx` | 40px frosted plate w/ bottom seam; MONITOR No. 1 label; session-title query box (`:TITLE` pill); FORGE LINK indicator + workspace chip (Optimus only); MSGS counter; PROCESSING (blinking amber) / QUERY COMPLETE / STANDBY; WAR ROOM, COMMS, SYS buttons; `hb-hide-sm` items collapse on mobile |
| HUD strip | `HudFrame.tsx` | Fixed 22px top strip: HOST · ONLINE/OFFLINE (red blink) · centered HEARTBREAKER wordmark · MODEL · TOOLS · RTT (green <400ms) · SESS · amber date chip · clock. Mobile: link state + wordmark + MODEL + DIAG dropdown (host/tools/RTT/sess/date/time). Health poll 8s |
| Sessions/settings | `SettingsModal.tsx` (7 tabs), `ConfigTab.tsx` | General (system prompt debounced 400ms, temperature slider); Configuration (backend config groups: typed fields, masked secrets, applied-live vs restart-required); Connections (Google & Notion OAuth via external browser + 3s status polling, MCP server toggles w/ token cost, ITPM budget bar w/ over-limit warning); Automations (n8n + Telegram pipeline status, watcher list w/ kind chips + pause/delete, Telegram connect flow); Interface (theme dark; light "soon"); Data (import Claude export .zip w/ background processing + session refresh polls, index-history one-shot); Account (name → greeting) |
| Systems board | `SystemsBoard.tsx` | Full-screen tactical overlay ("SYSTEMS 56A."): uplink KV telemetry; network nodes list; CORE ROUTING_MATRIX (periodic-table model tiles — click routes active model), CONTEXT SHARDS (MCP toggle tiles), AGENT CORES (per-agent model pins); TOKEN_BUDGET gauge (big %, 22-segment bar, per-server token bars); RESPONSE_TRACE RTT sparkline (4s probe); DATA_BANKS knowledge bank: memory file rail + fact readout, EXTEND_ to ~80% two-column dossier, owner EDIT/COMMIT with 409 conflict reload, HISTORY + RESTORE revisions. Esc retracts extended state first. Mobile: one scrollable column |
| Comms | `CommsTray.tsx`, `CommBubble.tsx` | Bottom-right floating glass slab (420px / EXTEND_ 780px / mobile full-width); 3s poll of `/agents/comms`; oldest-first scrollback pinned to end unless user scrolled up; bubbles tinted by from-agent color w/ monogram avatar, meta line (FROM ▸ TO · time · HP/BROADCAST), markdown bodies, threaded reply under task, live "WORKING… Ns" elapsed, failure states in red, MORE_/LESS_ clipping, copy; HPP STAND DOWN control (engage is voice-only) |
| War room | `PartyRosterStrip.tsx`, `PartyActivation.tsx`, `RosterModelWindow.tsx`, `HousePartyModal.tsx` | Roster strip under header (2.5s comms poll → per-agent WORKING/STANDBY jewels + done counts, CORES button, EXIT/STAND DOWN); activation cinematic: engage/standby enter (2600ms ignite / 3650ms done — directive line, title slam through blur, roster boot pings, shockwave, Heavy·Expensive·Prototype pill) and stand-down reverse wink-out (850/1750ms); profile/theme swap happens mid-cover (`onIgnite`); ROSTER CORES draggable window (per-agent DESKTOP + TELEGRAM model pins); HPP authorization modal (masked passphrase, server-side validation, scanning light bar, shake on error) |
| Agent switcher | `AgentSwitcherOverlay.tsx` | Alt+A armoury: room re-hues to focused agent, dual counter-rotating HUD rings + reactor glow on selection, staggered pod boot, designation panel with light sweep, floating motes, lock-in flare then theme morph handoff; keyboard nav (arrows/enter/esc/1-9) |
| Offline | `store/messageCache.ts` | Per `(agent, session)` transcript snapshot saved when a turn settles; hydrated instantly on session open; server refresh wins unless it returns empty while cache has content |
| Health/peers | `useHealth.ts`, `useOnlineAgents.ts` | `/health` poll (8s default; 4s on the board) → online/RTT/tool count; `/agents` poll (10s) → Forge peer presence |

**Not ported:** `src/teaser/` (marketing animation), Electron window-control IPC (OS handles it), `InteractionPrompt.tsx` (orphaned — not imported anywhere and references types that don't exist; revisit only if/when Optimus mid-turn interactions land in the desktop client for real).

**Desktop native folder picker** (`ForgeLink` workspace): on desktop this picks a *local* directory because the Forge runs on the same PC. From a phone the Forge workspace is a path on a remote machine — Android renders it as a plain text field (with recent-values dropdown), not a SAF picker.

### 1.3 Backend API surface consumed (all with `X-API-Key`)

Chat: `POST /chat/{agent_id}` (SSE: `start|chunk|tool|tool_result|file|done|error`, each with `session_id` + `request_id`), `GET /chat/attach/{request_id}`, `GET /chat/active[?session_id]`, `POST /chat/cancel/{request_id}`, `GET /welcome/{agent_id}`.
Sessions: `GET /sessions?agent_id&limit`, `GET /sessions/{id}/messages`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}`.
Models: `GET /models`, `GET|POST /agents/models`, `POST /agents/telegram-models`.
Memory: `GET|PUT /memory/files` (409 optimistic concurrency), `GET /memory/files/revisions?path`, `POST /memory/files/restore`, `GET|PUT /memory/sources`.
Connections: `GET|POST /connections`, Google/Notion `login|status|disconnect`.
Automations: `GET /automations`, `POST /automations/{id}/toggle`, `DELETE /automations/{id}`, `GET /automations/status`, Telegram `connect|status`.
Agents: `GET /agents`, `GET /agents/comms?limit&after_id`, `GET|POST /agents/house-party` (optional `passphrase`).
Misc: `GET|POST /budget-mode`, `GET /config`, `PUT /config`, `POST /admin/import-chats` (multipart), `POST /admin/index-history`, `GET /health`, file downloads under returned URLs.

No backend changes are required for parity. §6 lists the additive endpoints for Android exclusives (push).

---

## 2. Project setup

| Decision | Choice | Rationale |
|---|---|---|
| Location | `packages/heartbreaker-android/` in this monorepo | Sits next to the parity source; inert for the GitOps prod deploy (server never builds it). Shares the token fixtures (§7) |
| App ID | `com.speda.heartbreaker` | Matches the Electron `appUserModelId` |
| Language / UI | Kotlin 2.1.x, Jetpack Compose (BOM current), single Activity | The app is one chat surface + overlays; matches desktop UX exactly. No fragment/nav-graph ceremony — overlays are composables, like the web |
| minSdk / target | **minSdk 31, targetSdk 35** | API 31+ gives RenderEffect backdrop blur (the glass). Personal single-user app → we control the device. The occluding-fill fallback exists anyway if it must drop to 29 |
| DI | Manual (an `AppGraph` object) | Solo project, ~1 activity; Hilt is ceremony we don't need |
| Async | Coroutines + Flow, `kotlinx-collections-immutable` for message lists | |
| Network | OkHttp 5 (streaming SSE read, `readTimeout=0`), kotlinx.serialization | The desktop parses `data:` lines by hand; we do the same for byte-level parity (§4.1) |
| Persistence | DataStore (settings), plain JSON files in `filesDir/cache` for the per-session transcript mirror (same shape as localStorage), Keystore-encrypted prefs for `apiBase`/`apiKey` | Mirrors desktop's localStorage semantics; Room only if/when transcript search ships (§6) |
| Images | Coil | attachments, lightbox |
| Blur/glass | **Haze** (`dev.chrisbanes.haze`) for backdrop blur + custom `hbGlass` drawing for rim/shadows | §3.2 |
| Markdown | commonmark-java (+ gfm-tables/strikethrough/autolink extensions) → **custom Compose renderer** | Full control is mandatory: Stark heading plates, `_SUB` splits, sources chips, fence interception, per-segment memoization. Off-the-shelf renderers can't do this without fighting them |
| Syntax highlight | `dev.snipme:highlights` w/ custom vscDarkPlus-approximation theme | Pure Kotlin, works in Compose text spans |
| Charts | Custom Compose Canvas (`ChartSpec` is 4 chart types) | Vico/MPAndroidChart can't hit the exact Recharts styling; the spec is small enough to own |
| Math | Port `prepareMath()`; render display/inline math via a hidden KaTeX WebView rasterizer with per-expression bitmap cache (exact same renderer as desktop). Fallback if it fights us: `jlatexmath-android` for display math only, inline math as styled text | KaTeX-in-WebView is the only pixel-identical path |
| HTML/SVG widgets | WebView with the same injected `BASE_STYLES` + `RESIZE_SCRIPT` (bridge via `WebMessageListener`), JS enabled, file access off; SVG also WebView with the `animateSvgDrawIn` cascade injected as JS | Keeps widget behavior AND the draw-in cinematic identical |
| Build | Gradle version catalog, AGP 8.x, JDK 17 (Android Studio) | Note: this Windows box has no system Node/Python (see memory) — Android Studio brings its own JDK; no Node needed for the app itself |
| Distribution | Local `assembleRelease`, personal keystore, ADB/direct install | Single user; no Play Store |

Module layout (kept deliberately light):

```
packages/heartbreaker-android/
├── app/                        # activity, DI graph, navigation-free shell
│   └── src/main/kotlin/com/speda/heartbreaker/
│       ├── ui/                 # screens & overlays (mirror of components/)
│       ├── domain/             # ChatState/reducer, segmenter, watchdog, pure logic
│       ├── data/               # IgorApi (OkHttp+SSE), stores (settings, msg cache)
│       └── exclusive/          # Android-only features (§6)
├── designsystem/               # tokens, theme engine, hbGlass, seams, typography,
│   │                           # ambient background, icons (ImageVectors), motion specs
│   └── src/test/               # fixture tests vs TS-generated JSON (§7)
└── gradle/libs.versions.toml
```

---

## 3. Design-system port (the heart of 1:1)

### 3.1 Tokens & theme engine

`designsystem` exposes an `HbPalette` (every `--hb-*` / `--glass-*` / `--bg-*` token as a `Color`) provided via `CompositionLocal`. The generator is a literal port of `theme.ts`:

```kotlin
object ThemeEngine {
    fun deriveAccents(accent: Color): Accents   // bright = mix(accent, white, .28), dim = mix(accent, void, .62)
    fun buildPalette(accent: Color): HbPalette  // rehue BASE_HEX/BASE_RGBA at accent hue, exact accent family,
                                                // rgba tokens carry their original alphas
}
```

- `BASE_HEX` / `BASE_RGBA` tables copied value-for-value from `theme.ts` (they are *the* palette).
- **Agent morph:** an `Animatable<Color>` for the current accent; agent switch runs `animateTo(next, tween(500, easing = easeInOutQuad))` and every frame recomputes `buildPalette(accent.value)` into the CompositionLocal — same "whole world shifts hue together" effect. Brand text dissolve: `data-brand-text` equivalent = an `hbBrandText()` modifier that fades out over 180ms at morph start and swaps content at the 200ms midpoint (copy the App.tsx timeline).
- **Party cycle:** a coroutine loop (`while (engaged)`) stepping `PARTY_COLORS` at 3000ms/stop after a 700ms lead-in, smoothstep-mixed, throttled to ~12Hz — literal port of `startPartyCycle`. Owns the accent while running; profile changes must not snap it (same guard as `App.tsx`).
- Fonts in `res/font`: `rajdhani_{300,400,500,600,700}.ttf`, `inter_{400,500,600,700}.ttf`, `jetbrains_mono_regular.ttf`. Text styles mirror the CSS: `HbType.label` (0.62rem≈10sp, w600, tracking 0.18em, caps), `HbType.read` (15sp Inter, lh 1.7), `HbType.headerBar`, `HbType.numThin` (Rajdhani 300, tabular), etc. `1rem = 16sp/16dp`; keep the CSS rem values, don't re-derive.

### 3.2 The glass material

```kotlin
fun Modifier.hbGlass(
    shape: HbGlassShape = HbGlassShape.R14,   // R14 / R12 / R9 / Pill / TopOnly
    state: HbGlassState = Default,            // Default / Active / Amber / Tint(color) / Ghost
    backdrop: HazeState? = LocalHazeState.current,  // null → occluding-fill fallback (nested case)
): Modifier
```

- **Backdrop:** Haze `hazeEffect` with blur 28dp + saturation 1.4 color-matrix where the platform allows; the app's root content is the single `hazeSource`. **Rule (mirrors the CSS nested-backdrop-root rule): only surfaces directly over the void blur; anything nested inside another glass surface uses the occluding fill (`--glass-fill`/`--glass-menu`) with no blur.** This is both the fidelity rule and the perf budget — the desktop looks the way it does *because* nested glass doesn't blur.
- **Fill:** milky tint layered over the dark base (two `drawRect`s), exactly the CSS layering.
- **Rim + shadow stack:** Compose has no inset box-shadow → `drawWithContent`: 1dp border in `edge` color; inset top specular / left edge / bottom rim as 1dp gradient lines inside the shape; inner glow as a soft inner stroke; drop shadow via `Modifier.shadow`-equivalent `drawBehind` blur (or `graphicsLayer` shadow) tuned to `0 8px 32px black 35%`.
- **Seams:** `Modifier.hbSeamBottom()/Right()/Top()` drawing the dual groove+catch gradient lines with the 12%/18% dissolve stops.
- **Ambient background:** a `Box` behind everything — static 4-stop 160° gradient + 3 radial-gradient blobs animated along the exact `ambOrbit1..3` keyframe paths (`Animatable` keyframes, 22/16/12s) + 2 rotating sweep gradients (28/20s). Radial-gradient-to-transparent already reads soft at phone size; add `Modifier.blur` only if side-by-side comparison demands it.
- **Debug screen:** a token gallery (all colors, glass states, seams, type ramp, animations) — the reference surface for the visual-diff ritual (§7).

### 3.3 Iconography

Every icon in the app is an inline 24-viewBox stroked SVG. Port them 1:1 as `ImageVector` builders in `designsystem/icons` (path data copies over directly). No Material icons anywhere — silhouettes must match.

---

## 4. Runtime architecture

### 4.1 Transport (`data/IgorApi.kt`)

- OkHttp singleton: `readTimeout = 0`, `callTimeout = 0` (streams idle during long tool runs; the watchdog owns liveness, same as desktop). `X-API-Key` interceptor.
- SSE: POST, then read the body source line-by-line, `data: `-prefixed lines → `Json.decodeFromString<SseEvent>`; malformed lines skipped. Emitted as `Flow<SseEvent>` (`callbackFlow`/`flow` on IO). `SseEvent.data` stays `JsonElement`, mapped per `type` exactly like the TS switch.
- Same request body shape (`message`, `session_id`, `model`, `system_prompt`, `attachments`, `documents`, `keep_messages`, `regenerate`, `cwd` for Optimus).
- All other endpoints are trivial suspend functions returning the same DTOs as `api.ts`.
- **Network security:** if prod `apiBase` is plain `http://`, add a `network-security-config` cleartext exception for that single host (Android blocks cleartext by default). TLS preferred — the prod box already terminates `wss` for the Forge.

### 4.2 State (`domain/`)

Literal port of the store:

```kotlin
data class ChatState(
    val sessions: PersistentList<Session>, val activeSessionId: Int?,
    val messages: PersistentList<ChatMessage>, val isStreaming: Boolean)

sealed interface ChatAction { /* the 17 actions, same names */ }
fun reduce(state: ChatState, action: ChatAction): ChatState   // pure, fixture-tested
```

`ChatViewModel` holds `MutableStateFlow<ChatState>` + `dispatch()`. The send pipeline ports `ChatMain.tsx` mechanism-for-mechanism:

- **Chunk coalescing:** buffer deltas, flush once per frame (`withFrameNanos`), `charsSoFar` stamped as `afterChars` on tools (flush *before* recording a tool, same ordering guarantee).
- **Watchdog:** 1s ticker; `STALL_MS=15_000` → SET_STATUS "Waiting on {MODEL} — Ns, no tokens yet (may be rate-limited)"; `DEAD_MS=300_000` → cancel with the phase-specific message (**copy the three diagnostic strings verbatim** — no-ack / tool-stuck / accepted-but-silent).
- **Reattach:** on session enter, `/chat/active?session_id` → attach stream appended as a streaming bubble ("Reconnecting"), attached-set bookkeeping incl. the "no terminal seen → forget the request_id so a later return re-attaches" rule.
- **Abort-on-switch**, **stream-closed-without-terminal finalization**, **error classification** (IOException → "Couldn't reach the backend — network error…"), **title polling**, **regenerate/edit** — all as documented in §1.2.
- **Lifecycle deltas (Android-required, invisible to UX):** every poll loop (health 8s, comms 3s, active-runs 8s, house-party 4s, peers 10s) runs under `repeatOnLifecycle(STARTED)` — paused in background, resumed with an immediate tick on return. Process death mid-turn is already healed by the reattach path — it becomes the *standard* Android resume story.

### 4.3 Rendering pipeline

- **Message list:** `LazyColumn` with stable message keys; items are `@Stable` data — only the streaming row recomposes (the `MemoMessage` discipline maps directly onto Compose skipping).
- **Typewriter:** per-message `withFrameNanos` loop, `FLOOR=45`, `CATCH_UP=7`, dt-clamped at 50ms; skipped when content contains ``` (fence). Markdown re-parse gated at 80ms behind the reveal counter.
- **Segments:** `buildSegments(fullText, tools, revealedLen)` — pure port. Each settled text segment's parsed markdown tree is remembered (`remember(text)`); only the live tail re-parses.
- **Markdown:** commonmark AST → Compose: paragraphs/lists/tables/blockquote/hr per the prose spec; heading composables implement the `MAIN_SUB` split; a `p` starting with `Sources` gets the chip treatment; fence dispatch: `chart` → ChartBlock, `calendar` → CalendarBlock, HPP aliases/content-detection → HousePartyWarning, `html|svg` → WidgetFrame(WebView), else CodeBlock. Pre-processors `normalizeCodeFences` / `prepareMath` / `sanitizePartialMarkdown` and `looksIncomplete` ported verbatim with fixture tests.
- **Scroll behavior:** pin-to-bottom, instant during streaming / animated for new messages; Comms tray keeps its own pinned-unless-scrolled-up rule (60px threshold).

### 4.4 Surfaces & "navigation"

Single Activity, no nav library. `MainScreen` = ambient background → HUD strip → header → (roster strip) → chat/welcome → composer, with overlay slots exactly like `Layout.tsx`: drawer (sidebar), SettingsModal, SystemsBoard, CommsTray, RosterModelWindow, HousePartyModal, AgentSwitcherOverlay, PartyActivation — each a full-screen composable with its own enter animation and Esc-equivalent = system back (predictive back dismisses overlays in the same order Esc does on desktop, incl. "retract EXTEND_ first, close second").

---

## 5. Feature parity matrix (build checklist)

| # | Surface | Android implementation | Parity notes |
|---|---|---|---|
| 1 | Theme engine + morph + party cycle | `ThemeEngine` + accent `Animatable` + party coroutine | Fixture-tested vs TS output |
| 2 | Glass / seams / prose styles | `hbGlass`, `hbSeam*`, prose composables | Token gallery screen |
| 3 | Ambient background | Canvas + keyframed blobs/sweeps | Re-hues live during morph |
| 4 | HUD strip + DIAG | Compose row, health poll | Mobile variant only (this *is* mobile) |
| 5 | Header | Compose row | `hb-hide-sm` items stay hidden; FORGE LINK text-field workspace |
| 6 | Sidebar drawer | Fixed drawer + frosted scrim | 0.28s spring-out curve, live-run jewels, typewriter titles |
| 7 | Welcome view | Compose + two typewriters + clock | war-room copy variant |
| 8 | Chat stream + tool feed | §4.2/§4.3 | diff/command expanders incl. |
| 9 | Composer | Photo Picker + SAF, budget, models, voice, send/stop | "+" overflow menu is the layout (mobile spec) |
| 10 | Markdown/prose | commonmark + custom renderer | tables scroll horizontally in-place |
| 11 | Code blocks | Highlights + JetBrains Mono | vscDarkPlus-approx theme |
| 12 | Math | KaTeX WebView rasterizer (cache) | currency-`$` protection ported |
| 13 | Chart fence | Custom Canvas (line/area/bar/pie) | glass tooltip on tap; MATERIALIZING placeholder |
| 14 | Calendar fence | Pure Compose port | HUD ring, glowing today, ghost layers |
| 15 | HTML/SVG widgets | WebView + injected scripts | complete-markup gate; Code/Copy buttons on tap (no hover) |
| 16 | Files / uploads / lightbox | OkHttp→MediaStore download, Coil, full-screen viewer | amber DOWNLOAD chip |
| 17 | Sessions (group/rename/delete/search) | Sidebar + dialogs | Today/Yesterday/… grouping |
| 18 | Settings 7 tabs | Full-screen sheet w/ left rail → top tabs on narrow | OAuth via Custom Tabs + status polling |
| 19 | ConfigTab | Typed field editor, masked secrets | applied-live vs restart-required results |
| 20 | Systems board | Single-column scroll (mobile spec) | knowledge bank EDIT/COMMIT/409, HISTORY/RESTORE |
| 21 | Comms tray | Full-width bottom sheet-style slab | 3s poll, EXTEND_, STAND DOWN |
| 22 | War room (3-state) + cinematics | Party overlay composables, exact timings | ignite-under-cover profile swap |
| 23 | ROSTER CORES | Modal (drag optional on phone) | desktop + telegram pins |
| 24 | HPP modal + warning banner | Compose dialog, masked field | passphrase never enters transcript |
| 25 | Agent switcher | Full-screen armoury; horizontal pod pager; tap-to-focus, tap-again-to-engage | keyboard shortcuts n/a; add long-press header brand as the entry point (plus sidebar dropdown parity) |
| 26 | Offline transcript cache | JSON files, saved on settle | server-wins-unless-empty rule |
| 27 | Read-aloud / voice input | `TextToSpeech` / `SpeechRecognizer` (en-US) | same markdown-strip regex |
| 28 | Budget mode | Same toggle + post-turn re-sync | |
| 29 | Welcome remark | `/welcome/{agent}` typed at 26ms/char | |
| 30 | Config/first run | Uplink setup screen (apiBase + apiKey → Keystore) | replaces Electron env config; QR pairing = nice-to-have |

---

## 6. Android-exclusive features

Additive only; all wear the holo skin. Ordered by value-per-effort.

**Phase A (ship with v1):**
1. **Push notifications** — the one exclusive that needs backend work. Igor gets a tiny additive surface: `POST /devices` (register FCM token) + a `push` sender used by `output_mode="push"` triggers (today that path is Telegram-oriented). Per-agent `NotificationChannel`s (accent color, monogram icon) so Sentinel alerts look like Sentinel. n8n briefs land as real notifications. (If FCM/Google dependence is unwanted: self-hosted **ntfy** topic with the app subscribing; decide at implementation.)
2. **Detached-turn notifications** — leave mid-turn and a small foreground service tails `/chat/attach/{id}` and posts the finished answer as a notification (deep-links back to the session). Pure client feature; the backend's detached-run design makes it free.
3. **Share sheet target** — `ACTION_SEND`/`SEND_MULTIPLE` for text/URLs/images/PDFs → opens composer with attachments prefilled ("send anything to SPEDA").
4. **App shortcuts** — long-press icon: New chat per agent + War Room (static shortcuts, agent-tinted icons).
5. **Biometric lock** — `BiometricPrompt` gate on launch/resume (configurable). The phone carries the key to a very personal brain.
6. **Notification inline reply** — `RemoteInput` on chat-result notifications → replies POST straight to `/chat/{agent}` without opening the app.

**Phase B (post-v1):**
7. **Glance home-screen widgets** — clock + greeting + JARVIS remark widget; morning-brief widget (renders the latest `push` brief); quick-ask input widget. Glance's styling is limited — design a simplified flat-holo variant, don't fake the glass badly.
8. **Quick Settings tile** — "Talk to SPEDA" tile → voice input straight into a new turn.
9. **Camera capture** into attachments (one tap from the "+" menu).
10. **Transcript search** — Room FTS over the local mirror (searches even offline); extends the sidebar search beyond titles.
11. **Wear OS / Assistant integration** — exploration only; not planned.

---

## 7. Parity verification strategy

1. **Fixture tests (logic parity):** a tiny Node script (run manually in `packages/heartbreaker`) dumps JSON fixtures from the TS originals — `buildThemeVars(accent)` for all 9 brand accents + warroom amber, `buildSegments` cases, `looksIncomplete` cases, `prepareMath`/`sanitizePartialMarkdown` cases, reducer action sequences. The Kotlin ports must reproduce them exactly (unit tests in `designsystem`/`domain`). Fixtures are checked in; the script is rerun when the TS changes.
2. **Screenshot ritual (visual parity):** `npm run web:dev` → Chrome device mode at 393×851 (Pixel-class) → screenshot each surface; same surfaces captured on device/emulator; overlay-diff. Acceptance: identical tokens (guaranteed by #1), spacing within ~2dp, identical motion durations/curves (timed captures for morph/cinematics).
3. **Compose snapshot tests** (Paparazzi) for the design-system components (glass states, prose elements, chart types) to freeze fidelity once achieved.
4. **Behavior walkthrough:** scripted end-to-end pass per milestone — stream with tools, kill the app mid-turn and reattach, switch agents mid-stream, engage/stand-down war room, offline-open a cached session, 409 a memory commit.

---

## 8. Milestones

| M | Scope | Done when |
|---|---|---|
| **M0 — Foundation** | Project scaffold; tokens/fonts/icons; ThemeEngine + morph + party cycle; `hbGlass`/seams; ambient background; token-gallery debug screen; Uplink setup (apiBase/apiKey, Keystore) | Gallery matches web side-by-side; theme fixtures green; `/health` polled and shown in a bare HUD strip |
| **M1 — Chat core** | SSE client; reducer + ViewModel; composer (text-only) + send/stop; message list; typewriter; WorkingStatus; tool feed; watchdog; error banners; session create/select; title polling; transcript cache; reattach + abort-on-switch | Full text conversation with tools is indistinguishable from web (minus rich fences); kill-and-reattach works |
| **M2 — Rich content** | Markdown renderer + prose styles; code blocks; math; chart/calendar fences w/ MATERIALIZING; WebView widgets; file cards + downloads; image attachments + lightbox; doc uploads; voice input + read-aloud | The full fence-test conversation renders 1:1 |
| **M3 — Shell** | Sidebar drawer (groups/search/rename/delete/live jewels); header; HUD DIAG; welcome view; model picker + budget; Settings all 7 tabs incl. ConfigTab, OAuth flows, automations, import/index | Daily-driver complete for SPEDA solo use |
| **M4 — Multi-agent theatre** | Agent switcher overlay + morph; brands; comms tray; systems board (incl. knowledge bank edit/history); war-room 3-state + activation cinematics; roster strip; ROSTER CORES; HPP modal/banner | Full parity checklist (§5) signed off via §7 ritual |
| **M5 — Exclusives + release** | Phase A exclusives (push + backend `POST /devices`, detached-turn notifications, share target, shortcuts, biometric lock, inline reply); battery/lifecycle audit; release signing; install | v1 on the owner's phone |

Sequencing note: M0→M2 are strictly ordered; M3 and M4 can interleave; M5 last. Each milestone ends with the §7 ritual on its surfaces.

---

## 9. Risks & open questions

| Risk / question | Position |
|---|---|
| **Blur performance** with many glass layers | The desktop already forbids nested blur (occluding fills) — enforce the same rule; expected live blur surfaces per screen: ≤3. Profile on real hardware in M0; the no-blur fallback is visually sanctioned by the CSS itself |
| **Markdown fidelity** | Owning the renderer (commonmark AST → Compose) removes the library-fight risk at the cost of upfront work; the prose spec in §1.1 is the acceptance list |
| **KaTeX exactness** | WebView rasterizer is the true-parity path but the fiddliest piece of M2; jlatexmath fallback documented. Decide after measuring how much math actually appears in real sessions |
| **Recharts look** | Custom canvas is committed; tooltip becomes tap-based (no hover on touch) — the only intentional interaction delta, matching the web-on-phone behavior anyway |
| **Hover-revealed actions** (message action bar, widget Code/Copy) | Mobile web already implies tap-to-reveal; Android: tap message → action bar, matching `(pointer: coarse)` behavior. Long-press adds nothing the web doesn't have — keep tap |
| **SamsungOne fonts** | Missing from the repo today (separate task filed). Port proceeds with the *actual* stack (Rajdhani/Inter/JBM); if the Samsung TTFs land later, adding them to `res/font` is a token-level change |
| **Cleartext HTTP** | Confirm prod scheme; add scoped `network-security-config` only if needed |
| **FCM vs self-hosted push** | FCM is the reliable default; ntfy keeps the stack sovereign. Decide at M5; the client abstraction (`PushTransport`) keeps both viable |
| **Keyboard shortcuts** (Alt+A, Esc, arrows) | Replaced by: long-press brand → switcher, predictive back → Esc semantics, touch nav in the armoury. Hardware-keyboard users (tablet + keyboard) get the desktop bindings for free via Compose key handling — cheap to add in M4 |
| **`speda.db` at repo root / server deploy** | Untouched. The Android package adds no Python, no Docker layer, nothing the GitOps deploy notices |

---

## 10. Explicit non-goals

- No tablet-optimized two-pane layout in v1 (the 768px+ desktop layout could come later — the breakpoint logic ports naturally).
- No Wear OS, no Assistant role, no iOS.
- No Compose Multiplatform / KMP sharing with the web client — the parity contract is fixtures + screenshots, not shared code.
- No re-theming, "material you", dynamic color, or light mode (desktop lists light mode as "soon"; it arrives here when it arrives there).
