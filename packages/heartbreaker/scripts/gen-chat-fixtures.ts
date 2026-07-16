/**
 * Parity fixture generator for the chat DOMAIN logic (plan §7).
 *
 * buildSegments lives inside Message.tsx alongside React imports, so it can't be
 * imported under Node type-stripping. It is copied VERBATIM below (keep in sync
 * with Message.tsx). The copy still executes the REAL JS semantics — index math,
 * grouping, slice boundaries — so it catches Kotlin port bugs the hand-written
 * cases might miss.
 *
 *   node --experimental-strip-types packages/heartbreaker/scripts/gen-chat-fixtures.ts
 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// ── VERBATIM copy of buildSegments from Message.tsx ─────────────────────────
type Tool = { id: string; afterChars?: number }
type Segment = { kind: 'text'; text: string } | { kind: 'tools'; tools: Tool[] }

function buildSegments(fullText: string, tools: Tool[], revealedLen: number): Segment[] {
  const visible = tools
    .filter(t => (t.afterChars ?? 0) <= revealedLen)
    .slice()
    .sort((a, b) => (a.afterChars ?? 0) - (b.afterChars ?? 0))
  const segments: Segment[] = []
  let cursor = 0
  let i = 0
  while (i < visible.length) {
    const pos = Math.min(visible[i].afterChars ?? 0, revealedLen)
    if (pos > cursor) {
      segments.push({ kind: 'text', text: fullText.slice(cursor, pos) })
      cursor = pos
    }
    const group: Tool[] = []
    while (i < visible.length && Math.min(visible[i].afterChars ?? 0, revealedLen) === pos) {
      group.push(visible[i])
      i++
    }
    segments.push({ kind: 'tools', tools: group })
  }
  if (cursor < revealedLen) {
    segments.push({ kind: 'text', text: fullText.slice(cursor, revealedLen) })
  }
  return segments
}

// ── Cases ───────────────────────────────────────────────────────────────────
const cases = [
  { name: 'no tools partial reveal', fullText: 'hello world', tools: [], revealedLen: 5 },
  { name: 'no tools full reveal', fullText: 'hello world', tools: [], revealedLen: 11 },
  { name: 'one tool mid-text', fullText: 'hello world', tools: [{ id: 't1', afterChars: 5 }], revealedLen: 11 },
  { name: 'tool not yet revealed', fullText: 'hello world', tools: [{ id: 't1', afterChars: 20 }], revealedLen: 5 },
  { name: 'two tools grouped same offset', fullText: 'hello world', tools: [{ id: 't1', afterChars: 5 }, { id: 't2', afterChars: 5 }], revealedLen: 11 },
  { name: 'tool at zero', fullText: 'abc', tools: [{ id: 't1', afterChars: 0 }], revealedLen: 3 },
  { name: 'reveal exactly at tool offset', fullText: 'hello world', tools: [{ id: 't1', afterChars: 5 }], revealedLen: 5 },
  { name: 'undefined afterChars treated as zero', fullText: 'abc', tools: [{ id: 't1' }], revealedLen: 2 },
  { name: 'tools out of order sorted', fullText: 'abcdef', tools: [{ id: 't2', afterChars: 4 }, { id: 't1', afterChars: 2 }], revealedLen: 6 },
  { name: 'clipped tool offset past reveal groups at revealedLen', fullText: 'abcdef', tools: [{ id: 't1', afterChars: 2 }, { id: 't2', afterChars: 10 }], revealedLen: 4 },
]

const out = cases.map(c => ({
  name: c.name,
  fullText: c.fullText,
  tools: c.tools,
  revealedLen: c.revealedLen,
  segments: buildSegments(c.fullText, c.tools as Tool[], c.revealedLen).map(s =>
    s.kind === 'text' ? { kind: 'text', text: s.text } : { kind: 'tools', toolIds: s.tools.map(t => t.id) },
  ),
}))

const __dirname = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(__dirname, '../../heartbreaker-android/app/src/test/resources/fixtures')
mkdirSync(outDir, { recursive: true })
writeFileSync(resolve(outDir, 'segments.json'), JSON.stringify(out, null, 2) + '\n')
console.log(`wrote segments.json — ${out.length} cases`)
