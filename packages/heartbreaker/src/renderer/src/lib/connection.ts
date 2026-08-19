/**
 * Resolves and persists the client's own server URL + API key — the connection
 * to Igor itself, not anything Igor is configured with (that's ConfigTab, and it
 * needs this to already work before it can load anything).
 *
 * Two runtimes, one contract: Electron reads/writes through the main process
 * (`window.api.getConfig`/`setConfig`, backed by a userData JSON file — see
 * `src/main/index.ts`); the web build has no main process, so it falls back to
 * `import.meta.env.VITE_API_*` and localStorage. Either way the result tells the
 * caller not just what to use, but whether anyone actually chose it.
 */

const LOCAL_KEY = 'speda_connection_v1'

export interface ResolvedConnection {
  apiBase: string
  apiKey: string
  /** False only when nothing beyond the bare localhost/dev-key default was
   *  found — env, a build-time bake, and a prior save from the popup all count
   *  as "configured". This is the signal that a first-run prompt is warranted. */
  configured: boolean
  /** True for `npm run *:dev`. A local dev run already has a working default
   *  per HEARTBREAKER.md, so it is never nagged for connection info. */
  isDev: boolean
}

function readLocal(): { apiBase?: string; apiKey?: string } {
  try {
    const raw = localStorage.getItem(LOCAL_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

export async function resolveConnection(): Promise<ResolvedConnection> {
  if (window.api?.getConfig) {
    const raw = await window.api.getConfig()
    return { apiBase: raw.apiBase, apiKey: raw.apiKey, configured: raw.configured, isDev: raw.isDev }
  }
  const envBase = (import.meta.env.VITE_API_BASE as string | undefined)?.trim()
  const envKey = (import.meta.env.VITE_API_KEY as string | undefined)?.trim()
  const local = readLocal()
  const resolvedBase = envBase || local.apiBase
  return {
    apiBase: resolvedBase || 'http://localhost:8000',
    apiKey: envKey || local.apiKey || 'dev-key',
    configured: Boolean(resolvedBase),
    isDev: Boolean(import.meta.env.DEV),
  }
}

/** Saves the owner's server URL + key for every future launch — the popup's
 *  Connect button and the Account tab's Edit button both call this. */
export async function persistConnection(apiBase: string, apiKey: string): Promise<void> {
  if (window.api?.setConfig) {
    await window.api.setConfig({ apiBase, apiKey })
    return
  }
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify({ apiBase, apiKey })) } catch { /* */ }
}

/** One-shot reachability check against the backend's unauthenticated /health —
 *  no API key needed, so a wrong key doesn't masquerade as a wrong address. */
export async function pingHealth(apiBase: string): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase.replace(/\/+$/, '')}/health`)
    return res.ok
  } catch {
    return false
  }
}
