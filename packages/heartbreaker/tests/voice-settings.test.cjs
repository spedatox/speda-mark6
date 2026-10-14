// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const root = path.join(__dirname, '../src/renderer/src')
function compile(file, requireModule) {
  const exports = {}
  const source = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, require: requireModule })
  return exports
}
const dictionary = compile('lib/i18n/en.ts', () => ({})).default
const agent = {
  agent_id: 'test-agent', name: 'Test', default_voice: 'openai:tts-1:alloy',
  voice_id: 'openai:tts-1:nova', effective_voice: 'openai:tts-1:nova', voice_settings: { stability: 0.5 },
  languages: {
    tr: { voice_id: 'elevenlabs:eleven_v4:custom-tr', effective_voice: 'elevenlabs:eleven_v4:custom-tr',
      voice_settings: { stability: 0.2 }, effective_voice_settings: { stability: 0.2 } },
    en: { voice_id: 'elevenlabs:eleven_v4:custom-en', effective_voice: 'elevenlabs:eleven_v4:custom-en',
      voice_settings: { stability: 0.8 }, effective_voice_settings: { stability: 0.8 } },
  },
}
// Hook-driven component harness: rerenders real handlers without a browser,
// while keeping HTTP calls and settings storage outside the test.
function harness(props = {}, api = {}) {
  const states = [], effects = [], updates = []
  let cursor = 0, mounted = false
  const jsx = (type, props) => ({ type, props })
  const modules = {
    react: {
      useState(initial) {
        const index = cursor++
        if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial
        return [states[index], next => { states[index] = typeof next === 'function' ? next(states[index]) : next }]
      },
      useEffect(effect) { if (!mounted) effects.push(effect) },
    },
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
    '../lib/api': { fetchVoiceAgents: async () => [agent], fetchVoiceOptions: async () => [], ...api },
    '../lib/i18n': { useT: () => dictionary },
    '../store/settings': { useSettings: () => ({ settings: { locale: 'tr', voiceModel: 'openai:tts-1:echo' }, update: patch => updates.push(patch) }) },
    './settingsUI': Object.fromEntries(['PillBtn', 'SettingsField', 'SettingsSection', 'SliderField', 'Switch'].map(name => [name, name])),
    './Skeleton': { SkeletonList: 'SkeletonList' },
  }
  const component = compile('components/VoicesTab.tsx', name => modules[name])
  const editorProps = { agent, language: 'tr', voiceOptions: [], onSave: async () => {}, onClearAll: async () => {}, onCancel() {}, ...props }
  return {
    render(editor = true) {
      cursor = 0
      const tree = editor ? component.VoiceEditor(editorProps) : component.default({ config: {} })
      mounted = true
      while (effects.length) effects.shift()()
      return tree
    }, updates,
  }
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return []
  if (Array.isArray(tree)) return tree.flatMap(nodes)
  return [tree, ...nodes(tree.props?.children)]
}
function find(tree, type, predicate = () => true) {
  const result = nodes(tree).find(node => node.type === type && predicate(node.props))
  assert.ok(result, `Missing ${type}`)
  return result.props
}
const tick = () => new Promise(resolve => setImmediate(resolve))

test('Turkish and English editors use separate saved pins and tuning', () => {
  for (const language of ['tr', 'en']) {
    const tree = harness({ language }).render()
    const saved = agent.languages[language]
    assert.equal(find(tree, 'select').value, saved.voice_id)
    assert.equal(find(tree, 'option', p => p.value === saved.voice_id).children, saved.voice_id)
    assert.equal(find(tree, 'input').value, `custom-${language}`)
    assert.equal(find(tree, 'SliderField', p => p.label === dictionary.settingsVoices.stability).value, saved.voice_settings.stability)
  }
})

test('pinning a custom voice sends only its language and leaves untouched tuning alone', async () => {
  let saved
  const h = harness({ onSave: async patch => { saved = patch } })
  find(h.render(), 'input').onChange({ target: { value: 'new-tr' } })
  find(h.render(), 'PillBtn', p => p.tone === 'accent').onClick()
  await tick()
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), { language: 'tr', voice_id: 'elevenlabs:eleven_v4:new-tr' })
})

test('selecting the fallback clears a custom pin, including its manual ID', async () => {
  let saved
  const h = harness({ onSave: async patch => { saved = patch } })
  find(h.render(), 'select').onChange({ target: { value: '' } })
  const tree = h.render()
  assert.equal(find(tree, 'select').value, '')
  assert.equal(find(tree, 'input').value, '')
  find(tree, 'PillBtn', p => p.tone === 'accent').onClick()
  await tick()
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), { language: 'tr', voice_id: null })
})

test('editing one tuning control writes only that control for English', async () => {
  let saved
  const h = harness({ language: 'en', onSave: async patch => { saved = patch } })
  find(h.render(), 'SliderField', p => p.label === dictionary.settingsVoices.stability).onChange(0.3)
  find(h.render(), 'PillBtn', p => p.tone === 'accent').onClick()
  await tick()
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), { language: 'en', voice_id: 'elevenlabs:eleven_v4:custom-en', stability: 0.3 })
})

test('failed saves and resets retain the editor and show an error', async () => {
  for (const tone of ['accent', 'danger']) {
    const fail = async () => { throw new Error('offline') }
    const h = harness({ onSave: fail, onClearAll: fail })
    find(h.render(), 'input').onChange({ target: { value: 'keep-me' } })
    find(h.render(), 'PillBtn', p => p.tone === tone).onClick()
    assert.equal(find(h.render(), 'fieldset').disabled, true)
    await tick()
    const tree = h.render()
    assert.equal(find(tree, 'p', p => p.role === 'alert').children, dictionary.settingsVoices.saveFailed)
    assert.equal(find(tree, 'input').value, 'keep-me')
    assert.equal(find(tree, 'fieldset').disabled, false)
  }
})

test('language switch shows matching effective voices and can clear the chat override', async () => {
  const h = harness()
  h.render(false)
  await tick()
  let tree = h.render(false)
  assert.ok(nodes(tree).some(n => [].concat(n.props?.children).includes('elevenlabs:eleven_v4:custom-tr')))
  find(tree, 'select').onChange({ target: { value: 'en' } })
  tree = h.render(false)
  assert.ok(nodes(tree).some(n => [].concat(n.props?.children).includes('elevenlabs:eleven_v4:custom-en')))
  find(tree, 'PillBtn', p => p.children === dictionary.settingsVoices.useAgentVoices).onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(h.updates)), [{ voiceModel: '' }])
})
