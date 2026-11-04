import type { AppConfig } from './types'

export async function projectRequest<T>(config: AppConfig, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${config.apiBase}/projects${path}${path.includes('?') ? '&' : '?'}agent_id=${encodeURIComponent(config.agentId)}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-API-Key': config.apiKey, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const text = await response.text()
    let message = text
    try { const data = JSON.parse(text); message = data.detail?.message ?? data.detail ?? data.message ?? text } catch { /* plain response */ }
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }
  return response.json()
}

export interface FolderListing {
  path: string; root: string; parent: string | null; host_id: string
  directories: {name: string; path: string}[]; truncated: boolean
}
export interface CloudBinding { id: string; repository: string; branch: string; commit: string; image: string }
export interface WorkspaceState {
  environment?: CloudBinding | null
  cwd: string; host_id: string; execution_enabled: boolean; source_revision: string; files: string[]; files_omitted: number
  checkpoint: {objective?: string; progress?: string; next_steps?: string[]; blockers?: string[]} | null
  claims: unknown[]
  git: {available: boolean; status: string; truncated: boolean}
  tasks: {id: string; task: string; status: string; base_revision: string | null; result?: {revision?: string; integration?: string}}[]
  operations: {id: string; kind: string; status: string; details: {task_id?: string; after_revision?: string}}[]
}
export interface ProjectChanges {
  environment?: CloudBinding | null
  task_id: string; expected_source_revision: string; expected_artifact_revision: string
  source_changed_since_execution: boolean; diff: string
  changes: {path: string; change: string}[]
}
