import type { AppConfig, SSEEvent, ModelInfo, ImageBlock } from './types'

/**
 * Load an image file, downscale to <=1568px on the long edge (Anthropic's
 * recommended max, keeps it well under the 5MB limit and cuts token cost),
 * and return a base64 image block ready for the API.
 */
export async function fileToImageBlock(file: File): Promise<ImageBlock> {
  const dataUrl: string = await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = reject
    r.readAsDataURL(file)
  })

  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const i = new Image()
    i.onload = () => resolve(i)
    i.onerror = reject
    i.src = dataUrl
  })

  const MAX = 1568
  let { width, height } = img
  const longest = Math.max(width, height)
  if (longest > MAX) {
    const scale = MAX / longest
    width = Math.round(width * scale)
    height = Math.round(height * scale)
  }

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  canvas.getContext('2d')!.drawImage(img, 0, 0, width, height)

  const outType = file.type === 'image/png' ? 'image/png' : 'image/jpeg'
  const out = canvas.toDataURL(outType, 0.9)        // data:image/...;base64,XXXX
  const comma = out.indexOf(',')
  const media_type = out.slice(5, out.indexOf(';')) // image/jpeg
  return { media_type, data: out.slice(comma + 1) }
}

export async function* streamChat(
  message: string,
  sessionId: number | null,
  config: AppConfig,
  signal: AbortSignal,
  model?: string,
  systemPrompt?: string,
  images?: ImageBlock[],
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
      ...(images && images.length ? { attachments: images } : {}),
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

/** Download a produced file (fetch with auth header, then save as a blob). */
export async function downloadFile(config: AppConfig, url: string, filename: string): Promise<void> {
  const res = await fetch(`${config.apiBase}${url}`, {
    headers: { 'X-API-Key': config.apiKey },
  })
  if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`)
  const blob = await res.blob()
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(objUrl), 1000)
}

export interface ConnectionInfo {
  server: string
  label: string
  connected: boolean
  active: boolean
  always_on?: boolean
  tools: number
  tokens: number
  needs: string | null
}

export async function getConnections(config: AppConfig): Promise<{ servers: ConnectionInfo[]; active_tool_tokens: number; itpm_limit: number }> {
  const res = await fetch(`${config.apiBase}/connections`, { headers: { 'X-API-Key': config.apiKey } })
  if (!res.ok) return { servers: [], active_tool_tokens: 0, itpm_limit: 30000 }
  return res.json()
}

export async function googleLoginUrl(config: AppConfig): Promise<{ auth_url?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/connections/google/login`, { headers: { 'X-API-Key': config.apiKey } })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function setConnection(config: AppConfig, server: string, active: boolean): Promise<void> {
  await fetch(`${config.apiBase}/connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': config.apiKey },
    body: JSON.stringify({ server, active }),
  })
}

/* ── Automations — SPEDA's proactive n8n watchers ─────────────────────────── */

export interface AutomationInfo {
  id: number
  n8n_workflow_id: string | null
  name: string
  kind: 'schedule' | 'web_watch' | 'rss_watch' | 'webhook' | string
  intent: string
  active: boolean
  created_at: string | null
  expires_at: string | null
  last_fired_at: string | null
  summary: string
}

export interface AutomationsStatus {
  n8n_configured: boolean
  n8n_online: boolean
  n8n_url: string
  telegram_configured: boolean
  telegram_connected: boolean
}

export async function getAutomations(config: AppConfig): Promise<AutomationInfo[]> {
  const res = await fetch(`${config.apiBase}/automations`, { headers: { 'X-API-Key': config.apiKey } })
  if (!res.ok) return []
  return (await res.json()).automations ?? []
}

export async function toggleAutomation(config: AppConfig, id: number, active: boolean): Promise<void> {
  await fetch(`${config.apiBase}/automations/${id}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': config.apiKey },
    body: JSON.stringify({ active }),
  })
}

export async function deleteAutomation(config: AppConfig, id: number): Promise<void> {
  await fetch(`${config.apiBase}/automations/${id}`, {
    method: 'DELETE',
    headers: { 'X-API-Key': config.apiKey },
  })
}

export async function getAutomationsStatus(config: AppConfig): Promise<AutomationsStatus | null> {
  try {
    const res = await fetch(`${config.apiBase}/automations/status`, { headers: { 'X-API-Key': config.apiKey } })
    if (!res.ok) return null
    return res.json()
  } catch { return null }
}

export async function telegramConnect(config: AppConfig): Promise<{ link?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/automations/telegram/connect`, {
    method: 'POST',
    headers: { 'X-API-Key': config.apiKey },
  })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function telegramStatus(config: AppConfig): Promise<{ configured: boolean; connected: boolean }> {
  const res = await fetch(`${config.apiBase}/automations/telegram/status`, { headers: { 'X-API-Key': config.apiKey } })
  if (!res.ok) return { configured: false, connected: false }
  return res.json()
}

export async function getBudgetMode(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/budget-mode`, {
      headers: { 'X-API-Key': config.apiKey },
    })
    if (!res.ok) return true
    const data = await res.json()
    return !!data.budget_mode
  } catch {
    return true
  }
}

export async function setBudgetMode(config: AppConfig, enabled: boolean): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/budget-mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': config.apiKey },
      body: JSON.stringify({ enabled }),
    })
    if (!res.ok) return enabled
    const data = await res.json()
    return !!data.budget_mode
  } catch {
    return enabled
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

export async function indexHistory(
  config: AppConfig
): Promise<{ accepted: boolean; message: string }> {
  const res = await fetch(`${config.apiBase}/admin/index-history`, {
    method: 'POST',
    headers: { 'X-API-Key': config.apiKey },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`)
    throw new Error(text)
  }
  return res.json()
}
