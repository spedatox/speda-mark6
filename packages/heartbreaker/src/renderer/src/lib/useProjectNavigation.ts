import { useEffect, useRef, useState } from 'react'
import type { AppConfig } from './types'
import { useChatContext } from '../store/chat'
import { fetchProjects } from './api'

/** Remember selection only. The server remains authoritative about folder/chat access. */
export function useProjectNavigation(config: AppConfig, select: (sessionId:number, projectId?:number|null)=>unknown) {
  const {state, dispatch} = useChatContext()
  const live = useRef(state)
  live.current = state
  const scope = `${config.apiBase}:${config.agentId}`
  const key = `speda-project-navigation:${scope}`
  const [restored, setRestored] = useState<string|null>(null)
  const restoring = useRef<string|null>(null)
  useEffect(() => {
    if (!state.sessionsLoaded || restoring.current === scope) return
    if (state.config?.agentId !== config.agentId || state.config?.apiBase !== config.apiBase) return
    restoring.current = scope
    let alive = true
    const epoch = live.current.viewEpoch
    const restore = async () => {
      try {
        const saved = JSON.parse(localStorage.getItem(key) || 'null')
        if (!saved) return
        const session = state.sessions.find(s=>s.id===saved.sessionId)
        if (session && alive && live.current.viewEpoch===epoch) {
          await select(session.id, session.project_id ?? null)
        } else if (saved.sessionId == null && Number.isInteger(saved.projectId)) {
          const projects = await fetchProjects(config, true)
          if (alive && live.current.viewEpoch===epoch && projects.some(p=>p.id===saved.projectId && !p.archived)) {
            dispatch({type:'NEW_CHAT', payload:{projectId:saved.projectId}})
          }
        }
      } catch { /* Refused/offline selections never fabricate a folder binding. */ }
      finally { if (alive) setRestored(scope) }
    }
    void restore()
    return () => { alive=false; if (restoring.current===scope) restoring.current=null }
  }, [scope, state.sessionsLoaded, state.config?.agentId, state.config?.apiBase])
  useEffect(() => {
    if (restored!==scope || !state.sessionsLoaded || state.config?.agentId!==config.agentId) return
    try { localStorage.setItem(key,JSON.stringify({sessionId:state.activeSessionId, projectId:state.activeProjectId})) }
    catch { /* Selection persistence is optional on devices with storage disabled. */ }
  }, [restored,scope,key,state.sessionsLoaded,state.activeSessionId,state.activeProjectId])
}
