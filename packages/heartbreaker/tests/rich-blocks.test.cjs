// SPDX-License-Identifier: AGPL-3.0-or-later
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
function load(name) {
  const exports = {}
  const source = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib', name + '.ts'), 'utf8')
  const requireRaw = specifier => ({
    default: fs.readFileSync(path.resolve(__dirname, '../src/renderer/src/lib', specifier.replace(/\?raw$/, '')), 'utf8'),
  })
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, require: requireRaw })
  return exports
}
const { normalizeCodeFences, parseTable, parseTimeline, recoverRichBlock, remarkRichBlocks } = load('richBlocks')
const { buildWidgetDocument } = load('widgetDocument')
const plain = value => JSON.parse(JSON.stringify(value))

test('repairs attached fences without changing template literals or longer fences', () => {
  assert.equal(normalizeCodeFences('Here it is.```html\n<p>Hi</p>\n```'), 'Here it is.\n```html\n<p>Hi</p>\n```')
  for (const source of ['````js\nconst fence = "```html";\n````', '```js\nconst fence = "```html";\n```', '~~~html\n<p>Hello</p>\n~~~']) assert.equal(normalizeCodeFences(source), source)
})

test('recovers the separator-free weekly table from older replies', () => {
  const raw = 'DAY | THE PLAN\nMON | Sleep-in · 13:00 reel\nTUE | 16:20 hospital'
  assert.deepEqual(plain(parseTable(raw)), { headers: ['DAY', 'THE PLAN'], rows: [['MON', 'Sleep-in · 13:00 reel'], ['TUE', '16:20 hospital']] })
  assert.equal(recoverRichBlock('text', raw).language, 'table')
  assert.equal(recoverRichBlock('js', raw), null)
  assert.equal(recoverRichBlock('', raw), null)
})
test('tables preserve code-span pipes, escaped pipes, alignment and empty cells', () => {
  const data = parseTable('| Expression | Result |\n| :--- | ---: |\n| `a | b` | a\\|b |\n| Empty | |')
  assert.deepEqual(plain(data.rows), [['`a | b`', 'a|b'], ['Empty', '']])
  assert.equal(parseTable('A | B\nonly one'), null)
  assert.equal(parseTable('A | B\n--- | ---'), null)
})
test('recovers timeline marker and keeps time/date punctuation intact', () => {
  const block = recoverRichBlock('text', 'timeline | REST OF TONIGHT\n14:30 — Food\n2026-10-27 — Meeting\n~05:00 – Rest')
  assert.equal(block.title, 'REST OF TONIGHT')
  assert.deepEqual(plain(parseTimeline(block.source)), [{ when: '14:30', what: 'Food' }, { when: '2026-10-27', what: 'Meeting' }, { when: '~05:00', what: 'Rest' }])
})
test('fence metadata preserves titles and detects unclosed scripts', () => {
  for (const fence of ['```', '~~~~']) {
    for (const closed of [false, true]) {
      const value = `${fence}html | Calculator\n<input type="range">${closed ? '\n' + fence : ''}`
      const code = { type: 'code', lang: 'html', meta: '| Calculator', position: { start: { offset: 0 }, end: { offset: value.length } } }
      remarkRichBlocks()({ type: 'root', children: [code] }, { value })
      assert.equal(code.data.hProperties['data-title'], 'Calculator')
      assert.equal(code.data.hProperties['data-pending'], !closed)
    }
  }
})
test('widgets get theme defaults, responsive forms and resize reporting', () => {
  const source = '<label>Minutes<input type="range"></label><script>const result=30*7;</script>'
  const doc = buildWidgetDocument(source, { '--hb-cyan': '#d99c44' })
  assert.ok(doc.includes(source))
  assert.match(doc, /--hb-cyan:#d99c44/)
  assert.match(doc, /--hb-text:#dbe6ec/)
  assert.match(doc, /meta name="viewport"/)
  assert.match(doc, /ResizeObserver/)
  assert.match(doc, /setTimeout\(report,0\)/)
  assert.doesNotMatch(doc, /requestAnimationFrame/)
})
test('full documents and SVGs both receive the runtime', () => {
  for (const source of ['<!doctype html><html lang="en"><head data-x="1"></head><body><p>Test</p></body></html>', '<!doctype html><body>Test</body>', '<svg viewBox="0 0 700 100"></svg>']) {
    const doc = buildWidgetDocument(source)
    assert.equal((doc.match(/widget-height/g) || []).length, 1)
    assert.ok(doc.indexOf('widget-height') < doc.lastIndexOf('</body>'))
  }
})
test('widget host isolates executable content and resize channels', () => {
  const host = fs.readFileSync(path.join(__dirname, '../src/renderer/public/widget-host.html'), 'utf8')
  const component = fs.readFileSync(path.join(__dirname, '../src/renderer/src/components/WidgetFrame.tsx'), 'utf8')
  assert.match(host, /connect-src 'none'/)
  assert.match(host, /event.source!==frame.contentWindow/)
  assert.match(component, /event.source !== frame.current\?\.contentWindow/)
  assert.doesNotMatch(host + component, /allow-same-origin|allow-forms|dangerouslySetInnerHTML/)
  const policy = fs.readFileSync(path.join(__dirname, '../src/renderer/index.html'), 'utf8')
  assert.match(policy, /script-src 'self';/)
})

test('vector preview includes drawn content outside the authored viewBox', () => {
  const source = buildWidgetDocument('<svg viewBox="0 0 400 150"></svg>', {}, true)
  const attributes = {}
  const svg = {
    viewBox: { baseVal: { x: 0, y: 0, width: 400, height: 150 } },
    getBBox: () => ({ x: -25, y: 15, width: 510, height: 160 }),
    setAttribute: (key, value) => { attributes[key] = value },
    style: { setProperty() {} },
  }
  const messages = []
  vm.runInNewContext(source.match(/<script>([\s\S]+)<\/script>/)[1], {
    document: {
      body: { querySelector: () => svg, style: {} },
      documentElement: { dataset: {} },
      fonts: { ready: { then() {} } },
    },
    parent: { postMessage: message => messages.push(message) },
    setTimeout: run => run(),
    getComputedStyle: () => ({ zoom: '1' }),
    addEventListener() {},
    MutationObserver: class { observe() {} },
  })
  const [x, y, width, height] = attributes.viewBox.split(' ').map(Number)
  assert.ok(x < -25 && y + height > 175 && x + width > 485)
  assert.equal(attributes.preserveAspectRatio, 'xMidYMid meet')
  assert.equal(messages[0].ratio, width / height)
})

test('interactive hints describe only visible, enabled controls', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../speda-go/app/src/main/assets/artifact-controls.js'), 'utf8')
  const visible = { matches: () => false, getClientRects: () => [1], visibility: 'visible' }
  const groups = [[visible], [{ ...visible, matches: () => true }], [], [visible], [{ ...visible, visibility: 'hidden' }]]
  const context = { getComputedStyle: control => control }
  vm.runInNewContext(source, context)
  assert.equal(context.artifactControls({ querySelectorAll: () => groups.shift() }), 1 | 8)
})
