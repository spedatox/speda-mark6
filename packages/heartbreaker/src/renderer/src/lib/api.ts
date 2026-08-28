import type { AppConfig, SSEEvent, ModelInfo, ImageBlock, DocBlock, PendingAsk, Session } from './types'

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
  /** Working directory for an external-backend agent (the Forge / Optimus). */
  cwd?: string
  /** The reply will be SPOKEN. Changes how the backend asks for it to be
   *  written — prose to be heard, artefacts fenced for the canvas — so it has to
   *  be known before the turn runs, not filtered out of it afterwards. */
  voice?: boolean
}

/**
 * Surface + locale for this client, so Speda knows the owner is on the desktop
 * app (or the dev web build) rather than the phone or Telegram. The backend
 * stamps it onto the live turn only — never stored (see app/core/surface.py).
 */
function desktopClientContext(): { platform: string; locale?: string } {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
  return {
    platform: /Electron/i.test(ua) ? 'desktop' : 'web',
    locale: typeof navigator !== 'undefined' ? navigator.language || undefined : undefined,
  }
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
      ...(opts.cwd ? { cwd: opts.cwd } : {}),
      // Surface awareness — tell Speda whether this turn came from the desktop
      // app or the web build. (The Android app and Telegram set their own.)
      client_context: { ...desktopClientContext(), ...(opts.voice ? { voice: true } : {}) },
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

/** A short JARVIS-style welcome remark for the home screen (cached server-side,
 *  cheapest model). Empty string on any failure — the UI keeps its greeting. */
export async function fetchWelcome(config: AppConfig, agentId: string): Promise<string> {
  try {
    const res = await fetch(`${config.apiBase}/welcome/${agentId}`, { headers: authHeaders(config) })
    if (!res.ok) return ''
    const d = await res.json()
    return typeof d.text === 'string' ? d.text : ''
  } catch { return '' }
}

/** Re-attach to a detached, still-running (or just-finished) turn: replays the
 *  buffered events then tails the live stream, exactly like streamChat's output.
 *  Used when returning to a session whose turn kept running server-side. */
export async function* attachStream(
  config: AppConfig,
  requestId: string,
  signal: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${config.apiBase}/chat/attach/${requestId}`, {
    headers: authHeaders(config),
    signal,
  })
  if (!res.ok || !res.body) return
  const reader = res.body.getReader()
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
      try { yield JSON.parse(raw) as SSEEvent } catch { /* malformed */ }
    }
  }
}

export interface ActiveRun {
  request_id: string
  agent_id: string
  session_id: number
  running_s: number
  idle_s: number
}

export interface MemoryFolderInfo {
  path: string
  summary: string
  owner_agent: string | null
  open: boolean
}

/** Folders the store DECLARES — including ones holding no file yet.
 *  A folder with no files does not exist in the table, so without this the
 *  knowledge bank cannot show the owner where a thing will go before
 *  something has gone there. */
export async function fetchMemoryFolders(config: AppConfig): Promise<MemoryFolderInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/memory/folders`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return res.json()
  } catch { return [] }
}

/** Detached turns the backend is currently running (optionally one session). */
export async function fetchActiveRuns(config: AppConfig, sessionId?: number): Promise<ActiveRun[]> {
  try {
    const q = sessionId != null ? `?session_id=${sessionId}` : ''
    const res = await fetch(`${config.apiBase}/chat/active${q}`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return res.json()
  } catch { return [] }
}

/** Cancel a running turn (the stop button). Dropping the SSE socket no longer
 *  cancels a run, so this is the only way to actually stop one. */
export async function cancelRun(config: AppConfig, requestId: string): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/chat/cancel/${requestId}`, {
      method: 'POST', headers: authHeaders(config),
    })
    if (!res.ok) return false
    const d = await res.json()
    return !!d.cancelled
  } catch { return false }
}

/** Inject `text` into the turn `requestId` is currently streaming, instead of
 *  waiting for it to finish. Resolves false (never throws) when the turn is
 *  not steerable — in-process, or it already ended — so the caller can keep
 *  the composer text rather than losing it. */
export async function steerRun(config: AppConfig, requestId: string, text: string): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/chat/steer/${requestId}`, {
      method: 'POST',
      headers: { ...authHeaders(config), 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) return false
    const d = await res.json()
    return !!d.steered
  } catch { return false }
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
): Promise<Session[]> {
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

/** Speda's knowledge bank — the /memories virtual filesystem. */
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

export async function microsoftLoginUrl(config: AppConfig): Promise<{ auth_url?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/connections/microsoft/login`, { headers: authHeaders(config)})
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function microsoftStatus(config: AppConfig): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/connections/microsoft/status`, { headers: authHeaders(config)})
    if (!res.ok) return false
    return (await res.json()).connected === true
  } catch { return false }
}

