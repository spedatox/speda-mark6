import assert from 'node:assert/strict'
import { test } from 'node:test'
import { foldRoomMessages } from '../../packages/heartbreaker/src/renderer/src/lib/roomFold'
import type { ChatMessage, RoomEvent } from '../../packages/heartbreaker/src/renderer/src/lib/types'

test('waiting and resumed events preserve the original text and ignore replay', () => {
  const event = (sequence: number, kind: string, payload = {}): RoomEvent => ({
    room_id: 'room', sequence, kind, step_id: 'step', speaker: 'orion', payload,
  } as RoomEvent)
  let rows: ChatMessage[] = []
  const trace = [event(1, 'speaker_started'), event(2, 'delta', { text: 'Running the command.' }),
    event(3, 'step_waiting'), event(4, 'operation_finished', { status: 'succeeded' }),
    event(5, 'step_resumed'), event(6, 'delta', { text: ' Done.' })]
  for (const frame of trace) rows = foldRoomMessages(rows, frame, '', 1)
  assert.equal(rows[0].content, 'Running the command. Done.')
  assert.equal(rows[0].isStreaming, true)
  const once = structuredClone(rows)
  for (const frame of trace) rows = foldRoomMessages(rows, frame, '', 1)
  assert.deepEqual(rows, once)
})

test('reconnecting to a pending command reconstructs its saved status', () => {
  const rows = foldRoomMessages([], {room_id:'room', sequence:3, kind:'step_waiting', step_id:'step',
    speaker:'orion', payload:{text:'Running the command.'}} as RoomEvent, '', 1)
  assert.equal(rows[0].status, 'Command running')
  assert.equal(rows[0].isStreaming, false)
  assert.equal(rows[0].content, 'Running the command.')
})
