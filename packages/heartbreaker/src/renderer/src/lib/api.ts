import type { AppConfig, SSEEvent, ModelInfo } from './types'

export async function* streamChat(
  message: string,
  sessionId: number | null,
  config: AppConfig,
  signal: AbortSignal,
  model?: string,
  systemPrompt?: string,
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${config.apiBase}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': config.apiKey,
    },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      ...(model ? { model } : {}),
      ...(systemPrompt ? { system_prompt: systemPrompt } : {}),
    }),
    signal,
  })

  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`)
    throw new Error(text)
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const raw = line.slice(6).trim()
      if (!raw) continue
      try {
        yield JSON.parse(raw) as SSEEvent
      } catch { /* malformed line */ }
    }
  }
}

export async function fetchMessages(
  config: AppConfig,
  sessionId: number
): Promise<import('./types').ChatMessage[]> {
  const res = await fetch(`${config.apiBase}/sessions/${sessionId}/messages`, {
    headers: { 'X-API-Key': config.apiKey },
  })
  if (!res.ok) return []
  return res.json()
}

export async function fetchSessions(
  config: AppConfig,
  limit = 500
): Promise<Array<{ id: number; title: string | null; started_at: string }>> {
  const res = await fetch(`${config.apiBase}/sessions?limit=${limit}`, {
    headers: { 'X-API-Key': config.apiKey },
  })
  if (!res.ok) return []
  return res.json()
}

export async function fetchModels(config: AppConfig): Promise<ModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/models`, {
      headers: { 'X-API-Key': config.apiKey },
    })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export async function deleteSession(config: AppConfig, sessionId: number): Promise<void> {
  try {
    await fetch(`${config.apiBase}/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: { 'X-API-Key': config.apiKey },
    })
  } catch { /* non-fatal */ }
}

export async function renameSession(
  config: AppConfig,
  sessionId: number,
  title: string
): Promise<void> {
  try {
    await fetch(`${config.apiBase}/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': config.apiKey },
      body: JSON.stringify({ title }),
    })
  } catch { /* non-fatal */ }
}

export async function importChats(
  config: AppConfig,
  file: File
): Promise<{ accepted: boolean; message: string }> {
  const form = new FormData()
  form.append('file', file)
  // NOTE: do not set Content-Type — the browser adds the multipart boundary.
  const res = await fetch(`${config.apiBase}/admin/import-chats`, {
    method: 'POST',
    headers: { 'X-API-Key': config.apiKey },
    body: form,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`)
    throw new Error(text)
  }
  return res.json()
}
