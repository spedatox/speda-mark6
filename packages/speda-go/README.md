# Speda GO

Native Android (Kotlin + Jetpack Compose) port of the Heartbreaker desktop client,
targeting a 1:1 visual/UX parity with the sub-768px web layout. See
[`docs/ANDROID_PORT_PLAN.md`](../../docs/ANDROID_PORT_PLAN.md) for the full plan
and the parity contract.

This package is **inert to the GitOps prod deploy** — the server never runs
Gradle, so nothing here is built or shipped by the backend.

## Status — M0 (Foundation) + M1 (Chat core)

### M1 — Chat core

| Area | Where | Source of truth |
|---|---|---|
| Chat models + 19-action reducer | `app/.../domain/ChatModels.kt`, `ChatState.kt` | `store/chat.ts` |
| Segment interleaving (buildSegments) | `app/.../domain/Segmenter.kt` | `Message.tsx` |
| Tool status / summary / typewriter / watchdog | `app/.../domain/{ToolStatus,Watchdog}.kt` | `Message.tsx`, `ChatMain.tsx` |
| SSE client + endpoints | `app/.../data/IgorApi.kt`, `SseEvent.kt` | `lib/api.ts` |
| Offline transcript cache | `app/.../data/MessageCache.kt`, `MessageJson.kt` | `store/messageCache.ts` |
| Streaming engine (coalesce / watchdog / reattach / abort-on-switch / title poll) | `app/.../ui/chat/ChatViewModel.kt` | `ChatMain.tsx` |
| Chat UI (list, typewriter, tool feed, working status, composer, sessions) | `app/.../ui/chat/*` | `Message.tsx`, `InputBar.tsx` |

M1 renders text as plain prose; the full markdown/prose renderer, rich fences,
files/images and the real sidebar/header land in M2/M3. The token gallery
(`ui/gallery`) remains as the design-system reference surface.

**Parity verification done here:** `buildSegments` fixtures generated from a
verbatim copy (`scripts/gen-chat-fixtures.ts` → `segments.json`), asserted by
`SegmenterTest`; the 19-action reducer's subtle rules covered by `ReducerTest`.

## Status — M0 (Foundation)

Implemented, grounded value-for-value in `packages/heartbreaker/src/renderer/src`
(the parity source of truth):

| Area | Where | Source of truth |
|---|---|---|
| Colour math + theme engine | `designsystem/.../color`, `.../theme` | `profile/theme.ts` |
| Base token tables | `designsystem/.../theme/BaseTokens.kt` | `profile/theme.ts`, `theme/heartbreaker.css` |
| Brands / roster / party colours | `designsystem/.../brand/Brands.kt` | `profile/brands.ts`, `warroom.ts`, `lib/agents.ts` |
| Accent morph + House Party parade | `designsystem/.../theme/HbTheme.kt` | `theme.ts` `morphTheme` / `startPartyCycle` |
| The ONE glass material + seams | `designsystem/.../glass` | `.glass` / `.hb-seam-*` in `heartbreaker.css` |
| Ambient background | `designsystem/.../background/AmbientBackground.kt` | `components/NeuralBackground.tsx` |
| Typography ramp | `designsystem/.../type/HbType.kt` | `heartbreaker.css` |
| Motion tokens | `designsystem/.../motion/Motion.kt` | `theme.ts` + CSS |
| Uplink setup (Keystore) | `app/.../data`, `app/.../ui/UplinkSetupScreen.kt` | replaces Electron env config |
| `/health` poller + bare HUD strip | `app/.../data/HealthPoller.kt`, `ui/HudStrip.kt` | `lib/useHealth.ts`, `HudFrame.tsx` |
| Token-gallery reference screen | `app/.../ui/gallery/TokenGalleryScreen.kt` | plan M0 acceptance surface |

### Parity verification already done here

