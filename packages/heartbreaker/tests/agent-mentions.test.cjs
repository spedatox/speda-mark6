const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const root = path.join(__dirname, '../src/renderer/src')
const cache = new Map()
function load(file) {
  const resolved = path.resolve(root, file)
  if (cache.has(resolved)) return cache.get(resolved)
  const exports = {}; cache.set(resolved, exports)
  const source = ts.transpileModule(fs.readFileSync(resolved, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const localRequire = name => {
    if (!name.startsWith('.')) return require(name)
    const base = path.resolve(path.dirname(resolved), name)
    return load(path.relative(root, fs.existsSync(base + '.tsx') ? base + '.tsx' : base + '.ts'))
  }
  new Function('exports', 'require', source)(exports, localRequire)
  return exports
}
const { mentionQueryAt, mentionChoices, findAgentMentions } = load('lib/agentMentions.ts')
const MentionText = load('components/AgentMentionText.tsx').default

test('picker opens at a caret mention and replaces the entire queried word', () => {
  assert.deepEqual(mentionQueryAt('@', 1), { start: 0, end: 1, query: '' })
  assert.deepEqual(mentionQueryAt('Please @Ultron explain', 10), { start: 7, end: 14, query: 'ul' })
  assert.deepEqual(mentionQueryAt('Line one\n@Fr', 12), { start: 9, end: 12, query: 'fr' })
  assert.equal(mentionQueryAt('mail@example.com', 7), null)
  assert.equal(mentionQueryAt('@Ultron', 0, 7), null)
  assert.equal(mentionQueryAt('@Ultron ', 8), null)
})

test('suggestions use names, aliases and live room membership without duplicate Friday entries', () => {
  assert.equal(mentionChoices('').length, 8)
  assert.deepEqual(mentionChoices('fr'), ['friday'])
  assert.deepEqual(mentionChoices('night'), ['friday'])
  assert.deepEqual(mentionChoices('', ['speda', 'nightcrawler', 'ultron']), ['speda', 'friday', 'ultron'])
  assert.deepEqual(mentionChoices('op', ['speda', 'ultron']), [])
})

test('recognition retains exact text and positions for routing, with aliases canonicalized', () => {
  assert.deepEqual(findAgentMentions('Ask @NightCrawler, then @ULTRON.'), [
    { start: 4, end: 17, text: '@NightCrawler', agentId: 'friday' },
    { start: 24, end: 31, text: '@ULTRON', agentId: 'ultron' },
  ])
  assert.deepEqual(findAgentMentions('me@ultron.dev @UltronExtra @unknown'), [])
})

test('selected mentions render bold names and existing logo artwork while escaping authored HTML', () => {
  const markup = renderToStaticMarkup(React.createElement(MentionText, { text: '<img src=x> @Optimus help' }))
  assert.match(markup, /&lt;img src=x&gt;/)
  assert.doesNotMatch(markup, /<img/)
  assert.match(markup, /contenteditable="false"/)
  assert.match(markup, /data-mention-text="@Optimus"/)
  assert.match(markup, /<svg[^>]*[\s\S]*<path/)
  assert.match(markup, /<strong>Optimus<\/strong>/)
  assert.match(markup, /fill-rule="evenodd"/)
})
