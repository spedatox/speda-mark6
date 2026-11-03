import { authHeaders } from './api'
import type { AppConfig, RoomInfo, RoomEvent } from './types'
import { findAgentMentions } from './agentMentions'

export const canonicalRoomAgent = (id: string) => id === 'nightcrawler' ? 'friday' : id

async function response<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.message || body?.detail || `Room request failed (${res.status})`)
  }
  return res.json()
}

export async function fetchSessionRoom(config: AppConfig, sessionId: number, signal?: AbortSignal): Promise<RoomInfo | null> {
  return response(await fetch(`${config.apiBase}/sessions/${sessionId}/room?agent_id=${encodeURIComponent(config.agentId)}`,
    { headers: authHeaders(config), signal }))
}

export async function fetchRoomEvents(config: AppConfig, roomId: string, after: number, signal?: AbortSignal):
  Promise<{ room: RoomInfo; events: RoomEvent[]; next_cursor: number }> {
  return response(await fetch(`${config.apiBase}/coordination/rooms/${encodeURIComponent(roomId)}/events?after=${after}`,
    { headers: authHeaders(config), signal }))
}

export async function changeRoomMember(config: AppConfig, room: RoomInfo,
  action: 'invite' | 'dismiss' | 'handoff', agentId: string): Promise<RoomInfo> {
  return response(await fetch(`${config.apiBase}/coordination/rooms/${encodeURIComponent(room.id)}/members`, {
    method: 'POST', headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ expected_version: room.version, action, agent_id: agentId }),
  }))
}

/** Explicit mentions choose one speaker; ordinary messages follow the current lead. */
export function addressedAgent(text: string, members?: string[]): string | undefined {
  const mentions = findAgentMentions(text).map(mention => mention.agentId)
  const unique = [...new Set(mentions)]
  if (unique.length > 1) throw new Error('Address one participant per message. The lead can coordinate the others.')
  const target = unique[0]
  if (target && members && !members.includes(target)) throw new Error('Summon this participant before addressing them.')
  return target
}
