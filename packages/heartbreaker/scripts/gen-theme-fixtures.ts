/**
 * Parity fixture generator (plan §7, logic parity).
 *
 * Dumps `buildThemeVars(accent)` output — straight from the SHIPPING theme.ts —
 * for every brand accent plus the war-room amber, into a JSON the Kotlin
 * ThemeEngine tests assert against. Rerun whenever theme.ts / brands.ts change:
 *
 *   node --experimental-strip-types packages/heartbreaker/scripts/gen-theme-fixtures.ts
 *
 * (Node ≥ 22.7 strips the TS types at load; no build step, no tsc.)
 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
// theme.ts has zero imports, so Node's type-stripping loads it directly. We only
// need its MATH to be parity-tested; the accent values below are plain identity
// strings mirroring profile/brands.ts + profile/warroom.ts (kept in sync with
// designsystem Brands.kt). Importing brands.ts/warroom.ts would drag in their
// extensionless internal imports, which bare Node ESM can't resolve.
import { buildThemeVars, deriveAccents } from '../src/renderer/src/profile/theme.ts'

const accents: Record<string, string> = {
  speda: '#36abca',
  ultron: '#8a93a6',
  centurion: '#d8483c',
  sentinel: '#d99c44',
  atomix: '#3fae74',
  nightcrawler: '#9165e6',
  optimus: '#2f4f8f',
  orion: '#e0703a',
  warroom: '#f2b75c',
}

const themeVars: Record<string, Record<string, string>> = {}
const accentFamily: Record<string, { accent: string; bright: string; dim: string }> = {}
for (const [key, accent] of Object.entries(accents)) {
  themeVars[key] = buildThemeVars(accent)
  accentFamily[key] = deriveAccents(accent)
}

const __dirname = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(
  __dirname,
  '../../heartbreaker-android/designsystem/src/test/resources/fixtures',
)
mkdirSync(outDir, { recursive: true })

writeFileSync(
  resolve(outDir, 'theme_vars.json'),
  JSON.stringify({ accents, themeVars, accentFamily }, null, 2) + '\n',
)

console.log(
  `wrote theme_vars.json — ${Object.keys(accents).length} agents:`,
  Object.keys(accents).join(', '),
)
