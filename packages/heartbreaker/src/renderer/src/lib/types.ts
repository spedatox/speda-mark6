export type Role = 'user' | 'assistant'

export interface ToolBadge {
  id: string
  name: string
  input?: unknown    // arguments the model passed (what it searched/added/ran)
  result?: string    // truncated tool output
}

export interface ImageBlock {
  media_type: string
  data: string   // base64, no data: prefix
}

export interface FileMeta {
  name: string
  title: string
  kind: string   // "PDF", "Word", "Image", …
  size: number
  url: string    // e.g. /files/report.pdf
}

export interface ChatMessage {
  id: string
  role: Role
  content: string
  tools: ToolBadge[]
  isStreaming: boolean
  isError: boolean
  images?: string[]   // data: URLs for display in the user bubble
  files?: FileMeta[]  // downloadable files SPEDA produced
  status?: string     // live status line while streaming (real phase, not looped filler)
}

export interface Session {
  id: number
  title: string | null
  started_at: string
}

export interface AppConfig {
  apiBase: string
  apiKey: string
  /** Owner-login session JWT. When present it is sent as Authorization: Bearer
   *  and takes precedence over apiKey. Obtained via POST /auth/login. */
  token?: string
}

export interface ModelInfo {
  id: string
  name: string
  description: string
  tags?: string[]
  provider?: string // 'anthropic' | 'openai' | 'gemini' | 'ollama' — absent on old backends
}

export interface SSEEvent {
  type: 'start' | 'chunk' | 'tool' | 'tool_result' | 'file' | 'done' | 'error'
  data: unknown
  session_id: number
  request_id: string
}
