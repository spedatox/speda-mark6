import { useEffect, useRef, useState } from 'react'
import { attachSessionWorkers, snapshotSessionWorkers } from './api'
import type { AppConfig, SubagentRun } from './types'
import { useChatContext } from '../store/chat'
import { foldLegionEvent } from './subagentFold'
import { boundedWorkerRun, mergeWorkerRun } from './workerState'
import { WorkerTransportError } from './workerTransport'

type Card = { parent_step_id: string; parent_message_id?: string | null; run: SubagentRun; allowAnchor?: boolean }
export type WorkerFeedStatus = 'connecting' | 'connected' | 'reconnecting' | 'unavailable'

/** One subscription per selected session, recovered from a durable snapshot. */
export function useWorkerStreams(config: AppConfig) {
  const { state, dispatch } = useChatContext()
  const latest = useRef(state)
  latest.current = state
  const flushPending = useRef<() => void>(() => {})
  const [feed, setFeed] = useState<{ status: WorkerFeedStatus; error?: string }>({ status: 'connecting' })
  const sessionId = state.activeSessionId
  const hasBinding = state.messages.some(message => message.coordinationStepId)
  useEffect(() => { flushPending.current() }, [state.messages])
  useEffect(() => {
    if (!sessionId) return
    const controller = new AbortController()
    const cache = new Map<string, Card>()
    const pending = new Map<string, Card>()
    let batch: ReturnType<typeof setTimeout> | undefined
    const current = () => !controller.signal.aborted && latest.current.activeSessionId === sessionId
    const flush = () => {
      clearTimeout(batch)
      batch = undefined
      if (!current()) return
      for (const [id, card] of pending) {
        const parent = latest.current.messages.find(message => message.id === card.parent_message_id
          || message.coordinationStepId === card.parent_step_id
          || message.subagents?.some(run => (run.executionId || run.id) === id))
        if (!parent && !card.allowAnchor) continue // History can still be loading. Retain the card.
        dispatch({ type: 'WORKER_SNAPSHOT', payload: { id: parent?.id ?? `worker-parent:${card.parent_step_id}`,
          sessionId, run: card.run, parentStepId: card.parent_step_id, create: !parent && card.allowAnchor } })
        pending.delete(id)
      }
    }
    flushPending.current = flush
    const queue = (card: Card) => {
      const id = card.run.executionId || card.run.id
      const visible = latest.current.messages.flatMap(message => message.subagents ?? []).find(run => (run.executionId || run.id) === id)
      const prior = cache.get(id)
      card.run = boundedWorkerRun(mergeWorkerRun(visible, mergeWorkerRun(prior?.run, card.run)))
      cache.delete(id)
      cache.set(id, card)
      pending.set(id, card)
      if (cache.size > 128) {
        const oldest = [...cache.keys()].find(key => !pending.has(key))
        if (oldest) cache.delete(oldest)
        else throw new WorkerTransportError('Worker activity needs a history reload before continuing', false)
      }
      if (batch === undefined) batch = setTimeout(flush, 16)
    }
    const wait = (milliseconds: number) => new Promise<void>(resolve => {
      const finish = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', finish); resolve() }
      const timer = setTimeout(finish, milliseconds)
      controller.signal.addEventListener('abort', finish, { once: true })
    })
    const watch = async () => {
      let delay = 1000
      while (current()) {
        const attempt = new AbortController()
        const abort = () => attempt.abort()
        controller.signal.addEventListener('abort', abort, { once: true })
        let timeout: ReturnType<typeof setTimeout>
        const activity = () => { clearTimeout(timeout); timeout = setTimeout(() => attempt.abort(), 30000) }
        activity()
        try {
          setFeed({ status: delay === 1000 ? 'connecting' : 'reconnecting' })
          const snapshot = await snapshotSessionWorkers(config, sessionId, attempt.signal)
          if (!current()) return
          if (attempt.signal.aborted) throw new DOMException('Aborted', 'AbortError')
          for (const card of snapshot.cards) queue({ ...card, allowAnchor: !card.parent_message_id })
          flush()
          let cursor = snapshot.sequence
          setFeed({ status: 'connected', error: snapshot.omitted ? 'Some older assignments are omitted from this preview.' : undefined })
          for await (const event of attachSessionWorkers(config, sessionId, attempt.signal, cursor, activity)) {
            if (!current()) return
            const id = String(event.execution_id || event.id || '')
            const step = String(event.parent_step_id || cache.get(id)?.parent_step_id || '')
            const sequence = Number(event.sequence)
            if (!/^[a-f0-9]{32}$/.test(id) || !/^[a-f0-9]{32}$/.test(step) || !Number.isSafeInteger(sequence) || sequence <= 0) {
              throw new WorkerTransportError('Worker activity has invalid execution identity', false)
            }
            if (sequence <= cursor) continue
            const prior = cache.get(id)
            queue({ parent_step_id: step, parent_message_id: prior?.parent_message_id, allowAnchor: prior?.allowAnchor,
              run: foldLegionEvent(prior?.run ?? null, event) })
            cursor = sequence
            delay = 1000
          }
          flush()
        } catch (error) {
          if (!current()) return
          if (error instanceof WorkerTransportError && !error.retryable) {
            setFeed({ status: 'unavailable', error: error.message })
            return
          }
        } finally {
          clearTimeout(timeout!)
          controller.signal.removeEventListener('abort', abort)
          attempt.abort()
        }
        if (!current()) return
        setFeed({ status: 'reconnecting', error: 'Worker connection interrupted; recovering recorded activity.' })
        await wait(delay)
        delay = Math.min(delay * 2, 15000)
      }
    }
    void watch()
    return () => {
      controller.abort()
      clearTimeout(batch)
      pending.clear()
      cache.clear()
      flushPending.current = () => {}
    }
  }, [config, sessionId, hasBinding, dispatch])
  return feed
}