export async function microsoftDisconnect(config: AppConfig): Promise<void> {
  await fetch(`${config.apiBase}/connections/microsoft/disconnect`, {
    method: 'POST',
    headers: authHeaders(config),
  })
}

/* ── Hand-added MCP servers ───────────────────────────────────────────────
   A server is a command or a URL plus credentials, so the owner can wire one
   up without a code change — which is the whole point of MCP as a tier. */

export interface CustomMcpServer {
  name: string
  transport: 'stdio' | 'http'
  command: string | string[]
  url: string
  /** Credential values come back MASKED; sending one back unchanged keeps the
      stored secret rather than overwriting it with the placeholder. */
  env: Record<string, string>
  headers: Record<string, string>
  enabled: boolean
  note: string
  added_at?: string
  connected?: boolean
  tools?: number
  tokens?: number
}

export async function getCustomMcpServers(
  config: AppConfig,
): Promise<{ servers: CustomMcpServer[]; reserved: string[] }> {
  const res = await fetch(`${config.apiBase}/connections/mcp`, { headers: authHeaders(config) })
  if (!res.ok) return { servers: [], reserved: [] }
  return res.json()
}

export async function saveCustomMcpServer(
  config: AppConfig,
  server: Partial<CustomMcpServer>,
): Promise<{ connected?: boolean; tools?: number; error?: string; message?: string }> {
  const res = await fetch(`${config.apiBase}/connections/mcp`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(server),
  })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function deleteCustomMcpServer(config: AppConfig, name: string): Promise<void> {
  await fetch(`${config.apiBase}/connections/mcp/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers: authHeaders(config),
  })
}

// ── Web portals — the sites the owner has an account on ─────────────────────
// The password comes back MASKED and is meant to be sent back that way: the
// backend reads an unchanged mask as "keep the stored one", which is what lets
// the owner fix a label without retyping a credential they can no longer see.

export interface Portal {
  name: string
  label: string
  login_url: string
  home_url: string
  username: string
  /** Masked on read. Send it back untouched to keep the stored password. */
  password: string
  selectors: Record<string, string>
  extra_fields: Record<string, string>
  success_selector: string
  success_url_contains: string
  allowed_agents: string[]
  note: string
  enabled: boolean
  added_at?: string
  last_login?: string
  last_status?: string
  /** Whether the browser container currently holds cookies for this portal. */
  session?: boolean
}

export interface BrowserStatus {
  status: 'ok' | 'down' | 'off'
  reason?: string
  sessions?: number
  profiles?: string[]
}

export async function getPortals(
  config: AppConfig,
): Promise<{ portals: Portal[]; browser: BrowserStatus; agents: string[] }> {
  const res = await fetch(`${config.apiBase}/connections/portals`, { headers: authHeaders(config) })
  if (!res.ok) return { portals: [], browser: { status: 'down', reason: `HTTP ${res.status}` }, agents: [] }
  return res.json()
}

export async function savePortal(
  config: AppConfig,
  portal: Partial<Portal> & { test?: boolean },
): Promise<{ ok?: boolean | null; error?: string; message?: string; landed_on?: string }> {
  const res = await fetch(`${config.apiBase}/connections/portals`, {
    method: 'POST',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(portal),
  })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function portalLogin(
  config: AppConfig,
  name: string,
): Promise<{ ok?: boolean; already?: boolean; message?: string; landed_on?: string; error?: string }> {
  const res = await fetch(`${config.apiBase}/connections/portals/${encodeURIComponent(name)}/login`, {
    method: 'POST',
    headers: authHeaders(config),
  })
  if (!res.ok) return { error: `HTTP ${res.status}` }
  return res.json()
}

export async function portalForget(config: AppConfig, name: string): Promise<void> {
  await fetch(`${config.apiBase}/connections/portals/${encodeURIComponent(name)}/forget`, {
    method: 'POST',
    headers: authHeaders(config),
  })
}

export async function deletePortal(config: AppConfig, name: string): Promise<void> {
  await fetch(`${config.apiBase}/connections/portals/${encodeURIComponent(name)}`, {
    method: 'DELETE',
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

/* ── Automations — Speda's proactive n8n watchers ─────────────────────────── */

/** The six things the owner can build from Settings — three fire on a clock,
 *  three ("hook_*") fire on an event instead. See templates.py. */
export type AutomationTemplate =
  | 'briefing' | 'reminder' | 'proactive_ask'
  | 'hook_keyword' | 'hook_address' | 'hook_mail'

export type AutomationFrequency = 'once' | 'daily' | 'weekly' | 'monthly'

/**
 * When an automation fires, as STRUCTURE.
 *
 * The backend deliberately does not send a sentence: this pane renders in two
 * languages, and "Günde bir" chosen server-side would have picked one for the
 * owner. `cron` rides along for debugging only — never show it. See
 * packages/igor/app/automations/schedule.py.
 */
export interface AutomationSchedule {
  frequency: AutomationFrequency
  at: string                    // 'HH:MM' in the owner's timezone
  days?: number[]               // weekly — 1=Mon … 7=Sun
  dom?: number                  // monthly — day of month
  date?: string                 // once — 'YYYY-MM-DD'
  skips_short_months?: boolean  // monthly on the 29th+ — February eats it
  timezone: string
  cron: string | null
}

/**
 * A weekday class this automation's agent must be TOLD rather than left to work
 * out — "gym günü" on Mon/Wed/Fri. Computed by n8n at fire time and appended to
 * the instruction, because a model asked to derive it from a prose schedule
 * once told the owner he had trained on a day he had not.
 */
export interface AutomationDayFlag {
  label: string
  days: number[]   // 1=Mon … 7=Sun
}

/**
 * A Hook's structured watcher config — url/domain and polling interval, never
 * a sentence, same reason `AutomationSchedule` is structural. See
 * `composer.hook_display()`.
 */
export interface AutomationHook {
  type: 'keyword' | 'address' | 'mail'
  url?: string
  look_for?: string
  domain?: string
  recipient?: string
  interval_minutes: number
}

/** One agent that can own an automation, for the form's picker. */
export interface AutomationAgent {
  agent_id: string
  name: string
  domain: string
}

export interface AutomationInfo {
  id: number
  agent_id: string
  n8n_workflow_id: string | null
  name: string
  kind: 'schedule' | 'web_watch' | 'rss_watch' | 'webhook' | string
  intent: string
  active: boolean
  created_at: string | null
  expires_at: string | null
  last_fired_at: string | null
  summary: string
  /** Null for a raw agent-authored watcher — it has no template at all. */
  template: AutomationTemplate | null
  /** Null for a Hook or a raw watcher — neither has a clock. */
  schedule: AutomationSchedule | null
  /** Null for anything that isn't a Hook. */
  hook: AutomationHook | null
  /** The editable content half: what the owner asked for, possibly rewritten. */
  instruction: string | null
  /** His original wording, kept even after a polish so the editor can show it. */
  instruction_raw: string | null
  intent_status: 'raw' | 'polished' | 'failed' | null
  options: string[] | null
  every_minutes: number | null
  max_asks: number | null
  day_flags: AutomationDayFlag[] | null
  url: string | null
  look_for: string | null
  domain: string | null
  recipient: string | null
  interval_minutes: number | null
  /** Push automations only — reply spoken in the firing agent's TTS voice and
   *  sent as a Telegram audio message instead of text. Always false for
   *  proactive_ask, which already delivers through the reminders tool. */
  voice: boolean
}

/** The form's payload. Mirrors the composer spec; the backend validates it. */
export interface AutomationDraft {
  agent_id: string
  template: AutomationTemplate
  name: string
  instruction: string
  /** Required for the three schedule templates; absent for the three Hooks. */
  schedule?: {
    frequency: AutomationFrequency
    at: string
    days?: number[]
    dom?: number
    date?: string
  }
  options?: string[]
  every_minutes?: number
  max_asks?: number
  day_flags?: AutomationDayFlag[]
  /** Hook fields — hook_keyword/hook_address use url(+look_for); hook_mail
   *  uses domain(+recipient). interval_minutes applies to all three. */
  url?: string
  look_for?: string
  domain?: string
  recipient?: string
  interval_minutes?: number
  /** Any push template except proactive_ask. */
  voice?: boolean
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

/** Toggle a watcher on/off. The backend refuses the flip — and leaves the row
 *  untouched — when it can't also sync n8n (unreachable, not configured), so
 *  the caller must check for `error` and revert its optimistic update rather
 *  than assume the POST landed. */
export async function toggleAutomation(
  config: AppConfig, id: number, active: boolean,
): Promise<{ error: string } | null> {
  try {
    const res = await fetch(`${config.apiBase}/automations/${id}/toggle`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ active }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data?.error) return { error: data?.error || `Request failed (${res.status})` }
    return null
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Could not reach the backend.' }
  }
}

export async function deleteAutomation(config: AppConfig, id: number): Promise<void> {
  await fetch(`${config.apiBase}/automations/${id}`, {
    method: 'DELETE',
    headers: authHeaders(config),
  })
}

export async function getAutomationAgents(config: AppConfig): Promise<AutomationAgent[]> {
  try {
    const res = await fetch(`${config.apiBase}/automations/agents`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return (await res.json()).agents ?? []
  } catch { return [] }
}

/**
 * Create an automation. Resolves to the created row, or `{ error }` carrying the
 * backend's own message — which names the field and the fix, and is the only
 * feedback the form has to give.
 */
export async function createAutomation(
  config: AppConfig, draft: AutomationDraft,
): Promise<AutomationInfo | { error: string }> {
  try {
    const res = await fetch(`${config.apiBase}/automations`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(draft),
    })
    if (!res.ok) return { error: `HTTP ${res.status}` }
    return await res.json()
  } catch (e) { return { error: String(e) } }
}

/** Edit in place. The n8n workflow is updated, never recreated, so its "already
 *  fired today" memory and execution history survive the edit. */
export async function updateAutomation(
  config: AppConfig, id: number, draft: Partial<AutomationDraft>,
): Promise<AutomationInfo | { error: string }> {
  try {
    const res = await fetch(`${config.apiBase}/automations/${id}`, {
      method: 'PUT',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(draft),
    })
    if (!res.ok) return { error: `HTTP ${res.status}` }
    return await res.json()
  } catch (e) { return { error: String(e) } }
}

/**
 * Fire an automation's stored intent right now — the exact turn n8n would
 * start when its schedule comes due, not a mock. A push automation really
 * pushes to Telegram; a proactive ask really nags with real buttons. Never
 * touches n8n's own "already fired today" latch, so it cannot cause or be
 * mistaken for a duplicate real firing.
 */
export async function testAutomation(
  config: AppConfig, id: number,
): Promise<{ started: true; request_id: string } | { error: string }> {
  try {
    const res = await fetch(`${config.apiBase}/automations/${id}/test`, {
      method: 'POST',
      headers: authHeaders(config),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) return { error: body?.error || `HTTP ${res.status}` }
    if (body.error) return { error: body.error }
    return { started: true, request_id: body.request_id }
  } catch (e) { return { error: String(e) } }
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
): Promise<{ accepted: boolean; job_id?: number; message: string }> {
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

export interface MemoryStatus {
  job: {
    id: number
    status: 'pending' | 'running' | 'done' | 'failed'
    attempts: number
    last_error: string | null
    /** Written by the running rebuild itself, so a long job reports where it is. */
    progress?: { done: number; total: number; stored: number } | null
  } | null
  observations: number
  by_origin: Record<string, number>
  at_risk_facts: number
  thin_compositions: string[]
  verdict: string
}

/** Where the memory record stands. Cheap (no model call) — safe to poll. */
export async function memoryStatus(config: AppConfig): Promise<MemoryStatus> {
  const res = await fetch(`${config.apiBase}/admin/memory/status`, {
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

/** Stand the protocol down (and, historically, the un-gated toggle).
 *
 *  Engaging goes through engageHouseParty, which carries the passphrase. This
 *  still sends `platform` so an engage attempted through here is judged by the
 *  same rule rather than slipping past it. */
export async function setHouseParty(config: AppConfig, engaged: boolean): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/agents/house-party`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ engaged, platform: desktopClientContext().platform }),
    })
    if (!res.ok) return engaged
    return !!(await res.json()).engaged
  } catch {
    return engaged
  }
}

