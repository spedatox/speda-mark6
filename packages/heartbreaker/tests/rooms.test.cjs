const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const sourceRoot = path.join(__dirname, '../src/renderer/src')
function loader(overrides = {}) {
  const cache = new Map()
  return function load(file) {
    const resolved = path.resolve(sourceRoot, file)
    if (cache.has(resolved)) return cache.get(resolved)
    const exports = {}; cache.set(resolved, exports)
    const source = ts.transpileModule(fs.readFileSync(resolved, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText
    const localRequire = name => name.startsWith('.') ? load(path.relative(sourceRoot, path.resolve(path.dirname(resolved), name + '.ts'))) : require(name)
    new Function('exports', 'require', ...Object.keys(overrides), source)(exports, localRequire, ...Object.values(overrides))
    return exports
  }
}
const load = loader()
const { chatReducer: reduce, initialState } = load('store/chat.ts')
const { historyMessageCount } = load('lib/roomFold.ts')
const { addressedAgent } = load('lib/rooms.ts')
const room = { id: 'room', title: 'Study', lead: 'speda', members: ['speda', 'ultron'], version: 2, sequence: 0, active_run: 'run' }
const frame = (sequence, kind, step, speaker, payload = {}) => ({ schema_version: 1, room_id: 'room', sequence, run_id: 'run', step_id: step, speaker, kind, payload, created_at: '2026-10-28T09:00:00Z' })
const apply = (state, event) => reduce(state, { type: 'ROOM_EVENT', payload: { event, sessionId: 1 } })

test('Android and Heartbreaker show the same peer exchange without an owner relay', () => {
  const trace = JSON.parse(fs.readFileSync(path.join(__dirname, '../../speda-go/app/src/test/resources/fixtures/room_v1.json'), 'utf8'))
  const state = trace.reduce(apply, begin([{ id: 'local-user', role: 'user', content: 'Plan the week', tools: [], isStreaming: false, isError: false }]))
  const speakers = state.messages.filter(message => message.speaker)
  assert.deepEqual(speakers.map(message => [message.speaker, message.content]), [
    ['speda', 'Bringing Ultron in.'], ['ultron', '@SPEDA, when is the midterm?'],
    ['speda', 'The midterm is Friday.'], ['ultron', 'Then review on Thursday.'],
  ])
  assert.equal(speakers[0].tools[0].name, 'collaborate')
  assert.equal(speakers[1].roomSequence, 9)
  assert.equal(speakers[3].roomSequence, 14)
  assert.equal(historyMessageCount(state.messages), 5)
  assert.equal(state.messages.filter(message => message.roomNotice).length, 1)
  assert.equal(state.messages.filter(message => message.role === 'user').length, 1)
})

test('older history pages deduplicate sessions and reject another agent or server', () => {
  const config = { agentId: 'speda', apiBase: 'http://localhost:5384', apiKey: 'test' }
  const state = { ...initialState, config, sessions: [{ id: 8, title: 'Current' }] }
  const page = [{ id: 8, title: 'Stale copy' }, { id: 3, title: 'Older' }]
  const append = cfg => reduce(state, { type: 'APPEND_SESSIONS', payload: { sessions: page, config: cfg } })
  assert.deepEqual(append(config).sessions, [{ id: 8, title: 'Current' }, { id: 3, title: 'Older' }])
  assert.equal(append({ ...config, agentId: 'sentinel' }), state)
  assert.equal(append({ ...config, apiBase: 'https://other.invalid' }), state)
  assert.equal(append({ ...config, apiKey: 'another-owner' }), state)
})
function begin(messages = []) {
  let state = { ...initialState, activeSessionId: 1, messages, config: { agentId: 'speda', apiBase: 'https://fixture.invalid', apiKey: 'test' } }
  state = reduce(state, { type: 'ADD_ASSISTANT_MESSAGE', payload: { id: 'placeholder', role: 'assistant', content: '', tools: [], isStreaming: true, isError: false, sessionId: 1 } })
  return reduce(state, { type: 'ROOM_STREAM_START', payload: { room, runId: 'run', placeholderId: 'placeholder', sessionId: 1 } })
}

test('public speakers keep separate text, tools and canonical reply sequences', () => {
  let state = begin()
  state = apply(state, frame(1, 'speaker_started', 'speda-step', 'speda', { mode: 'owner' }))
  state = apply(state, frame(2, 'delta', 'speda-step', 'speda', { text: 'Bringing Ultron in.' }))
  state = apply(state, frame(3, 'tool', 'speda-step', 'speda', { data: { id: 'call', name: 'collaborate' } }))
  state = apply(state, frame(4, 'membership', 'speda-step', 'speda', { action: 'invite', agent_id: 'ultron' }))
  state = apply(state, frame(5, 'message', 'speda-step', 'speda', { text: 'Bringing Ultron in.', message_id: 7 }))
  state = apply(state, frame(6, 'speaker_started', 'ultron-step', 'ultron', { mode: 'invite' }))
  state = apply(state, frame(7, 'delta', 'ultron-step', 'ultron', { text: 'My own answer.' }))
  state = apply(state, frame(8, 'message', 'ultron-step', 'ultron', { text: 'My own answer.', message_id: 8 }))
  const speakers = state.messages.filter(message => !message.roomNotice)
  assert.deepEqual(speakers.map(message => [message.speaker, message.content]), [['speda', 'Bringing Ultron in.'], ['ultron', 'My own answer.']])
  assert.equal(speakers[0].tools[0].name, 'collaborate')
  assert.equal(speakers[1].tools.length, 0)
  assert.equal(speakers[1].roomSequence, 8)
  assert.equal(state.messages.filter(message => message.roomNotice).length, 1)
  assert.equal(state.isStreaming, true, 'a contribution is not the end of the round')
  assert.equal(historyMessageCount(state.messages), 2)
})

test('duplicate deltas and a full reconnect replay produce one answer', () => {
  const frames = [frame(1, 'speaker_started', 'step', 'speda', { mode: 'owner' }), frame(2, 'delta', 'step', 'speda', { text: 'Once' }), frame(3, 'message', 'step', 'speda', { text: 'Once', message_id: 1 })]
  let state = begin()
  for (const event of frames) state = apply(state, event)
  assert.equal(apply(state, frames[1]), state)
  state = reduce(state, { type: 'ROOM_STREAM_START', payload: { room, runId: 'run', placeholderId: 'placeholder', sessionId: 1 } })
  for (const event of frames) state = apply(state, event)
  assert.equal(state.messages.length, 1)
  assert.equal(state.messages[0].content, 'Once')
})

test('history and attach share step identity and remove reconnect placeholders', () => {
  const saved = { id: '42', coordinationStepId: 'step', speaker: 'ultron', role: 'assistant', content: 'Saved', tools: [], isStreaming: false, isError: false }
  let state = begin([saved])
  state = apply(state, frame(1, 'speaker_started', 'step', 'ultron', { mode: 'address' }))
  state = apply(state, frame(2, 'delta', 'step', 'ultron', { text: 'Live' }))
  state = reduce(state, { type: 'SELECT_SESSION', payload: { sessionId: 1, messages: [saved] } })
  assert.equal(state.messages.length, 1)
  assert.equal(state.messages[0].content, 'Live')
  assert.equal(state.messages[0].sessionId, 1)
  assert.equal(state.isStreaming, true)
  const switched = reduce(state, { type: 'NEW_CHAT' })
  assert.equal(apply(switched, frame(3, 'delta', 'step', 'ultron', { text: 'Wrong chat' })), switched)
})

test('mentions resolve Friday consistently and reject ambiguous or absent recipients', () => {
  assert.equal(addressedAgent('Ordinary message', ['speda']), undefined)
  assert.equal(addressedAgent('@Friday check this', ['speda', 'friday']), 'friday')
  assert.equal(addressedAgent('@Nightcrawler check this', ['speda', 'friday']), 'friday')
  assert.equal(addressedAgent('Please @Ultron explain', ['speda', 'ultron']), 'ultron')
  assert.throws(() => addressedAgent('@Ultron @Optimus', undefined), /one participant/)
  assert.throws(() => addressedAgent('@Ultron', ['speda']), /Summon/)
})

test('restoring the regular room never copies House Party messages or live flags', () => {
  const saved = { ...initialState, room, activeSessionId: 1, isStreaming: true,
    messages: [{ id: 'one', content: 'Regular room', role: 'assistant', tools: [], isStreaming: true, isError: false }] }
  const restored = reduce({ ...initialState, activeSessionId: 2 }, { type: 'RESTORE_CHAT', payload: saved })
  assert.equal(restored.activeSessionId, 1)
  assert.equal(restored.room.id, 'room')
  assert.equal(restored.messages[0].content, 'Regular room')
  assert.equal(restored.isStreaming, false)
  assert.equal(restored.messages[0].isStreaming, false)
})

test('House Party state and stand-down failures remain explicit', async () => {
  const api = loader({ fetch: async () => new Response('{}', { status: 503 }) })('lib/api.ts')
  const config = { apiBase: 'https://fixture.invalid', apiKey: 'test', agentId: 'warroom' }
  await assert.rejects(api.readHouseParty(config), /unavailable/)
  await assert.rejects(api.standDownHouseParty(config), /not confirmed/)
})

test('late membership polling stays in server order and owner receipts retain uploaded images', () => {
  const user = { id: 'optimistic', role: 'user', content: 'Hello', tools: [], images: ['data:image/png;base64,fixture'], isStreaming: false, isError: false }
  let state = begin([user])
  state = apply(state, frame(2, 'user_message', null, 'owner', { text: 'Hello', message_id: 17 }))
  state = apply(state, frame(3, 'speaker_started', 'step', 'speda', { mode: 'owner' }))
  state = apply(state, frame(4, 'message', 'step', 'speda', { text: 'Welcome', message_id: 18 }))
  state = reduce(state, { type: 'ROOM_MEMBERSHIP', payload: { sessionId: 1,
    event: frame(1, 'membership', null, 'owner', { action: 'invite', agent_id: 'ultron' }) } })
  assert.equal(state.messages[0].roomNotice.agent_id, 'ultron')
  assert.equal(state.messages[1].role, 'user')
  assert.deepEqual(state.messages[1].images, user.images)
  assert.equal(state.messages[1].serverMessageId, 17)
  assert.equal(state.messages.length, 3)
})

test('confirmed joins update the roster immediately and stale membership snapshots cannot roll it back', () => {
  let state = begin()
  const joined = { ...room, version: 3, sequence: 4, members: ['speda', 'ultron', 'optimus'] }
  state = apply(state, frame(4, 'membership', 'step', 'speda', { action: 'invite', agent_id: 'optimus', room: joined }))
  assert.deepEqual(state.room.members, joined.members)
  state = reduce(state, { type: 'ROOM_MEMBERSHIP', payload: { sessionId: 1,
    event: frame(2, 'membership', null, 'owner', { action: 'invite', agent_id: 'ultron', room }) } })
  assert.equal(state.room.version, 3)
  assert.deepEqual(state.room.members, joined.members)
})

test('a late acknowledgment cannot adopt a departed new chat or another server', () => {
  let state = begin()
  const oldEpoch = state.viewEpoch
  state = reduce(state, { type: 'NEW_CHAT' })
  assert.equal(reduce(state, { type: 'TAG_MESSAGE_SESSION', payload: {
    id: 'placeholder', sessionId: 99, viewEpoch: oldEpoch,
  } }), state)
  const config = { ...state.config, apiBase: 'https://another-fixture.invalid' }
  state = reduce(state, { type: 'SET_CONFIG', payload: config })
  assert.equal(state.activeSessionId, null)
  assert.equal(state.room, null)
  assert.equal(state.messages.length, 0)
  assert.equal(reduce(state, { type: 'SELECT_SESSION', payload: {
    sessionId: 1, messages: [], expectedViewEpoch: oldEpoch,
  } }), state)
})
