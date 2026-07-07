import type { AppConfig, SSEEvent, ModelInfo, ImageBlock, DocBlock } from './types'

/** Auth header for every backend call — the service X-API-Key. */
export function authHeaders(
  config: AppConfig,
  extra: Record<string, string> = {},
): Record<string, string> {
  return { ...extra, 'X-API-Key': config.apiKey }
}

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

/**
 * Read any file as a base64 document block for upload. No client-side parsing
 * or downscaling — the backend extracts the text (PDF/DOCX/XLSX/CSV/TXT/…) and
 * embeds it in the turn, so this works for every provider.
 */
export async function fileToDocBlock(file: File): Promise<DocBlock> {
  const dataUrl: string = await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = reject
    r.readAsDataURL(file)
  })
  const comma = dataUrl.indexOf(',')
  return {
    name: file.name || 'file',
    media_type: file.type || 'application/octet-stream',
    data: dataUrl.slice(comma + 1),
    size: file.size,
  }
}

export interface StreamOpts {
  model?: string
  systemPrompt?: string
  images?: ImageBlock[]
  documents?: DocBlock[]
  /** Regenerate/edit: delete all but the first N stored messages before running. */
  keepMessages?: number
  /** Re-run on existing history without appending a new user message. */
  regenerate?: boolean
}

export async function* streamChat(
  message: string,
  sessionId: number | null,
  config: AppConfig,
  signal: AbortSignal,
  opts: StreamOpts = {},
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${config.apiBase}/chat/${config.agentId}`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      message,
      session_id: sessionId,
      ...(opts.model ? { model: opts.model } : {}),
      ...(opts.systemPrompt ? { system_prompt: opts.systemPrompt } : {}),
      ...(opts.images && opts.images.length ? { attachments: opts.images } : {}),
      ...(opts.documents && opts.documents.length ? { documents: opts.documents } : {}),
      ...(opts.keepMessages != null ? { keep_messages: opts.keepMessages } : {}),
      ...(opts.regenerate ? { regenerate: true } : {}),
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
    headers: authHeaders(config),
  })
  if (!res.ok) return []
  return res.json()
}

export async function fetchSessions(
  config: AppConfig,
  limit = 500
): Promise<Array<{ id: number; title: string | null; started_at: string }>> {
  const res = await fetch(`${config.apiBase}/sessions?agent_id=${config.agentId}&limit=${limit}`, {
    headers: authHeaders(config),
  })
  if (!res.ok) return []
  return res.json()
}

export async function fetchModels(config: AppConfig): Promise<ModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/models`, {
      headers: authHeaders(config),
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
    headers: authHeaders(config),
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

export interface MemoryFileInfo {
  path: string
  content: string
  updated_at: string | null
  /** Canonical files are owner-editable from the board; system trails are not. */
  editable?: boolean
}

/** SPEDA's knowledge bank — the /memories virtual filesystem. */
export async function fetchMemoryFiles(config: AppConfig): Promise<MemoryFileInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/memory/files`, { headers: authHeaders(config)})
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export interface MemoryConflict {
  conflict: true
  current: MemoryFileInfo
}

/** Commit an owner edit to a memory file. On a 409 (an agent wrote since the
 *  board loaded it) returns { conflict: true, current } so the caller can
 *  re-diff instead of clobbering. */
export async function commitMemoryFile(
  config: AppConfig,
  path: string,
  content: string,
  expectedUpdatedAt: string | null
): Promise<MemoryFileInfo | MemoryConflict> {
  const res = await fetch(`${config.apiBase}/memory/files`, {
    method: 'PUT',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ path, content, expected_updated_at: expectedUpdatedAt })
  })
  if (res.status === 409) {
    const body = await res.json().catch(() => null)
    return { conflict: true, current: body?.detail?.current }
  }
  if (!res.ok) throw new Error(`Commit failed (${res.status})`)
  return res.json()
}

export interface MemoryRevisionInfo {
  id: number
  path: string
  author: string
  action: string
  created_at: string | null
  before: string
  after: string
}

export async function fetchMemoryRevisions(config: AppConfig, path: string): Promise<MemoryRevisionInfo[]> {
  try {
    const res = await fetch(
      `${config.apiBase}/memory/files/revisions?path=${encodeURIComponent(path)}`,
      { headers: authHeaders(config) }
    )
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export async function restoreMemoryRevision(config: AppConfig, revisionId: number): Promise<MemoryFileInfo> {
  const res = await fetch(`${config.apiBase}/memory/files/restore`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ revision_id: revisionId })
  })
  if (!res.ok) throw new Error(`Restore failed (${res.status})`)
  return res.json()
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
  const res = await fetch(`${config.apiBase}/connections`, { headers: authHeaders(config)})
  if (!res.ok) return { servers: [], active_tool_tokens: 0, itpm_limit: 30000 }
  return res.json()
}

export async function googleLoginUrl(config: AppConfig): Promise<{ auth_url?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/connections/google/login`, { headers: authHeaders(config)})
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function googleStatus(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/connections/google/status`, { headers: authHeaders(config)})
    if (!res.ok) return false
    return (await res.json()).connected === true
  } catch { return false }
}

export async function googleDisconnect(config: AppConfig): Promise<void> {
  await fetch(`${config.apiBase}/connections/google/disconnect`, {
    method: 'POST',
    headers: authHeaders(config),
  })
}

export async function notionLoginUrl(config: AppConfig): Promise<{ auth_url?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/connections/notion/login`, { headers: authHeaders(config)})
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function notionStatus(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/connections/notion/status`, { headers: authHeaders(config)})
    if (!res.ok) return false
    return (await res.json()).connected === true
  } catch { return false }
}

export async function notionDisconnect(config: AppConfig): Promise<void> {
  await fetch(`${config.apiBase}/connections/notion/disconnect`, {
    method: 'POST',
    headers: authHeaders(config),
  })
}

export async function setConnection(config: AppConfig, server: string, active: boolean): Promise<void> {
  await fetch(`${config.apiBase}/connections`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
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
  const res = await fetch(`${config.apiBase}/automations`, { headers: authHeaders(config)})
  if (!res.ok) return []
  return (await res.json()).automations ?? []
}

export async function toggleAutomation(config: AppConfig, id: number, active: boolean): Promise<void> {
  await fetch(`${config.apiBase}/automations/${id}/toggle`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ active }),
  })
}

export async function deleteAutomation(config: AppConfig, id: number): Promise<void> {
  await fetch(`${config.apiBase}/automations/${id}`, {
    method: 'DELETE',
    headers: authHeaders(config),
  })
}

export async function getAutomationsStatus(config: AppConfig): Promise<AutomationsStatus | null> {
  try {
    const res = await fetch(`${config.apiBase}/automations/status`, { headers: authHeaders(config)})
    if (!res.ok) return null
    return res.json()
  } catch { return null }
}

export async function telegramConnect(config: AppConfig): Promise<{ link?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/automations/telegram/connect`, {
    method: 'POST',
    headers: authHeaders(config),
  })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function telegramStatus(config: AppConfig): Promise<{ configured: boolean; connected: boolean }> {
  const res = await fetch(`${config.apiBase}/automations/telegram/status`, { headers: authHeaders(config)})
  if (!res.ok) return { configured: false, connected: false }
  return res.json()
}

export async function getBudgetMode(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/budget-mode`, {
      headers: authHeaders(config),
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
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
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
      headers: authHeaders(config),
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
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
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
    headers: authHeaders(config),
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
    headers: authHeaders(config),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`)
    throw new Error(text)
  }
  return res.json()
}