/* ── Lockdown Protocol ────────────────────────────────────────────────────── */

export interface LockdownState {
  /** Client-side only: false when THIS APP could not reach Igor at all. Without
   *  it the fallback below reads as `enabled: false`, and the panel then says
   *  "disabled on this deployment — set LOCKDOWN_PROTOCOL_ENABLED" about a
   *  server it never managed to ask. Same distinction the three host protocols
   *  carry, and it belongs here for the same reason: the comment below already
   *  said an unreachable backend is not proof containment is off, but nothing
   *  downstream could tell. */
  reachable: boolean
  engaged: boolean
  enabled: boolean
  /** What the host firewall actually shows, keyed by what each rule seals.
   *  Reported separately from `engaged` so a drift between the flag and the
   *  real rules is visible instead of averaged away. */
  rules: Record<string, boolean>
  report?: string | null
}

export async function getLockdown(config: AppConfig): Promise<LockdownState> {
  try {
    const res = await fetch(`${config.apiBase}/agents/lockdown`, { headers: authHeaders(config) })
    if (!res.ok) return { reachable: false, engaged: false, enabled: false, rules: {} }
    return { ...(await res.json()), reachable: true }
  } catch {
    // Unreachable backend is NOT proof containment is off — say nothing rather
    // than render an all-clear the server never sent.
    return { reachable: false, engaged: false, enabled: false, rules: {} }
  }
}