- **Theme fixtures generated from the shipping TS**:
  `node --experimental-strip-types packages/heartbreaker/scripts/gen-theme-fixtures.ts`
  → `designsystem/src/test/resources/fixtures/theme_vars.json` (9 agents).
- **`ThemeEngineTest`** asserts the Kotlin engine reproduces `buildThemeVars` /
  `deriveAccents` byte-for-byte (runs on the JVM, no device).
- The engine algorithm was cross-checked independently (369 assertions across 9
  agents, all matching) — see the port notes. Rounding uses `floor(x+0.5)` to
  match JS `Math.round`, **not** Kotlin's banker's `round`.

## Build status

**Green.** The whole project compiles, the unit tests pass, and `assembleDebug`
produces an installable APK. Verified on this box with Android Studio's bundled
JBR (JDK 21) + Gradle 8.11.1 + AGP 8.9.0:

| Module | Test | Tests | Failures |
|---|---|---|---|
| designsystem | `ThemeEngineTest` (theme parity, 9 agents) | 3 | 0 |
| app | `SegmenterTest` (buildSegments fixtures) | 1 | 0 |
| app | `ReducerTest` (the 19-action store) | 8 | 0 |

Android Studio syncs straight from `gradle/wrapper/gradle-wrapper.properties`
(Gradle 8.11.1). To build from this shell without the IDE:

```bash
export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"
export ANDROID_HOME="$LOCALAPPDATA/Android/Sdk"
GRADLE=~/.gradle/wrapper/dists/gradle-8.11.1-bin/*/gradle-8.11.1/bin/gradle
$GRADLE :designsystem:testDebugUnitTest :app:testDebugUnitTest :app:assembleDebug
```

Note: `gradlew`/`gradle-wrapper.jar` are **not** committed (only the
`.properties`), so there is no `./gradlew`. Android Studio doesn't need it; run
`gradle wrapper` once if you want the CLI script.

### Gotcha that already bit once

A batch of jars in `~/.gradle/caches/modules-2` downloaded **corrupt**
(right byte-count, zero-padded tail, no ZIP end header). Symptom was a very
misleading `Could not apply requested plugin [id: 'com.android.application'] …
does not provide a plugin with id` — nothing to do with the AGP/Gradle versions.
Gradle names each cache folder after the artifact's SHA-1, so the cache is
self-verifying; compare `sha1sum <jar>` to its parent folder name (Gradle strips
leading zeros), delete the mismatching module dirs, and re-resolve. Afterwards
clear the stale script caches (`.gradle/`, `~/.gradle/caches/8.11.1/kotlin-dsl`),
or the build scripts stay compiled against the missing `android {}` DSL.

### Still open

- **Not run on a device**, and the §7 **visual-parity ritual has not been done** —
  no screenshot diff against the web yet. Correctness is asserted only by the
  unit tests above.
- ~~Fonts~~ — **done**: Rajdhani + Inter + JetBrains Mono are bundled in
  `designsystem/src/main/res/font` and wired in `HbFonts`. See `docs/FONTS.md`.
- **Cleartext** — `res/xml/network_security_config.xml`: add the prod host only if
  its `apiBase` is plain `http://`.

## Status — M2/M3 shipped, M4 in progress

The two milestone tables above are historical: M2 (rich content) and M3 (shell,
settings, systems board) landed, and the sections describing them as future work
are stale. What is worth reading is the **remaining** gap against the desktop.

### Landed since (M4 — multi-agent theatre)