/* ── Inter-agent comms (AGENT_COMMS tray) ─────────────────────────────────── */

export interface AgentCommEntry {
  id: number
  request_id: string
  from_agent: string
  to_agent: string
  kind: string          // dispatch | broadcast
  protocol: string      // direct | house_party
  task: string
  result: string | null
  status: string        // running | ok | error | timeout | offline | refused
  duration_ms: number | null
  created_at: string
}

/** Recent inter-agent traffic, newest first. after_id polls incrementally. */
export async function fetchAgentComms(
  config: AppConfig,
  limit = 100,
  afterId = 0,
): Promise<AgentCommEntry[]> {
  try {
    const res = await fetch(
      `${config.apiBase}/agents/comms?limit=${limit}&after_id=${afterId}`,
      { headers: authHeaders(config) },
    )
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export async function getHouseParty(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/agents/house-party`, { headers: authHeaders(config) })
    if (!res.ok) return false
    return !!(await res.json()).engaged
  } catch {
    return false
  }
}

export async function setHouseParty(config: AppConfig, engaged: boolean): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/agents/house-party`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ engaged }),
    })
    if (!res.ok) return engaged
    return !!(await res.json()).engaged
  } catch {
    return engaged
  }
}

/* ── Backend configuration (Settings → Configuration) ─────────────────────── */

export interface ConfigFieldInfo {
  key: string
  label: string
  type: 'text' | 'password' | 'bool' | 'int' | 'select' | 'url'
  secret: boolean
  requires_restart: boolean
  help: string
  placeholder: string
  options: string[]
  is_set: boolean
  value?: string | number | boolean   // present for non-secret fields
  hint?: string                       // masked hint for secret fields
}

export interface ConfigGroupInfo {
  id: string
  label: string
  blurb: string
  fields: ConfigFieldInfo[]
}

export interface ConfigSaveResult {
  applied_live: string[]
  restart_required: string[]
  rejected: string[]
}

export async function getConfig(config: AppConfig): Promise<ConfigGroupInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/config`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return (await res.json()).groups ?? []
  } catch {
    return []
  }
}

/** Persist only the changed keys. Secrets left untouched must NOT be sent;
 *  sending a secret as '' clears its override. */
export async function saveConfig(
  config: AppConfig,
  values: Record<string, string | number | boolean>,
): Promise<ConfigSaveResult> {
  const res = await fetch(`${config.apiBase}/config`, {
    method: 'PUT',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ values }),
  })
  if (!res.ok) throw new Error(`Save failed (${res.status})`)
  return res.json()
}

/* ── Per-agent model routing ──────────────────────────────────────────────── */

export interface AgentModelInfo {
  agent_id: string
  name: string
  domain: string
  override: string | null
  default_main: string
  default_background: string
}

export async function fetchAgentModels(config: AppConfig): Promise<AgentModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents/models`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

/** Pin an agent to a model ref; null clears the pin (profile policy again). */
export async function pinAgentModel(
  config: AppConfig,
  agentId: string,
  model: string | null,
): Promise<AgentModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents/models`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ agent_id: agentId, model }),
    })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}