/** Stand containment down. Never takes a passphrase: the way out must always be
 *  available, or a lockdown could outlive the owner's ability to lift it. */
export async function standDownLockdown(
  config: AppConfig,
): Promise<{ ok: boolean; error?: string; report?: string }> {
  try {
    const res = await fetch(`${config.apiBase}/agents/lockdown`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ engaged: false }),
    })
    if (!res.ok) return { ok: false, error: `Stand down failed (HTTP ${res.status}).` }
    const body = await res.json()
    return { ok: true, report: body.report }
  } catch {
    return { ok: false, error: "Couldn't reach the backend to stand down." }
  }
}


/* ── The host protocols ────────────────────────────────────────────────────
 *
 * Read-only status for the three protocols the owner drives through Orion, plus
 * the one action worth its own button.
 *
 * All four follow the rule getLockdown established: an unreachable backend is
 * NOT evidence that everything is fine. Each fallback is the shape that reads as
 * "unknown / no protection" rather than a green light the server never sent —
 * `status: 'error'` for the lifeboat, `stale: true` for Octavius. Otherwise the
 * panel reports a healthy disk and a fresh backup at precisely the moment the
 * server stopped answering.
 *
 * They are also deliberately absent from the pane's 5-second poll: a lifeboat
 * read is an SSH round trip to the host and an Octavius read is a Google API
 * call. Neither answer changes meaningfully in five seconds, and paying for them
 * at that rate for as long as a settings window is open would be absurd.
 */

