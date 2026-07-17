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

// ── VERBATIM copies of the markdown pre-processors from Message.tsx ─────────
function normalizeCodeFences(text: string): string {
  return text.replace(/([^\n])```/g, '$1\n```')
}

function prepareMath(text: string): string {
  const parts = text.split(/(```[\s\S]*?```|`[^`]*`)/g)
  return parts
    .map((seg, i) => {
      if (i % 2 === 1) return seg // code — leave untouched
      return seg
        .replace(/(?<![$\\])\$(?=\d)/g, '\\$')                 // currency first
        .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `$$${m}$$`)  // \[ \] → $$ $$
        .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m}$`)    // \( \) → $ $
    })
    .join('')
}

function sanitizePartialMarkdown(text: string): string {
  const fences = (text.match(/```/g) ?? []).length
  if (fences % 2 !== 0) text += '\n```'
  const stripped = text.replace(/```[\s\S]*?```/g, '')
  const ticks = (stripped.match(/(?<!`)`(?!`)/g) ?? []).length
  if (ticks % 2 !== 0) text += '`'
  return text
}

const prepCases: { name: string; input: string }[] = [
  { name: 'fence needs its own line', input: 'see this.```html\n<b>x</b>```' },
  { name: 'fence already on its own line', input: 'see this.\n```html\n<b>x</b>```' },
  { name: 'currency before digit is escaped', input: 'It costs $5 today and $10.50 tomorrow.' },
  { name: 'currency inside inline code untouched', input: 'Outside $5 but `inside $6` stays.' },
  { name: 'currency inside a fence untouched', input: '```js\nconst a = $5\n```\nafter $7' },
  { name: 'display math delimiters', input: 'math \\[x^2 + y^2\\] end' },
  { name: 'inline math delimiters', input: 'inline \\(a+b\\) end' },
  { name: 'dollar not before digit is left alone', input: 'costs $ ten and US$ x' },
  { name: 'unclosed fence gets closed', input: '```js\nconst a = 1' },
  { name: 'unclosed inline tick gets closed', input: 'this is `partial' },
  { name: 'complete fence untouched', input: 'a ```js\nx\n``` b' },
  { name: 'complete inline code untouched', input: 'a `code` b' },
  { name: 'tick inside a fence is not counted', input: '```js\nconst s = `tpl`\n```' },
]

const prep = prepCases.map(c => ({
  name: c.name,
  input: c.input,
  normalizeCodeFences: normalizeCodeFences(c.input),
  prepareMath: prepareMath(c.input),
  sanitizePartialMarkdown: sanitizePartialMarkdown(c.input),
  // the whole TextSegment pipeline, in the order Message.tsx applies it
  prepare: prepareMath(normalizeCodeFences(sanitizePartialMarkdown(c.input))),
}))

const __dirname = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(__dirname, '../../heartbreaker-android/app/src/test/resources/fixtures')
mkdirSync(outDir, { recursive: true })
writeFileSync(resolve(outDir, 'segments.json'), JSON.stringify(out, null, 2) + '\n')
writeFileSync(resolve(outDir, 'markdown_prep.json'), JSON.stringify(prep, null, 2) + '\n')
console.log(`wrote segments.json — ${out.length} cases`)
console.log(`wrote markdown_prep.json — ${prep.length} cases`)
