// SPDX-License-Identifier: AGPL-3.0-or-later
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
function load(name, client = 'heartbreaker') {
  const exports = {}
  const source = fs.readFileSync(path.join(__dirname, '../..', client, 'src/renderer/src/lib', name + '.ts'), 'utf8')
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
test('canvas tables keep conditional probability notation and all three cells', () => {
  const raw = '| Question | Setup | Answer |\n|---|---|---|\n| P(2nd King | 1st was King) | 3 kings left, 51 cards | 3/51 = 1/17 |'
  const expected = [['P(2nd King | 1st was King)', '3 kings left, 51 cards', '3/51 = 1/17']]
  const { splitPanels } = load('voicePanels')
  const [panel] = splitPanels('```table | Worked example: decks and dice logic\n' + raw + '\n```')
  assert.equal(panel.kind, 'table')
  assert.deepEqual(plain(parseTable(panel.source).rows), expected)

  const { repairTablePipes } = load('voicePanels', 'striker')
  const escaped = raw.replace('King | 1st', 'King \\| 1st')
  assert.equal(repairTablePipes(raw), escaped)
  assert.equal(repairTablePipes(escaped), escaped)
  const valid = '| Left | Right |\n|---|---|\n| (1 | 2) |'
  assert.equal(repairTablePipes(valid), valid)
  assert.deepEqual(plain(parseTable(valid).rows), [['(1', '2)']])
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

function runVectorRuntime(svg, globals = {}) {
  const source = buildWidgetDocument('<svg viewBox="0 0 400 150"></svg>', {}, true)
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
    ...globals,
  })
  return messages
}

test('vector preview includes drawn content outside the authored viewBox', () => {
  const attributes = {}
  const messages = runVectorRuntime({
    classList: { contains: () => false },
    viewBox: { baseVal: { x: 0, y: 0, width: 400, height: 150 } },
    getBBox: () => ({ x: -25, y: 15, width: 510, height: 160 }),
    setAttribute: (key, value) => { attributes[key] = value },
    style: { setProperty() {} },
  })
  const [x, y, width, height] = attributes.viewBox.split(' ').map(Number)
  assert.ok(x < -25 && y + height > 175 && x + width > 485)
  assert.equal(attributes.preserveAspectRatio, 'xMidYMid meet')
  assert.equal(messages[0].ratio, width / height)
})

test('themed node labels fit their own box while captions and shorter lines keep their size', () => {
  class SVGRectElement {
    x = { baseVal: { value: 20 } }
    y = { baseVal: { value: 20 } }
    width = { baseVal: { value: 140 } }
    height = { baseVal: { value: 80 } }
    matches() { return true }
    hasAttribute() { return false }
  }
  class SVGTextElement {
    children = []
    style = {}
    hasAttribute() { return false }
    constructor(width, y) {
      this.x = { baseVal: { numberOfItems: 1, getItem: () => ({ value: 90 }) } }
      this.y = { baseVal: { numberOfItems: 1, getItem: () => ({ value: y }) } }
      this.getComputedTextLength = () => width
    }
  }
  const labels = [new SVGTextElement(45, 48), new SVGTextElement(192, 74), new SVGTextElement(192, 130)]
  for (const label of labels) label.parentElement = { children: [new SVGRectElement(), ...labels] }
  runVectorRuntime({
    classList: { contains: () => true },
    viewBox: { baseVal: { x: 0, y: 0, width: 400, height: 150 } },
    getBBox: () => ({ x: 20, y: 20, width: 140, height: 110 }),
    querySelectorAll: () => labels,
    setAttribute() {},
    style: { setProperty() {} },
  }, {
    SVGTextElement,
    SVGRectElement,
    getComputedStyle: () => ({ textAnchor: 'middle', fontSize: '21px', zoom: '1' }),
  })
  assert.deepEqual(labels.map(label => label.style.fontSize), [undefined, '14.000px', undefined])
})

test('interactive hints describe only visible, enabled controls', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../speda-go/app/src/main/assets/artifact-controls.js'), 'utf8')
  const visible = { matches: () => false, getClientRects: () => [1], visibility: 'visible' }
  const groups = [[visible], [{ ...visible, matches: () => true }], [], [visible], [{ ...visible, visibility: 'hidden' }]]
  const context = { getComputedStyle: control => control }
  vm.runInNewContext(source, context)
  assert.equal(context.artifactControls({ querySelectorAll: () => groups.shift() }), 1 | 8)
})

test('native practice and simulation follow the Android fixture contract', () => {
  const { parseNativeArtifact, answerMatches } = load('nativeArtifacts')
  const fixtures = JSON.parse(fs.readFileSync(path.join(__dirname, '../../speda-go/app/src/test/resources/native-artifacts.json'), 'utf8'))
  const practice = parseNativeArtifact(JSON.stringify(fixtures.practice))
  assert.equal(practice.kind, 'practice')
  assert.ok(answerMatches(' 2\n', practice.items[0].answer))
  assert.equal(practice.items[0].choice, undefined)
  assert.equal(practice.items[1].choice.answerIndex, 1)
  assert.equal(practice.items[1].answer, 'Remainder')
  assert.equal(parseNativeArtifact(JSON.stringify(fixtures.practice).replace('"answerIndex":1', '"answerIndex":3')), null)
  assert.equal(parseNativeArtifact(JSON.stringify(fixtures.practice).replace('"Product"', '" Remainder "')), null)
  assert.equal(answerMatches('A  B', 'A B'), false)
  assert.equal(answerMatches('a', 'A'), false)
  const explore = parseNativeArtifact(JSON.stringify(fixtures.explore))
  for (const [value, expected] of fixtures.boundaries) {
    assert.equal(explore.cases.findIndex(step => value <= step.through), expected)
  }
  fixtures.explore.cases.at(-1).through = 100
  assert.equal(parseNativeArtifact(JSON.stringify(fixtures.explore)), null)
})

test('Mark VI SVG kit resolves renderer tokens for the exact agent example', () => {
  const example = fs.readFileSync(path.join(__dirname, '../../../app/profiles/markvi-diagram.svg'), 'utf8')
  const doc = buildWidgetDocument(example, { '--hb-cyan-bright': '#abcdef' }, true)
  assert.match(doc, /--hb-cyan-bright:#abcdef/)
  assert.match(doc, /svg\.mkvi-diagram \.mkvi-decision\s*\{[^}]+stroke: var\(--hb-cyan-bright\)/)
  assert.doesNotMatch(doc, /__(?:PANEL|TEXT|MUTED|ACCENT|GOOD|BAD)__/)
  const classes = [...example.matchAll(/class="(mkvi-[\w-]+)"/g)].map(([, name]) => name)
  for (const name of new Set(classes)) assert.ok(doc.includes('.' + name), name)
})