export interface LifeboatReadings {
  filesystem?: string
  disk_pct?: number
  disk_free_gb?: number
  disk_total_gb?: number
  inode_pct?: number
  mem_pct?: number
  mem_available_gb?: number
  mem_total_gb?: number
  swap_pct?: number
  docker_reclaimable_gb?: number
}

export interface LifeboatState {
  /** Client-side only: false when THIS APP could not reach Igor at all. Kept
   *  apart from `status` because the three failures need different answers —
   *  the app cannot reach Igor, Igor cannot reach the host, or the protocol is
   *  switched off. Collapsing them is how a panel tells you a protocol is
   *  disabled when in fact nobody asked it anything. */
  reachable: boolean
  /** ok | error | disabled */
  status: string
  /** healthy | watch | critical — the WORST of disk, inodes and memory. */
  level: string
  by_resource: Record<string, string>
  pressed: string[]
  readings: LifeboatReadings
  summary: string
  recommendation: string
  target_free_gb: number
  detail: string
}

const lifeboatUnknown = (): LifeboatState => ({
  reachable: false, status: 'error', level: 'healthy', by_resource: {}, pressed: [],
  readings: {}, summary: '', recommendation: '', target_free_gb: 0, detail: '',
})

export async function getLifeboat(config: AppConfig): Promise<LifeboatState> {
  try {
    const res = await fetch(`${config.apiBase}/host/lifeboat`, { headers: authHeaders(config) })
    if (!res.ok) return lifeboatUnknown()
    return { ...(await res.json()), reachable: true }
  } catch {
    return lifeboatUnknown()
  }
}

export interface DoormatChecklistItem {
  provider: string
  where: string
  field: string
  value: string
  note: string
}

export interface DoormatState {
  /** Client-side only — see LifeboatState.reachable. */
  reachable: boolean
  enabled: boolean
  /** '' (idle) | 'staged' | 'cutover' */
  phase: string
  target: string
  previous: string
  staged_at: string
  cutover_at: string
  current_domain: string
  /** null while idle; otherwise whether the new domain actually answers. */
  target_serving: boolean | null
  /** Cutover written but Igor not restarted, so it still runs on the old domain. */
  restart_pending: boolean
  checklist: DoormatChecklistItem[]
  detail: string
}

const doormatUnknown = (): DoormatState => ({
  reachable: false, enabled: false, phase: '', target: '', previous: '', staged_at: '', cutover_at: '',
  current_domain: '', target_serving: null, restart_pending: false,
  checklist: [], detail: '',
})

export async function getDoormat(config: AppConfig): Promise<DoormatState> {
  try {
    const res = await fetch(`${config.apiBase}/host/doormat`, { headers: authHeaders(config) })
    if (!res.ok) return doormatUnknown()
    return { ...(await res.json()), reachable: true }
  } catch {
    return doormatUnknown()
  }
}

export interface BackupEntry {
  id: string
  name: string
  bytes: number
  mb: number
  created: string
  sha256: string
}

export interface OctaviusState {
  /** Client-side only — see LifeboatState.reachable. */
  reachable: boolean
  enabled: boolean
  count: number
  latest: BackupEntry | null
  age_hours: number | null
  /** Newest copy too old, none at all, or Drive unreachable. All three mean the
   *  same thing to whoever is reading: no protection you can count on. */
  stale: boolean
  detail: string
}

