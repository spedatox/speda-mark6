# Bundled type

The fonts are **bundled** in `designsystem/src/main/res/font` (the design system
owns type). `HbFonts` in `designsystem/.../type/HbType.kt` is the single place
they are wired.

| File | Family / weight | Used for |
|---|---|---|
| `rajdhani_light.ttf` | Rajdhani 300 | `.hb-num-thin` — the welcome clock |
| `rajdhani_regular.ttf` | Rajdhani 400 | |
| `rajdhani_medium.ttf` | Rajdhani 500 | the typewritten greeting |
| `rajdhani_semibold.ttf` | Rajdhani 600 | `.hb-label`, the query box |
| `rajdhani_bold.ttf` | Rajdhani 700 | header plates, the agent hero |
| `inter_variable.ttf` | Inter (variable `wght`) | `--font-read` / `--font-mono` — body copy + readouts |
| `jetbrains_mono_variable.ttf` | JetBrains Mono (variable `wght`) | code blocks (CodeBlock.tsx) |

All OFL, from Google's official `google/fonts` repo.

## Notes

- **Inter and JetBrains Mono are published only as variable fonts.** Compose
  drives the `wght` axis from the requested `FontWeight` (`Font()`'s default
  `variationSettings` derive from it), so one file covers every weight — API 26+,
  and we're minSdk 31.
- **Rajdhani has no 800.** `heartbreaker.css` asks for `font-weight: 800` on the
  agent hero and H1/H2; browsers clamp that to the 700 face, so `HbFonts.Ui` maps
  `FontWeight.ExtraBold` to `rajdhani_bold.ttf` rather than letting Android
  synthesise a fake heavier weight.
- **SamsungOne / SamsungSharpSans are not shipped and never were.** The renderer
  declares `@font-face` rules pointing at `theme/fonts/SamsungOne-*.ttf`, but that
  directory does not exist in the repo — so every `'SamsungOne','Inter',sans-serif`
  stack in the web actually renders **Inter**. Bundling Inter therefore matches the
  web exactly. If the Samsung TTFs are ever recovered, drop them in here and add a
  family to `HbFonts`; nothing else needs to change.
- Metrics (size / weight / tracking / line-height) live in `HbType`, ported from
  the CSS — they did not change when the real faces landed.
