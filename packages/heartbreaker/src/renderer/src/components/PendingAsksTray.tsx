import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchPendingAsks, answerAsk } from '../lib/api'
import { PermissionPrompt } from './InteractionPrompt'
import type { AppConfig, PendingAsk } from '../lib/types'

/**
 * Global tray for irreversible operations a Forge peer (Optimus, Centurion) is
 * waiting on the owner to approve.
 *
 * The inline card in ChatMain only fires on `permission_request` SSE frames,
 * which a peer raises only when it tags the ask with a chat_id — and the Forge's
 * peer oracle attaches none, so a dispatched or background job's ask never
 * reaches that path. GET /agents/asks is the guaranteed surface, and nothing
 * polled it before this. Mounted once at the app root (not per chat view) so an
 * ask surfaces no matter which agent is selected or whether a chat is open.
 *
 * The endpoint is agent-agnostic — every open ask carries its own `agent_id`, so
 * this one tray covers the whole external roster at once. Answering is relayed
 * to the peer by Igor (POST /agents/asks/{id}); the peer runs its own countdown
 * and denies locally if the owner never answers, so a missed card is a "no".
 */

const POLL_MS = 3000

export default function PendingAsksTray({ config }: { config: AppConfig }) {
  const [asks, setAsks] = useState<PendingAsk[]>([])
  // ask_ids the owner just answered — hidden immediately so a click feels
  // instant, and kept out of the list until the backend stops returning them.
  const answeredRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    let live = true
    const poll = async () => {
      const open = await fetchPendingAsks(config)
      if (!live) return
      const answered = answeredRef.current
      // Drop ids the server no longer lists — they can't come back, so stop
      // suppressing them and keep the set from growing without bound.
      const stillOpen = new Set(open.map(a => a.ask_id))
      for (const id of [...answered]) if (!stillOpen.has(id)) answered.delete(id)
      setAsks(open.filter(a => !answered.has(a.ask_id)))
    }
    poll()
    const t = setInterval(poll, POLL_MS)
    return () => { live = false; clearInterval(t) }
  }, [config])

  const resolve = useCallback(async (ask: PendingAsk, approved: boolean, remember: boolean) => {
    answeredRef.current.add(ask.ask_id)
    setAsks(prev => prev.filter(a => a.ask_id !== ask.ask_id))
    await answerAsk(config, ask.ask_id, approved, remember)
  }, [config])

  if (asks.length === 0) return null

  return (
    <div style={{
      position: 'fixed', top: '4.5rem', right: '1rem',
      width: 'min(440px, calc(100vw - 2rem))',
      display: 'flex', flexDirection: 'column', gap: '0.6rem',
      maxHeight: 'calc(100vh - 6rem)', overflowY: 'auto',
      zIndex: 60, pointerEvents: 'auto',
    }}>
      {asks.map(ask => (
        <PermissionPrompt
          key={ask.ask_id}
          ask={ask}
          embedded
          onResolve={(approved, remember) => resolve(ask, approved, remember)}
        />
      ))}
    </div>
  )
}