const octaviusUnknown = (): OctaviusState => ({
  reachable: false, enabled: false, count: 0, latest: null, age_hours: null, stale: true, detail: '',
})

export async function getOctavius(config: AppConfig): Promise<OctaviusState> {
  try {
    const res = await fetch(`${config.apiBase}/admin/octavius`, { headers: authHeaders(config) })
    if (!res.ok) return octaviusUnknown()
    return { ...(await res.json()), reachable: true }
  } catch {
    return octaviusUnknown()
  }
}

/** Take a backup now. Minutes on a large database — snapshot, gzip, upload — so
 *  the caller keeps its button disabled throughout rather than assuming speed.
 *  A failed run returns ok:false with the STAGE it stopped at, which the panel
 *  shows: an 'integrity' failure is a statement about the live database, not
 *  about the backup, and must not read as "try again later". */
export async function runOctaviusBackup(
  config: AppConfig,
): Promise<{ ok: boolean; name?: string; stage?: string; error?: string }> {
  try {
    const res = await fetch(`${config.apiBase}/admin/octavius/backup`, {
      method: 'POST',
      headers: authHeaders(config),
    })
    if (!res.ok) return { ok: false, error: `Backup failed (HTTP ${res.status}).` }
    const body = await res.json()
    return { ok: !!body.ok, name: body.name, stage: body.stage, error: body.error }
  } catch {
    return { ok: false, error: 'Could not reach the server.' }
  }
}


/* ── Skyfall ───────────────────────────────────────────────────────────────
 *
 * Projects are the owner's own launch targets. Two things about this client are
 * load-bearing:
 *
 * 1. **Header values never arrive here.** The server sends the header NAMES
 *    mapped to a mask; the values stay behind it. Saving a form rendered from
 *    that data sends the mask straight back, which the server reads as "leave
 *    this one alone" — so the owner does not have to retype every secret to
 *    change a description.
 *
 * 2. **`fire` is what the countdown calls at zero, and nothing else calls it.**
 *    Aborting is not an API call at all: it is this app never making one. That
 *    is the strongest form the abort can take — there is no request in flight to
 *    race, and a crashed renderer fails toward "did not fire".
 */

export interface SkyfallProject {
  id: string
  name: string
  description: string
  url: string
  method: string
  body: string
  /** Header NAMES → a mask. Values live on the server only. */
  headers: Record<string, string>
  has_body: boolean
  countdown_seconds: number
  created_at: string
  updated_at: string
  last_fired_at: string
  last_result: string
}

/** What the countdown screen needs. No body, no headers — the request is
 *  assembled server-side at zero, so this app never holds the secret and cannot
 *  turn an armed countdown into a different request than the one armed. */
export interface SkyfallArm {
  project_id: string
  name: string
  description: string
  method: string
  url: string
  countdown_seconds: number
  armed_at: string
}

export interface SkyfallResult {
  /** Did a request leave? Separate from `ok` on purpose: "went out and came back
   *  500" and "never left" are different events and must not render alike. */
  fired: boolean
  ok: boolean
  status: number
  body: string
  truncated: boolean
  error: string
  started_at: string
  finished_at: string
}

export async function listSkyfallProjects(config: AppConfig): Promise<SkyfallProject[]> {
  try {
    const res = await fetch(`${config.apiBase}/protocols/skyfall/projects`, {
      headers: authHeaders(config),
    })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export async function saveSkyfallProject(
  config: AppConfig,
  project: Partial<SkyfallProject>,
): Promise<{ ok: boolean; project?: SkyfallProject; error?: string }> {
  try {
    const res = await fetch(`${config.apiBase}/protocols/skyfall/projects`, {
      method: 'PUT',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(project),
    })
    const body = await res.json().catch(() => ({}))
    // The server validates and answers in words the pane can show as-is —
    // re-wording it here would put a second, drifting copy of the rules in the UI.
    if (!res.ok) return { ok: false, error: body?.detail || `HTTP ${res.status}` }
    return { ok: true, project: body }
  } catch {
    return { ok: false, error: 'Could not reach the server.' }
  }
}

export async function deleteSkyfallProject(config: AppConfig, id: string): Promise<boolean> {
  try {
    const res = await fetch(`${config.apiBase}/protocols/skyfall/projects/${id}`, {
      method: 'DELETE',
      headers: authHeaders(config),
    })
    return res.ok
  } catch {
    return false
  }
}

/** Get the countdown payload for a project picked from the list. Sends nothing. */
export async function armSkyfall(config: AppConfig, id: string): Promise<SkyfallArm | null> {
  try {
    const res = await fetch(`${config.apiBase}/protocols/skyfall/arm/${id}`, {
      method: 'POST',
      headers: authHeaders(config),
    })
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

/** The clock reached zero. This is the only caller. */
export async function fireSkyfall(config: AppConfig, id: string): Promise<SkyfallResult> {
  try {
    const res = await fetch(`${config.apiBase}/protocols/skyfall/fire`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ project_id: id }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) {
      // A 409 means nothing was sent — a deleted or unusable project. Say that,
      // rather than letting the screen imply a launch that never happened.
      return {
        fired: false, ok: false, status: 0, body: '', truncated: false,
        error: body?.detail || `HTTP ${res.status}`, started_at: '', finished_at: '',
      }
    }
    return body
  } catch {
    // The request may or may not have left this machine. `fired: true` is the
    // honest answer — the screen must not tell the owner nothing happened when
    // it cannot know that.
    return {
      fired: true, ok: false, status: 0, body: '', truncated: false,
      error: 'Lost contact with the server mid-launch — whether the request went '
        + 'out is unknown. Check the target before firing again.',
      started_at: '', finished_at: '',
    }
  }
}

/** Record that the owner stopped the clock. Best-effort: the abort already
 *  happened by NOT firing, and this call only writes it down. */
export async function abortSkyfall(
  config: AppConfig, id: string, remaining: number,
): Promise<void> {
  try {
    await fetch(`${config.apiBase}/protocols/skyfall/abort`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ project_id: id, remaining_seconds: remaining }),
    })
  } catch {
    /* nothing was sent either way */
  }
}

