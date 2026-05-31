export type Role = 'user' | 'assistant'

export interface ToolBadge {
  id: string
  name: string
}

export interface ImageBlock {
  media_type: string
  data: string   // base64, no data: prefix
}

export interface ChatMessage {
  id: string
  role: Role
  content: string
  tools: ToolBadge[]
  isStreaming: boolean
  isError: boolean
  images?: string[]   // data: URLs for display in the user bubble
}

export interface Session {
  id: number
  title: string | null
  started_at: string
}

export interface AppConfig {
  apiBase: string
  apiKey: string
}

export interface ModelInfo {
  id: string
  name: string
  description: string
  tags?: string[]
}

export interface SSEEvent {
  type: 'start' | 'chunk' | 'tool' | 'done' | 'error'
  data: unknown
  session_id: number
  request_id: string
}
