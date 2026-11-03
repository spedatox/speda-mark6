const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const Renderer = require('react-test-renderer')
const { act } = Renderer
const source = path.join(__dirname, '../src/renderer/src')
function compile(file, modules = {}) {
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(path.join(source, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(code, { exports, structuredClone, require: name => modules[name] ?? require(name) })
  return exports
}
const fields = compile('lib/policyFields.ts')
const PolicyField = compile('components/PolicyField.tsx', { '../lib/policyFields': fields, './settingsUI': compile('components/settingsUI.tsx') }).default
const schema = { type: 'object', properties: {
  paused: { type: 'boolean' },
  run: { $ref: '#/$defs/Run' },
  prices: { type: 'object', additionalProperties: { type: 'object', properties: { microdollars_per_token: { type: 'number', exclusiveMinimum: 0 } } } },
  lifetime_tokens: { anyOf: [{ type: 'integer', minimum: 0 }, { type: 'null' }] },
  fallbacks: { type: 'array', maxItems: 3, items: { type: 'string' } },
}, $defs: { Run: { type: 'object', required: ['tokens', 'calls'], properties: { tokens: { type: 'integer', minimum: 0, maximum: 20000000 }, calls: { type: 'integer', minimum: 0 } } } } }
const base = { id: 'coordination', label: 'Run limits', revision: 1, schema, value: { paused: false, run: { tokens: 200000, calls: 16 }, prices: { 'existing:model': { microdollars_per_token: 1 } }, lifetime_tokens: null, fallbacks: ['first', 'second'], retained: 'keep me' } }
const copy = value => JSON.parse(JSON.stringify(value))
async function mount(locale = 'en') {
  let policies = [copy(base)], failure = false, loadFailure = false
  const saves = []
  const component = compile('components/ConfigPolicies.tsx', {
    '../lib/policyFields': fields, './PolicyField': { default: PolicyField }, './settingsUI': compile('components/settingsUI.tsx'),
    '../store/settings': { useSettings: () => ({ settings: { locale } }) },
    '../lib/api': {
      getConfigPolicies: async () => { if (loadFailure) throw Error('offline'); return copy(policies) },
      saveConfigPolicy: async (_, policy, value) => {
        saves.push({ policy: copy(policy), value: copy(value) })
        if (failure) throw Error('stale revision')
        policies = [{ ...policy, revision: policy.revision + 1, value: copy(value) }]
        return copy(policies)
      },
    },
  }).default
  let tree
  await act(async () => { tree = Renderer.create(React.createElement(component, { config: { apiBase: 'fixture', apiKey: 'fixture' } })) })
  return { tree, saves, fail() { failure = true }, offline() { loadFailure = true }, remoteChange() { policies[0].revision++ },
    async input(id, value) { await act(async () => { tree.root.findByProps({ id }).props.onChange({ target: { value } }) }) },
    async button(text) { const button = tree.root.findAllByType('button').find(node => node.children.join('') === text && !node.props.disabled); assert.ok(button, text); await act(async () => { await button.props.onClick() }) },
  }
}
test('number edit saves typed values and retains untouched nested settings', async () => {
  const h = await mount()
  assert.equal(h.tree.root.findAllByType('textarea').length, 0)
  assert.equal(h.tree.root.findAllByType('pre').length, 0)
  await h.input('policy-coordination.run.tokens', '2000000')
  await h.button('Save settings')
  assert.equal(h.saves[0].value.run.tokens, 2000000)
  assert.equal(typeof h.saves[0].value.run.tokens, 'number')
  assert.deepEqual(h.saves[0].value, { ...base.value, run: { ...base.value.run, tokens: 2000000 } })
  assert.equal(h.tree.root.findByProps({ role: 'status' }).children.join(''), 'Saved')
  h.tree.unmount()
})
test('blank, fractional and out-of-range limits cannot save', async () => {
  const h = await mount()
  for (const value of ['', '1.5', '-1', '20000001']) {
    await h.input('policy-coordination.run.tokens', value)
    await h.button('Save settings')
    assert.equal(h.saves.length, 0)
    assert.ok(h.tree.root.findAllByProps({ role: 'alert' }).length)
  }
  h.tree.unmount()
})
test('failed save retains edits; reload detects stale drafts and discard adopts latest revision', async () => {
  const h = await mount()
  await h.input('policy-coordination.run.tokens', '2000000')
  h.fail(); await h.button('Save settings')
  assert.equal(h.tree.root.findByProps({ id: 'policy-coordination.run.tokens' }).props.value, '2000000')
  h.remoteChange(); await h.button('Reload settings (keep drafts)')
  const save = h.tree.root.findAllByType('button').find(n => n.children.join('') === 'Save settings')
  assert.equal(save.props.disabled, true)
  await h.button('Discard changes')
  assert.equal(h.tree.root.findByProps({ id: 'policy-coordination.run.tokens' }).props.value, '200000')
  h.tree.unmount()
})
test('nullable values, union types, maps, ordered lists and enum constraints stay typed', () => {
  assert.equal(fields.validatePolicy(schema, schema, base.value).length, 0)
  assert.ok(fields.validatePolicy(schema.properties.lifetime_tokens, schema, '').length)
  assert.ok(fields.validatePolicy(schema.properties.lifetime_tokens, schema, -1).length)
  assert.equal(fields.validatePolicy(schema.properties.lifetime_tokens, schema, null).length, 0)
  assert.ok(fields.validatePolicy({ enum: ['off', 'effort'], type: 'string' }, schema, 'invalid').length)
  assert.ok(fields.validatePolicy(schema, schema, { ...base.value, prices: { model: { microdollars_per_token: 0 } } }).length)
  assert.deepEqual(copy(fields.defaultValue({ $ref: '#/$defs/Run' }, schema)), { tokens: 0, calls: 0 })
})
test('map entries, switches, nullable controls and ordered lists save through their actual handlers', async () => {
  const h = await mount()
  await act(async () => h.tree.root.findByProps({ role: 'switch' }).props.onClick())
  await act(async () => h.tree.root.findByProps({ type: 'checkbox' }).props.onChange({ target: { checked: false } }))
  await h.input('policy-coordination.lifetime_tokens', '4000000')
  const name = h.tree.root.findByProps({ 'aria-label': 'Model prices new entry name' })
  await act(async () => name.props.onChange({ target: { value: 'new:model' } }))
  await h.button('Add entry')
  await h.input('policy-coordination.prices.new:model.microdollars_per_token', '0.5')
  await h.button('Move up')
  await h.button('Save settings')
  assert.equal(h.saves[0].value.paused, true)
  assert.equal(h.saves[0].value.lifetime_tokens, 4000000)
  assert.deepEqual(h.saves[0].value.prices, { 'existing:model': { microdollars_per_token: 1 }, 'new:model': { microdollars_per_token: 0.5 } })
  assert.deepEqual(h.saves[0].value.fallbacks, ['second', 'first'])
  h.tree.unmount()
})
test('Turkish uses labeled settings controls', async () => {
  const h = await mount('tr')
  await h.input('policy-coordination.run.tokens', '2000000')
  await h.button('Ayarları kaydet')
  assert.equal(h.saves[0].value.run.tokens, 2000000)
  h.tree.unmount()
})
if (process.env.SPEDA_POLICY_FIXTURES) test('all real backend policy schemas render and validate without JSON editors', async () => {
  const policies = JSON.parse(fs.readFileSync(process.env.SPEDA_POLICY_FIXTURES, 'utf8'))
  for (const policy of policies) {
    assert.deepEqual(copy(fields.validatePolicy(policy.schema, policy.schema, policy.value)), [], policy.id)
    let tree
    await act(async () => { tree = Renderer.create(React.createElement(PolicyField, { schema: policy.schema, root: policy.schema, value: policy.value, path: policy.id, label: policy.label, tr: false, onChange() {} })) })
    assert.equal(tree.root.findAllByType('textarea').length, 0)
    assert.equal(tree.root.findAllByType('pre').length, 0)
    tree.unmount()
  }
})
