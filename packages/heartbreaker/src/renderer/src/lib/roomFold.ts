import type { ChatMessage, RoomEvent, RoomInfo, ToolBadge, FileMeta } from './types'

export const roomMessageId = (step: string) => `coord:${step}`

/** Sort sequenced rows without moving legacy originals or operational worker anchors. */
function orderedRoomRows(messages: ChatMessage[]): ChatMessage[] {
  const ordered = messages.filter(message => message.roomOrder != null)
    .sort((a, b) => a.roomOrder! - b.roomOrder!)
  let index = 0
  return messages.map(message => message.roomOrder != null ? ordered[index++] : message)
}

/** Count only server originals; membership notices and worker anchors are UI rows. */
export const historyMessageCount = (messages: ChatMessage[]) =>
  messages.filter(message => !message.roomNotice && !message.isWorkerAnchor &&
    (!message.id.startsWith('coord:') || message.serverMessageId != null)).length

export function membershipNotice(event: RoomEvent): ChatMessage {
  return { id: `room:${event.room_id}:${event.sequence}`, role: 'assistant', content: '', tools: [],
    isStreaming: false, isError: false, roomSequence: event.sequence, roomOrder: event.sequence, createdAt: event.created_at,
    roomNotice: event.payload as ChatMessage['roomNotice'] }
}

export function updateRoom(current: RoomInfo | null, next: RoomInfo): RoomInfo {
  if (current?.id === next.id && (current.version > next.version ||
      (current.version === next.version && current.sequence > next.sequence))) return current
  return next
}

/** Fold the server's public speaking slot. Replays restart a step, never concatenate twice. */
export function foldRoomMessages(messages: ChatMessage[], event: RoomEvent, placeholderId: string, sessionId?: number): ChatMessage[] {
  if (event.kind === 'membership') {
    const notice = membershipNotice(event)
    return messages.some(message => message.id === notice.id) ? messages : orderedRoomRows([...messages, notice])
  }
  if (event.kind === 'user_message') {
    const serverId = Number(event.payload.message_id)
    const index = messages.findIndex(message => message.role === 'user' &&
      (message.serverMessageId === serverId || message.id === String(serverId)))
    const optimistic = index >= 0 ? index : messages.map((message, at) => ({ message, at })).reverse().find(({ message }) =>
      message.role === 'user' && message.serverMessageId == null && !/^\d+$/.test(message.id) &&
      message.content === event.payload.text)?.at ?? -1
    if (event.payload.replay && optimistic >= 0) return messages
    const user: ChatMessage = { ...(optimistic >= 0 ? messages[optimistic] : {}),
      id: optimistic >= 0 ? messages[optimistic].id : String(serverId), serverMessageId: serverId,
      role: 'user', content: String(event.payload.text ?? ''), tools: [], isStreaming: false, isError: false,
      roomSequence: event.sequence, roomOrder: event.sequence, createdAt: event.created_at, sessionId }
    return orderedRoomRows(optimistic >= 0 ? messages.map((message, at) => at === optimistic ? user : message) : [...messages, user])
  }
  if (!event.step_id) return messages
  const step = event.step_id
  const index = messages.findIndex(message => message.coordinationStepId === step && !message.isWorkerAnchor)
  const existing = index >= 0 ? messages[index] : undefined
  if (event.kind === 'speaker_started') {
    if (event.payload.mode === 'consult') return messages
    const next: ChatMessage = { ...existing, id: roomMessageId(step), role: 'assistant', speaker: event.speaker,
      coordinationStepId: step, sessionId, content: '', tools: [], files: [], thinking: undefined,
      thinkingDone: false, thinkingRedacted: false, isStreaming: true, isError: false,
      errorNote: undefined, status: undefined, roomSequence: event.sequence, roomOrder: event.sequence, createdAt: event.created_at }
    // Replace a history row in place by step ID; preserve attached worker cards.
    if (index >= 0) return orderedRoomRows(messages.map((message, at) => at === index ? next : message).filter(message => message.id !== placeholderId))
    return orderedRoomRows([...messages.filter(message => message.id !== placeholderId), next])
  }
  if (!existing) return messages
  let next = existing
  switch (event.kind) {
    case 'delta': next = { ...existing, content: existing.content + String(event.payload.text ?? '') }; break
    case 'message': next = { ...existing, content: String(event.payload.text ?? ''), isStreaming: false,
      serverMessageId: Number(event.payload.message_id), roomSequence: event.sequence, status: undefined, thinkingDone: true }; break
    case 'thinking': {
      const data = event.payload.data as { text?: string; redacted?: boolean }
      next = { ...existing, thinking: (existing.thinking ?? '') + (data.text ?? ''),
        thinkingRedacted: existing.thinkingRedacted || !!data.redacted }; break
    }
    case 'tool': next = { ...existing, thinkingDone: true, tools: [...existing.tools,
      { ...(event.payload.data as ToolBadge), afterChars: existing.content.length }] }; break
    case 'tool_result': {
      const data = event.payload.data as { id: string; result: string }
      next = { ...existing, tools: existing.tools.map(tool => tool.id === data.id ? { ...tool, result: data.result } : tool) }; break
    }
    case 'file': next = { ...existing, files: [...(existing.files ?? []), event.payload.data as FileMeta] }; break
    case 'contribution_stopped': case 'contribution_skipped':
      next = { ...existing, isStreaming: false, isError: true, thinkingDone: true,
        errorNote: String(event.payload.reason ?? 'Contribution stopped'), status: undefined }; break
  }
  return next === existing ? messages : messages.map((message, at) => at === index ? next : message)
}
