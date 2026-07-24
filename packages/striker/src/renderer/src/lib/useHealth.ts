import { useEffect, useState } from 'react'

export interface Health {
  online: boolean
  latencyMs: number | null
  tools: number | null
}

/**
 * Polls the backend /health endpoint for real connection telemetry:
 * reachability, round-trip latency, and the live registered-tool count.
 */
export function useHealth(
  apiBase: string | undefined,
  apiKey: string | undefined,
  intervalMs = 8000,
): Health {
  const [health, setHealth] = useState<Health>({ online: false, latencyMs: null, tools: null })

  useEffect(() => {
    if (!apiBase) return
    let alive = true

    const ping = async () => {
      const t0 = performance.now()
      try {
        const res = await fetch(`${apiBase}/health`, {
          headers: apiKey ? { 'X-API-Key': apiKey } : {},
        })
        const dt = Math.round(performance.now() - t0)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json().catch(() => ({}))
        if (alive) {
          setHealth({
            online: true,
            latencyMs: dt,
            tools: typeof data.tools_registered === 'number' ? data.tools_registered : null,
          })
        }
      } catch {
        if (alive) setHealth({ online: false, latencyMs: null, tools: null })
      }
    }

    ping()
    const id = setInterval(ping, intervalMs)
    return () => { alive = false; clearInterval(id) }
  }, [apiBase, apiKey, intervalMs])

  return health
}