/** Engage containment with the owner's authorization passphrase (the same secret
 *  House Party uses). A 409 means the server refused to seal — the protocol is
 *  NOT active, so the caller must not render it as such. */
export async function engageLockdown(
  config: AppConfig,
  passphrase: string,
): Promise<{ ok: boolean; error?: string; report?: string }> {
  try {
    const res = await fetch(`${config.apiBase}/agents/lockdown`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ engaged: true, passphrase }),
    })
    if (res.status === 403) return { ok: false, error: 'Authorization denied — incorrect passphrase.' }
    if (res.status === 409) {
      const body = await res.json().catch(() => ({}))
      return { ok: false, error: body.detail || 'Containment failed — the host was NOT sealed.' }
    }
    if (!res.ok) return { ok: false, error: `Engage failed (HTTP ${res.status}).` }
    const body = await res.json()
    return { ok: !!body.engaged, report: body.report }
  } catch {
    return { ok: false, error: "Couldn't reach the backend to engage." }
  }
}

/** Engage the House Party Protocol with the owner's authorization passphrase.
 *  The passphrase is validated server-side (never stored in chat). Returns
 *  {ok:false, error} on a wrong passphrase (403) or a network failure. */
export async function engageHouseParty(
  config: AppConfig,
  passphrase: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`${config.apiBase}/agents/house-party`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      // `platform` gates the engage: the backend refuses any surface that is
      // not the deck, and a client that does not name itself counts as not the
      // deck. This is the path that must always carry it.
      body: JSON.stringify({ engaged: true, passphrase, platform: desktopClientContext().platform }),
    })
    if (res.status === 403) return { ok: false, error: 'Authorization denied — incorrect passphrase.' }
    if (res.status === 409) {
      const detail = await res.json().catch(() => null)
      return { ok: false, error: detail?.detail ?? 'The protocol is available on the desktop app only.' }
    }
    if (!res.ok) return { ok: false, error: `Engage failed (HTTP ${res.status}).` }
    return { ok: !!(await res.json()).engaged }
  } catch {
    return { ok: false, error: "Couldn't reach the backend to engage." }
  }
}

/* ── Per-agent source-of-truth memory files ───────────────────────────────── */

export interface SourceAgentInfo {
  agent_id: string
  name: string
  domain: string
  source: string | null   // the /memories/*.md file this agent reads+writes
  default: string | null
}

export interface MemorySources {
  files: string[]          // editable /memories/*.md files to choose from
  agents: SourceAgentInfo[]
}

export async function getMemorySources(config: AppConfig): Promise<MemorySources> {
  try {
    const res = await fetch(`${config.apiBase}/memory/sources`, { headers: authHeaders(config) })
    if (!res.ok) return { files: [], agents: [] }
    return res.json()
  } catch {
    return { files: [], agents: [] }
  }
}

/** Assign (or clear, with null) an agent's source-of-truth file. */
export async function setMemorySource(
  config: AppConfig,
  agentId: string,
  path: string | null,
): Promise<{ agent_id: string; source: string | null }> {
  const res = await fetch(`${config.apiBase}/memory/sources`, {
    method: 'PUT',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ agent_id: agentId, path }),
  })
  if (!res.ok) throw new Error(`Assign failed (${res.status})`)
  return res.json()
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
  telegram_override: string | null
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

