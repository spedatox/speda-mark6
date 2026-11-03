const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.join(__dirname, '../src/renderer/src/lib')
const source = ts.transpileModule(fs.readFileSync(path.join(root, 'api.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

test('canvas is explicit per turn and independent of spoken output', async () => {
  const exports = {}, requests = []
  vm.runInNewContext(source, { exports, AbortController, TextDecoder, navigator: { userAgent: 'Electron', language: 'tr-TR' },
    fetch: async (_url, options) => { requests.push(JSON.parse(options.body)); return { ok: true, body: { getReader: () => ({ read: async () => ({ done: true }), releaseLock() {} }) } } },
  })
  const config = { apiBase: 'https://fixture.test', apiKey: 'test', agentId: 'ultron' }
  for (const opts of [{ voice: true, canvas: true, canvasMaxPanels: 24 }, { voice: true, canvas: false }, { canvas: true, canvasMaxPanels: 8 }, {}]) {
    for await (const _ of exports.streamChat('Teach me', 1, config, new AbortController().signal, opts)) {}
  }
  assert.deepEqual(requests.map(r => [r.client_context.voice, r.client_context.canvas, r.client_context.canvas_max_panels]), [
    [true, true, 24], [true, false, undefined], [false, true, 8], [false, false, undefined],
  ])
  assert.ok(requests.every(r => r.client_context.platform === 'desktop'))
})