| Surface | Where | Source of truth |
|---|---|---|
| ```html widgets (sealed WebView + injected base styles/resize bridge) | `ui/prose/HtmlBlock.kt` | `WidgetFrame.tsx` |
| ```hpp-warning salvage banner + alias/content detection | `ui/prose/Prose.kt` | `Message.tsx` |
| Composer budget mode + dictation | `ui/chat/Composer.kt`, `ui/chat/ChatScreen.kt` | `InputBar.tsx` |
| Protocols — Lockdown (engage/stand-down), Lifeboat/Doormat/Octavius (read-only + backup-now), Skyfall countdown, House Party (read-only status) | `ui/settings/ProtocolsTab.kt`, `ui/skyfall/SkyfallCountdown.kt` | `ProtocolsTab.tsx` |
| Pending permission asks — global tray + inline `permission_request` SSE card | `ui/chat/AsksTray.kt`, `ui/chat/ChatViewModel.kt` | `PendingAsksTray.tsx`, `InteractionPrompt.tsx` |
| Custom MCP servers + web portals (add/edit/delete, sign-in, forget session) + Microsoft Graph connection | `ui/settings/ConnectionsTab.kt` | `McpServersPanel.tsx`, `PortalsPanel.tsx` |
| Subagent delegation panel — per-turn runs, foldable steps | `ui/chat/SubagentPanel.kt`, `domain/ChatState.kt` | `SubagentPanel.tsx`, `SubagentDetailView.tsx` |
| Telegram model pins (second per-agent override, separate from the app pin) | `ui/systems/RoutingMatrix.kt` | `RosterModelWindow.tsx` |
| Memory record status (observations/at-risk/verdict) + declared-but-empty folders in the knowledge bank tree | `ui/systems/KnowledgeBank.kt` | `SystemsBoard.tsx` |

**House Party stays desktop-only, on purpose.** No engage path exists here —
`HeartbreakerRoot.kt` documents why (the war room needs a stage a phone does not
build, and the backend refuses to ENGAGE from a non-desktop client). The
Protocols tab shows the flag read-only, greyed, with the reason, rather than
omitting it — see `ProtocolsTab.kt`'s own doc comment.

**Deliberate delta:** the Protocols tab's CORES-equivalent stays on the systems
board rather than a second window — the board already owns AGENT CORES and
Telegram pins, and a phone-sized modal would give them two places to be edited
from.

### Still missing against the desktop

- **Voice mode** — VOX, the 3D orb, spoken replies and the canvas HUD
  (`VoiceMode.tsx`, `VoiceOrb.tsx`, `VoiceCanvas.tsx`). Postdates the port plan
  entirely; the orb is Three.js and needs a real Compose/OpenGL port, not a
  transliteration.
- **Subagent panel** has no chat-like full-detail thread view yet (the desktop's
  `SubagentDetailView.tsx`) — steps expand inline in the run card instead of
  opening their own screen.
- **Portal advanced fields** — CSS selectors, extra form fields, success-URL
  matching, per-agent access scoping. The add-portal form covers login/home
  URL, username, password and a note; the rest stays desktop-only for now.

## Shipping — the downstream mirror and APK releases

This package is the **source of truth**. It is also published on its own as
[`spedatox/speda-go`](https://github.com/spedatox/speda-go), and that repo is a
**mirror** — never hand-edit it, the next sync overwrites whatever you change.

`.github/workflows/speda-go.yml` (in the monorepo root, since only the root
`.github` is live for Actions) runs on every push to `main` that touches
`packages/speda-go/**`:

1. unit tests, then `:app:assembleRelease` signed with the personal keystore
   from repo secrets — the run fails rather than shipping an unsigned APK;
2. `git archive` of this directory is rsynced over the standalone repo
   (`--delete`, so the mirror is exact) and pushed as one `sync:` commit;
3. the APK is attached to a GitHub Release there, tagged
   `v<spedaGoVersion>-b<run number>`.

The **only** file exempt from the mirror is the standalone repo's `README.md`,
which keeps its own product front page. Everything else must stay identical.

Versioning: `spedaGoVersion` in `gradle.properties` is the marketing version;
CI sets `versionCode` to the workflow run number and `versionName` to
`<spedaGoVersion>-b<run>`, so every build upgrade-installs over the last one.
Bump `spedaGoVersion` by hand when the milestone changes. A local
`assembleRelease` with no signing env set produces an **unsigned** APK at
`versionCode 1` — that is deliberate, not a misconfiguration.
