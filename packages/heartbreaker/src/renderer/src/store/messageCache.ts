// Local message cache — a per-session mirror of the transcript in localStorage.
//
// The backend is the source of truth, but two situations leave the owner staring
// at nothing: (1) the connection drops mid-turn (e.g. Orion restarts Igor) and
// the answer was never persisted server-side; (2) the app opens with no network,
// so history can't be fetched at all. This cache is the offline/failure fallback:
// we snapshot each session's messages as turns settle, and hydrate from it when
// the server can't be reached. It is never authoritative — a successful fetch
// always wins and refreshes the snapshot.

import type { ChatMessage } from '../lib/types'

const key = (agentId: string, sessionId: number): string =>
  `hb:msgs:${agentId}:${sessionId}`

export function saveMessages(agentId: string, sessionId: number, messages: ChatMessage[]): void {
  if (!agentId || sessionId == null) return
  try {
    // Drop the volatile live flags so a reload never rehydrates a "streaming"
    // bubble that will spin forever with no socket behind it.
    const clean = messages.map(m => ({ ...m, isStreaming: false, status: undefined }))
    localStorage.setItem(key(agentId, sessionId), JSON.stringify(clean))
  } catch { /* quota / serialization — the cache is best-effort */ }
}

export function loadMessages(agentId: string, sessionId: number): ChatMessage[] | null {
  if (!agentId || sessionId == null) return null
  try {
    const raw = localStorage.getItem(key(agentId, sessionId))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) && parsed.length ? (parsed as ChatMessage[]) : null
  } catch {
    return null
  }
}
