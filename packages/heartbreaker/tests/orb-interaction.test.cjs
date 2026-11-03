const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const exportsObject = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/orbInteraction.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports: exportsObject })
const { createOrbInteraction, orbBufferSize } = exportsObject
function harness() {
  const frames = new Map(), timers = new Map(), paints = [], commits = []
  let id = 0
  const control = createOrbInteraction({ dock: { dx: 0, dy: 0, scale: 1 },
    paint: (d, active) => paints.push({ ...d, active }), commit: d => commits.push({ ...d }),
    frame: fn => { frames.set(++id, fn); return id }, cancelFrame: id => frames.delete(id),
    delay: fn => { timers.set(++id, fn); return id }, cancelDelay: id => timers.delete(id),
  })
  const run = map => { const pending = [...map.values()]; map.clear(); pending.forEach(fn => fn()) }
  return { control, paints, commits, frames, timers, tick: () => run(frames), settle: () => run(timers) }
}
test('hundreds of pointer events share one paint and one settled settings write', () => {
  const h = harness(); h.control.begin(20, 30)
  for (let i = 1; i <= 600; i++) h.control.move(20 + i, 30 - i)
  assert.equal(h.frames.size, 1); assert.equal(h.commits.length, 0)
  h.tick(); assert.equal(h.paints.length, 1); assert.equal(h.paints[0].dx, 600)
  h.control.end(); assert.deepEqual(h.commits, [{ dx: 600, dy: -600, scale: 1 }])
  assert.equal(h.frames.size, 0)
})
test('wheel bursts accumulate smoothly and save only when idle', () => {
  const h = harness()
  for (let i = 0; i < 20; i++) h.control.zoom(-3)
  assert.equal(h.frames.size, 1); assert.equal(h.timers.size, 1); assert.equal(h.commits.length, 0)
  h.tick(); assert.ok(h.paints[0].scale > 1)
  h.settle(); assert.equal(h.commits.length, 1); assert.ok(h.commits[0].scale > 1)
  h.control.dispose(); assert.equal(h.commits.length, 1)
})
test('scaling during a drag preserves both position and scale', () => {
  const h = harness(); h.control.begin(10, 10); h.control.move(35, 45); h.control.zoom(-100); h.control.move(40, 50)
  assert.equal(h.timers.size, 0); h.control.end()
  assert.equal(h.commits[0].dx, 30); assert.equal(h.commits[0].dy, 40); assert.ok(h.commits[0].scale > 1)
})
test('gesture cancellation flushes once and removes scheduled work', () => {
  const h = harness(); h.control.zoom(-30); h.control.dispose()
  assert.equal(h.commits.length, 1); assert.equal(h.timers.size, 0); assert.equal(h.frames.size, 0)
})
test('a settings echo keeps the settled value and reset applies to the next gesture', () => {
  const h = harness(); h.control.begin(0, 0); h.control.move(50, 50); h.control.end()
  h.control.sync(h.commits[0]); h.control.sync({ dx: 0, dy: 0, scale: 1 }); h.control.begin(0, 0); h.control.move(5, 6); h.control.end()
  assert.deepEqual(h.commits[1], { dx: 5, dy: 6, scale: 1 })
})
test('zoom stays grabbable and GPU buffer dimensions are capped', () => {
  const h = harness(); for (let i=0;i<100;i++) h.control.zoom(-120); h.settle(); assert.equal(h.commits[0].scale, 2.6)
  for (let i=0;i<100;i++) h.control.zoom(120); h.settle(); assert.equal(h.commits[1].scale, .5)
  for (const size of [200, 600, 1456]) { const b = orbBufferSize(size,size,3); assert.ok(b.width * b.ratio <= 2048); assert.equal(b.width,b.height) }
  assert.equal(orbBufferSize(400,400,1).ratio,2)
})
