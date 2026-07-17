# Heartbreaker Droid

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
- **Fonts** — `docs/FONTS.md`: bundle the OFL TTFs into `app/src/main/res/font`;
  until then system fallbacks render (metrics already ported).
- **Cleartext** — `res/xml/network_security_config.xml`: add the prod host only if
  its `apiBase` is plain `http://`.

## Next

M2 (Rich content) per the plan: markdown renderer + prose styles, code blocks,
math, chart/calendar fences, WebView widgets, file cards + downloads, image
attachments + lightbox, doc uploads, voice input + read-aloud.
