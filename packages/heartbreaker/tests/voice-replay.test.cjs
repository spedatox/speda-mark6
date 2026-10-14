// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function harness() {
  const contexts = [], sockets = [], requests = [], timers = new Map()
  let timerId = 0
  class Buffer {
    constructor(channels, length, rate) {
      this.numberOfChannels = channels
      this.length = length
      this.duration = length / rate
      this.data = Array.from({ length: channels }, () => new Float32Array(length))
    }
    getChannelData(i) { return this.data[i] }
  }
  class Context {
    constructor() { this.currentTime = 0; this.state = 'running'; this.sources = []; this.destination = {}; contexts.push(this) }
    createAnalyser() { return { fftSize: 1024, frequencyBinCount: 512, connect() {} } }
    createGain() { return { connect() {} } }
    createBuffer(channels, length, rate) { return new Buffer(channels, length, rate) }
    createBufferSource() {
      const source = { connect() {}, start(time = 0) { this.time = time; queueMicrotask(() => this.onended?.()) }, stop() { this.stopped = true; this.onended?.() } }
      this.sources.push(source)
      return source
    }
    decodeAudioData(raw, done) { queueMicrotask(() => done(new Buffer(1, 24, 24000))) }
    async resume() { this.state = 'running' }
    async close() { this.state = 'closed' }
  }
  class Socket {
    static OPEN = 1
    constructor() { this.readyState = 1; this.sent = []; sockets.push(this) }
    send(raw) { this.sent.push(JSON.parse(raw)) }
    close() { this.readyState = 3 }
    message(data) { this.onmessage?.({ data: typeof data === 'object' && !(data instanceof ArrayBuffer) ? JSON.stringify(data) : data }) }
  }
  const exports = {}
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/voice.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  vm.runInNewContext(source, {
    exports, require: () => ({ authHeaders: () => ({}) }),
    window: { AudioContext: Context, setTimeout: callback => { timers.set(++timerId, callback); return timerId }, clearTimeout: id => timers.delete(id) },
    WebSocket: Socket, ArrayBuffer, Uint8Array, Float32Array, DataView, AbortController,
    fetch: async (_url, options) => { requests.push(JSON.parse(options.body)); return { ok: true, arrayBuffer: async () => new ArrayBuffer(2) } },
  })
  return { VoiceSession: exports.VoiceSession, contexts, sockets, requests, timers }
}

const config = { apiBase: 'https://speda.test', apiKey: 'test', agentId: 'speda' }
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)) }

test('stream replay preserves PCM samples across odd-byte boundaries without another request', async () => {
  const h = harness()
  let recording
  const live = new h.VoiceSession(config)
  live.onRecording = audio => { recording = audio }
  h.sockets[0].message({ type: 'ready', sample_rate: 24000 })
  live.feed('Hello there.')
  live.finish()
  h.sockets[0].message(Uint8Array.from([0, 0, 0]).buffer)
  h.sockets[0].message(Uint8Array.from([64, 0, 128]).buffer)
  h.sockets[0].message({ type: 'done' })
  assert.ok(recording)
  assert.equal(recording.text, 'Hello there.')
  assert.deepEqual(Array.from(recording.buffers).flatMap(buffer => Array.from(buffer.getChannelData(0))), [0, 0.5, -1])
  live.stop()
  const replay = new h.VoiceSession(config, {}, recording)
  await replay.resume()
  assert.equal(h.sockets.length, 1)
  assert.equal(h.requests.length, 0)
  assert.deepEqual(h.contexts[1].sources.map(source => source.buffer), Array.from(recording.buffers))
  let state
  replay.onState = next => { state = next }
  h.contexts[1].currentTime = 100
  for (const callback of Array.from(h.timers.values())) callback()
  assert.equal(state, 'idle')
  replay.stop()
  assert.equal(h.contexts[1].state, 'closed')
})

test('HTTP fallback recordings replay in sentence order and stop all scheduled clips', async () => {
  const h = harness()
  let recording
  const live = new h.VoiceSession(config)
  live.onRecording = audio => { recording = audio }
  h.sockets[0].message({ type: 'unsupported' })
  live.feed('Hello. Another sentence.')
  live.finish()
  await tick()
  assert.equal(recording.buffers.length, 2)
  assert.equal(h.requests.length, 2)
  live.stop()
  const replay = new h.VoiceSession(config, {}, recording)
  assert.equal(h.requests.length, 2)
  assert.equal(h.sockets.length, 1)
  const sources = h.contexts[1].sources
  assert.equal(sources[0].buffer, recording.buffers[0])
  assert.equal(sources[1].buffer, recording.buffers[1])
  assert.ok(sources[1].time >= sources[0].time + sources[0].buffer.duration)
  replay.stop()
  assert.ok(sources.every(source => source.stopped))
  assert.equal(h.timers.size, 0)
})

test('an interrupted response does not publish an incomplete recording', () => {
  const h = harness()
  let published = false
  const live = new h.VoiceSession(config)
  live.onRecording = () => { published = true }
  h.sockets[0].message({ type: 'ready', sample_rate: 24000 })
  live.feed('Still speaking.')
  h.sockets[0].message(Uint8Array.from([0, 64]).buffer)
  live.stop()
  assert.equal(published, false)
  assert.equal(h.contexts[0].state, 'closed')
})

test('audio that finishes before the text stream ends becomes replayable on finish', async () => {
  const h = harness()
  let recording, state
  const live = new h.VoiceSession(config)
  live.onRecording = audio => { recording = audio }
  live.onState = next => { state = next }
  h.sockets[0].message({ type: 'unsupported' })
  live.feed('Already spoken.\n')
  await tick()
  assert.equal(recording, undefined)
  live.finish()
  assert.ok(recording)
  assert.equal(state, 'idle')
  live.stop()
})
