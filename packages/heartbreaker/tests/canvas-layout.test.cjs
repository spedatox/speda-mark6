// SPDX-License-Identifier: AGPL-3.0-or-later
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const exportsObject = {}
const source = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/voicePanels.ts'), 'utf8')
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports: exportsObject })
const { pack, splitPanels, GAP } = exportsObject
const panel = (kind, n) => ({ id: `${kind}-${n}`, kind, title: `${kind}_${n}`, source: '' })
const board = [panel('calendar', 1), panel('table', 1), panel('chart', 1), panel('widget', 1), panel('stat', 1), panel('stat', 2), panel('timeline', 1), panel('card', 1), panel('narration', 1)]

test('a crowded board grows vertically without shrinking reading sizes', () => {
  const placed = pack(board, 1440, 500, 1220, 320)
  assert.equal(placed.length, board.length)
  assert.ok(Math.max(...placed.map(p => p.y + p.h)) > 500)
  for (const p of placed) {
    assert.ok(p.w >= (p.kind === 'stat' ? 280 : 340), `${p.kind} width ${p.w}`)
    assert.ok(p.h >= (p.kind === 'stat' ? 190 : 300))
    assert.ok(p.x + p.w <= 1220 - GAP)
  }
})
test('packing never overlaps windows or loses the final panel', () => {
  for (const width of [320, 687, 1024, 1440, 1920]) {
    const placed = pack([...board, ...board.map(p => ({ ...p, id: p.id + '-copy' }))], width, 500, width - 220, 320)
    for (let i = 0; i < placed.length; i++) {
      const a = placed[i]
      assert.ok(a.w > 0 && a.x >= 0 && a.x + a.w <= width, `${width}: ${a.id} stays inside board`)
      for (const b of placed.slice(i + 1)) assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y, `${a.id} overlaps ${b.id}`)
    }
    assert.equal(placed.length, board.length * 2)
  }
})
test('more panels do not shrink earlier windows or change their row', () => {
  const start = pack(board.slice(0, 2), 1440, 500, 1220, 320)
  const full = pack(board, 1440, 500, 1220, 320)
  assert.deepEqual(JSON.parse(JSON.stringify(full.slice(0, 2))), JSON.parse(JSON.stringify(start)))
})
test('table and interactive HTML retain their renderer source in canvas', () => {
  const panels = splitPanels('```table | Plan\nDAY | PLAN\nMON | Study\n```\n```html | Calculator\n<input type="range"><script>const n=1</script>\n```')
  assert.equal(panels[0].kind, 'table')
  assert.equal(panels[0].source, 'DAY | PLAN\nMON | Study')
  assert.equal(panels[1].kind, 'widget')
  assert.match(panels[1].source, /^```html/)
  assert.match(panels[1].source, /const n=1/)
})

test('native labs keep their fence and short timelines use less board space', () => {
  const panels = splitPanels('```artifact | Practice\n{"kind":"practice"}\n```\n```timeline | Plan\n09:00 — Start\n10:00 — End\n```')
  assert.equal(panels[0].kind, 'artifact')
  assert.match(panels[0].source, /^```artifact/)
  assert.equal(pack(panels, 1200, 500, 1200, 500)[1].h, 184)
})
