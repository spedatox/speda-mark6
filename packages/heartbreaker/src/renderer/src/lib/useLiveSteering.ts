import { useCallback, useRef } from 'react'
import { newRequestId, steerRun } from './api'
import { useChatContext } from '../store/chat'
import type { AppConfig } from './types'

export interface SteeringTarget { requestId: string; sessionId: number | null; stepId?: string }
export interface DraftIdentity { revision: number; sessionId: number | null; agentId: string }
export function mayClearSteeringDraft(submitted: DraftIdentity, current: DraftIdentity) {
  return submitted.revision === current.revision && submitted.sessionId === current.sessionId && submitted.agentId === current.agentId
}

/** Acknowledgment belongs to its original turn, even after the owner changes views. */
export function useLiveSteering(config: AppConfig, getTarget: () => SteeringTarget | null) {
  const { state, dispatch } = useChatContext()
  const latest = useRef({ state, config, getTarget })
  latest.current = { state, config, getTarget }
  const pending = useRef<{ key: string; id: string; promise?: Promise<boolean> } | null>(null)
  return useCallback((text: string): Promise<boolean> => {
    const source = latest.current
    const target = source.getTarget()
    if (!target || target.sessionId !== source.state.activeSessionId || !text.trim()) return Promise.resolve(false)
    const key = JSON.stringify([target.requestId, target.stepId, target.sessionId, text])
    if (pending.current?.key !== key) pending.current = { key, id: newRequestId() }
    const item = pending.current
    if (item.promise) return item.promise
    item.promise = (async () => {
      const accepted = await steerRun(source.config, target.requestId, text, item.id, target.stepId)
      const current = latest.current
      if (accepted && current.state.activeSessionId === target.sessionId
          && current.config.apiBase === source.config.apiBase && current.config.agentId === source.config.agentId
          && current.config.apiKey === source.config.apiKey) {
        dispatch({ type:'ADD_USER_MESSAGE', payload:{id:`steering:${item.id}`,role:'user',content:text,
          tools:[],isStreaming:false,isError:false,sessionId:target.sessionId ?? undefined} })
      }
      if (accepted && pending.current === item) pending.current = null
      else item.promise = undefined // Unknown acknowledgment retries retain the same ID.
      return accepted
    })()
    return item.promise
  }, [dispatch])
}
