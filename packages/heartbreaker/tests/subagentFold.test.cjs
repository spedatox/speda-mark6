const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const source = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/subagentFold.ts'), 'utf8')
const exportsObject = {}
new Function('exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(exportsObject)
const { foldLegionEvent: fold, workerLabel } = exportsObject
const event = (sequence, phase, fields = {}) => ({ id: 'execution', execution_id: 'execution', owner_agent: 'optimus', role: 'autobot', source: 'forge', execution_type: 'forge', event_id: `event-${sequence}`, sequence, phase, ...fields })

test('replayed durable text and tool frames remain idempotent and immutable', () => {
  let run = fold(null, event(1, 'started', { agent: 'autobot', label: 'Implement feature' }))
  const first = fold(run, event(2, 'text', { text: 'First' }))
  run = fold(first, event(3, 'text', { text: ' second' }))
  assert.equal(first.steps[0].text, 'First')
  assert.equal(run.steps[0].text, 'First second')
  assert.equal(fold(run, event(2, 'text', { text: 'First' })), run)
  run = fold(run, event(4, 'tool', { tool: 'read_file', tool_call_id: 'call-1', input: { path: 'a' } }))
  run = fold(run, event(5, 'tool_result', { tool_call_id: 'call-1', result: 'evidence' }))
  run = fold(run, event(6, 'tool', { tool: 'read_file', tool_call_id: 'call-1' }))
  assert.equal(run.steps.filter(step => step.kind === 'tool').length, 1)
  assert.equal(run.steps[1].result, 'evidence')
  const done = fold(run, event(7, 'finished', { status: 'succeeded', ok: true, report: 'Verified' }))
  assert.equal(fold(done, event(1, 'started')), done)
  assert.equal(done.running, false)
  assert.equal(done.executionId, 'execution')
})

test('event IDs also deduplicate unsequenced frames and unknown outcomes stay incomplete', () => {
  let run = fold(null, { id: 'legacy', event_id: 'one', phase: 'text', text: 'once' })
  assert.equal(fold(run, { id: 'legacy', event_id: 'one', phase: 'text', text: 'once' }), run)
  run = fold(run, { id: 'legacy', event_id: 'two', phase: 'finished', status: 'reconciling' })
  assert.equal(run.running, false)
  assert.equal(run.ok, false)
})

test('corps labels retain the real supervisor rather than impersonating a persona', () => {
  const run = fold(null, event(1, 'started', { agent: 'autobot' }))
  assert.equal(workerLabel(run), 'Autobot · supervised by Optimus')
  assert.equal(workerLabel({ ...run, workerRole: 'decepticon', ownerAgent: 'scourge' }), 'Decepticon · supervised by Scourge')
  assert.equal(workerLabel({ ...run, workerRole: 'research', source: 'legion', executionType: 'legion', ownerAgent: 'friday' }), 'Legion · research · supervised by Friday')
})


test('task controls use only execution routes and stream reconnect carries its cursor', async () => {
  const apiSource = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/api.ts'), 'utf8')
  const calls = []
  const fakeFetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url.includes('/attach?')) return new Response('data: ' + JSON.stringify({ type: 'subagent', data: { id: 'child', event_id: 'next', sequence: 32, phase: 'text', text: 'next' } }) + '\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    return new Response(JSON.stringify({ status: 'queued' }), { headers: { 'Content-Type': 'application/json' } })
  }
  const api = {}
  const transport = {}
  new Function('exports', ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/workerTransport.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(transport)
  new Function('exports', 'fetch', 'require', ts.transpileModule(apiSource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(api, fakeFetch, name => {
    assert.equal(name, './workerTransport')
    return transport
  })
  const config = { apiBase: 'https://fixture.invalid', apiKey: 'fixture-key', agentId: 'optimus' }
  await api.interruptWorker(config, 'child')
  await api.messageWorker(config, 'child', 'Review this condition', 'a'.repeat(32))
  const frames = []
  for await (const frame of api.attachWorkerExecution(config, 'child', new AbortController().signal, 31)) frames.push(frame)
  assert.equal(calls[0].url, 'https://fixture.invalid/legion/executions/child/interrupt')
  assert.equal(calls[1].url, 'https://fixture.invalid/legion/executions/child/messages')
  assert.equal(JSON.parse(calls[1].options.body).message_id, 'a'.repeat(32))
  assert.equal(calls[2].url, 'https://fixture.invalid/legion/executions/child/attach?after=31')
  assert.equal(frames[0].sequence, 32)
  assert.equal(calls.some(call => call.url.includes('/chat/cancel') || call.url.includes('/runs/')), false)
})

test('lost executor remains unknown and cannot appear as running or successful', () => {
  let run = fold(null, event(1, 'started', { status: 'running' }))
  run = fold(run, { id: 'execution', execution_id: 'execution', phase: 'executor_unavailable', status: 'unknown' })
  assert.equal(run.running, false)
  assert.equal(run.status, 'unknown')
  assert.notEqual(run.ok, true)
})
