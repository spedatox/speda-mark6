# Heartbreaker Droid

Native Android (Kotlin + Jetpack Compose) port of the Heartbreaker desktop client,
targeting a 1:1 visual/UX parity with the sub-768px web layout. See
[`docs/ANDROID_PORT_PLAN.md`](../../docs/ANDROID_PORT_PLAN.md) for the full plan
and the parity contract.

This package is **inert to the GitOps prod deploy** — the server never runs
Gradle, so nothing here is built or shipped by the backend.

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

### NOT verifiable in the authoring sandbox

This Windows box has **no Android Studio / JDK-17 Gradle / Android SDK**, so the
project was **not compiled, run, or screenshot-diffed here**. Before relying on
M0, in Android Studio (Ladybug+ / JDK 17):

1. `gradle wrapper --gradle-version 8.11.1` (materialises `gradle-wrapper.jar`;
   only the `.properties` is committed).
2. Gradle sync — resolve the version catalog (`gradle/libs.versions.toml`). If a
   pinned artifact version (Compose BOM, Haze `1.5.4`, OkHttp alpha) needs a bump,
   do it in the catalog only.
3. `./gradlew :designsystem:testDebugUnitTest` — the theme fixture parity gate.
4. Run `:app` on an API 31+ device; drive the §7 ritual on the token gallery
   (switch agents → watch the morph + ambient re-hue; HOUSE PARTY → parade).

### Known M0 seams to close on real hardware

- **Haze API/version** (`HbHaze.kt`) — isolated from the core material on purpose;
  reconcile the `hazeSource`/`hazeEffect` surface with the resolved Haze version.
- **Fonts** — `res/font/README.md`: bundle the OFL TTFs; until then system
  fallbacks render (metrics already ported).
- **Cleartext** — `res/xml/network_security_config.xml`: add the prod host only if
  its `apiBase` is plain `http://`.

## Next

M1 (Chat core) per the plan: SSE client, chat reducer + ViewModel, composer,
message list, typewriter, watchdog, transcript cache, reattach.
