import { useEffect, useState } from 'react'
import { fetchOnlineAgents, type OnlineAgent } from './api'
import type { AppConfig } from './types'

/**
 * Polls GET /agents for the external peers the backend currently sees online
 * (the Forge, connected as Optimus). An agent present here is running on its
 * standalone engine; an agent absent from the list is answering in-process.
 *
 * Mirrors useHealth's self-contained polling shape. The default 10s cadence is
 * gentle — peer presence changes on connect/disconnect, not per second.
 */
export function useOnlineAgents(config: AppConfig, intervalMs = 10000): OnlineAgent[] {
  const [agents, setAgents] = useState<OnlineAgent[]>([])

  useEffect(() => {
    if (!config?.apiBase) return
    let alive = true

    const poll = async () => {
      const list = await fetchOnlineAgents(config)
      if (alive) setAgents(list)
    }

    poll()
    const id = setInterval(poll, intervalMs)
    return () => { alive = false; clearInterval(id) }
  }, [config, intervalMs])

  return agents
}

/** True when the named agent is currently backed by an online external peer. */
export function useIsPeerOnline(config: AppConfig, agentId: string, intervalMs = 10000): boolean {
  const agents = useOnlineAgents(config, intervalMs)
  return agents.some(a => a.agent_id === agentId)
}