/** Pin an agent to a Telegram-specific model; null clears (falls back to desktop model). */
export async function pinTelegramModel(
  config: AppConfig,
  agentId: string,
  model: string | null,
): Promise<AgentModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents/telegram-models`, {
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

/* ── Legion worker model routing ──────────────────────────────────────────── */

export interface LegionModelInfo {
  worker_id: string
  when_to_use: string
  effort: string
  /** Human-readable description of the effort rule used when nothing is pinned. */
  derived_from: string
  override: string | null
  /** LEGION_MODEL_OVERRIDE from the deployment env — outranks every pin. */
  deployment_pin: string | null
}

export async function fetchLegionModels(config: AppConfig): Promise<LegionModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents/legion-models`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

/** Pin a legionnaire to a model ref; null clears it (back to effort policy). */
export async function pinLegionModel(
  config: AppConfig,
  workerId: string,
  model: string | null,
): Promise<LegionModelInfo[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents/legion-models`, {
      method: 'POST',
      headers: authHeaders(config, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ worker_id: workerId, model }),
    })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

/* ── Online external peers (the Forge link) ───────────────────────────────── */

/** One external peer agent currently connected over WS /agents/ws/<id> — i.e.
 *  an agent whose real engine is a standalone backend (the Forge, for Optimus).
 *  An agent absent from this list is answering from its in-process profile. */
export interface OnlineAgent {
  agent_id: string
  agent_name: string
  domain: string
  status: string
  last_seen: string | null
  capabilities: string[]
}

/** The list of external peers the backend currently sees as online. Feeds the
 *  FORGE LINK status indicator; a fetch failure is treated as "none online". */
export async function fetchOnlineAgents(config: AppConfig): Promise<OnlineAgent[]> {
  try {
    const res = await fetch(`${config.apiBase}/agents`, { headers: authHeaders(config) })
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}


/** Irreversible operations a peer is waiting on the owner for.

    The guaranteed path. A chat job's card also arrives inline on its own
    stream, but a dispatched background job has no stream, and a window that was
    closed when the ask was raised never saw one either. */
export async function fetchPendingAsks(config: AppConfig): Promise<PendingAsk[]> {
  const res = await fetch(`${config.apiBase}/agents/asks`, { headers: authHeaders(config) })
  if (!res.ok) return []
  return res.json()
}

/** Send the owner's decision down to the peer.

    A 404 means the ask is gone — expired, already answered, or its agent
    disconnected. That is not a retry condition: the peer runs its own timeout
    and has already denied locally, so the operation did not happen either way. */
export async function answerAsk(
  config: AppConfig,
  askId: string,
  approved: boolean,
  remember = false,
  note = '',
): Promise<boolean> {
  const res = await fetch(`${config.apiBase}/agents/asks/${encodeURIComponent(askId)}`, {
    method: 'POST',
    headers: { ...authHeaders(config), 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, remember, note }),
  })
  return res.ok
}

/* ── Persistent reminders ─────────────────────────────────────────────────────
 * Standing reminders the owner configures in Settings ▸ Reminders. Igor asks
 * them on a schedule and keeps asking until answered; these endpoints are the
 * definitions, not the runs (see app/models/reminder_definition.py).
 */

export interface ReminderOption { label: string; value: string }
export interface ReminderDefinition {
  id: string
  agent: string
  text: string
  at: string
  days: string
  options: ReminderOption[]
  every_minutes: number
  max_asks: number
  enabled: boolean
  updated_at?: string
}
export interface ReminderCycleInfo {
  reminder_id: string; day: string; status: string
  answer: string; via: string; asks: number; closed_at: string
}

export async function getReminders(config: AppConfig): Promise<ReminderDefinition[]> {
  const res = await fetch(`${config.apiBase}/reminders/definitions`, { headers: authHeaders(config) })
  if (!res.ok) return []
  return (await res.json()).definitions ?? []
}

export async function saveReminder(
  config: AppConfig,
  def: ReminderDefinition,
): Promise<{ status: string; detail?: string }> {
  const { id, ...body } = def
  const res = await fetch(`${config.apiBase}/reminders/definitions/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: authHeaders(config, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) return { status: 'error', detail: `HTTP ${res.status}` }
  return await res.json()
}

export async function deleteReminder(config: AppConfig, id: string): Promise<void> {
  await fetch(`${config.apiBase}/reminders/definitions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(config),
  })
}

/** Recently closed cycles — what was actually taken or missed. */
export async function getReminderHistory(
  config: AppConfig, limit = 30,
): Promise<ReminderCycleInfo[]> {
  const res = await fetch(`${config.apiBase}/reminders/history?limit=${limit}`, {
    headers: authHeaders(config),
  })
  if (!res.ok) return []
  return (await res.json()).history ?? []
}
