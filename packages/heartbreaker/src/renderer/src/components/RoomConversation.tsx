import FormSelect from './FormSelect'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useChatContext } from '../store/chat'
import { changeRoomMember, fetchRoomEvents, fetchSessionRoom, canonicalRoomAgent } from '../lib/rooms'
import { BRANDS } from '../profile/brands'
import { ROSTER } from '../lib/agents'
import { useT } from '../lib/i18n'
import type { AppConfig, ChatMessage } from '../lib/types'
import AgentMark from './AgentMark'

export const roomName = (id: string) => BRANDS[id]?.name ?? id

export default function RoomConversation({ config, party, reply, onClearReply, children }: {
  config: AppConfig; party: boolean; engaged?: boolean
  reply: ChatMessage | null; onClearReply: () => void; children: ReactNode
}) {
  const { state, dispatch } = useChatContext()
  const t = useT().room
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('')
  const [controlsOpen, setControlsOpen] = useState(false)
  const scope = `${config.apiBase}\n${config.apiKey}\n${config.agentId}\n${state.activeSessionId}`
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const room = state.room
  const sid = state.activeSessionId

  useEffect(() => { setControlsOpen(false) }, [scope])
  useEffect(() => {
    const open = () => { if (!party && sid != null) setControlsOpen(true) }
    window.addEventListener('speda:room-actions', open)
    return () => window.removeEventListener('speda:room-actions', open)
  }, [party, sid])
  useEffect(() => {
    if (!controlsOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setControlsOpen(false) }
    window.addEventListener('keydown', close)
    return () => { window.removeEventListener('keydown', close); document.querySelector<HTMLButtonElement>('[data-room-actions]')?.focus() }
  }, [controlsOpen])

  useEffect(() => {
    setError(''); setConnection(''); setBusy(false); setInvite('')
    if (sid == null) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    let roomId: string | undefined
    const load = async () => {
      try {
        if (!roomId) {
          const found = await fetchSessionRoom(config, sid, controller.signal)
          if (controller.signal.aborted) return
          if (!found) { setConnection(t.unbound); return }
          roomId = found.id
        }
        let more = true
        while (more && !controller.signal.aborted) {
          const page = await fetchRoomEvents(config, roomId, cursor, controller.signal)
          if (controller.signal.aborted) return
          dispatch({ type: 'SET_ROOM', payload: { room: page.room, sessionId: sid } })
          for (const event of page.events) if (event.kind === 'membership') {
            dispatch({ type: 'ROOM_MEMBERSHIP', payload: { event, sessionId: sid } })
          }
          cursor = page.next_cursor
          more = page.events.length === 200
        }
        setConnection('')
      } catch (cause) {
        if (!controller.signal.aborted) setConnection(cause instanceof Error ? cause.message : t.disconnected)
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 2500)
      }
    }
    void load()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  }, [scope, dispatch, t.unbound, t.disconnected])

  const change = async (action: 'invite' | 'dismiss' | 'handoff', id: string) => {
    if (!room || sid == null || busy || !id) return
    setBusy(true); setError('')
    try {
      const next = await changeRoomMember(config, room, action, id)
      if (scopeRef.current !== scope) return
      dispatch({ type: 'SET_ROOM', payload: { room: next, sessionId: sid } })
      if (action === 'dismiss' && reply?.speaker === id) onClearReply()
      setInvite('')
      if (action === 'invite') setControlsOpen(false)
      // A confirmed membership receipt is followed by its durable notice, never an optimistic join.
      const page = await fetchRoomEvents(config, room.id, Math.max(0, next.sequence - 1))
      if (scopeRef.current !== scope) return
      for (const event of page.events) if (event.kind === 'membership')
        dispatch({ type: 'ROOM_MEMBERSHIP', payload: { event, sessionId: sid } })
    } catch (cause) {
      if (scopeRef.current === scope) setError(cause instanceof Error ? cause.message : t.failed)
    } finally { if (scopeRef.current === scope) setBusy(false) }
  }
  const members = room?.members ?? []
  const speaking = [...state.messages].reverse().find(message => message.isStreaming && message.speaker)?.speaker
  const choices = ROSTER.map(canonicalRoomAgent).filter(id => !members.includes(id))
  return <>
    {controlsOpen && createPortal(<div className="hb-room-overlay" onClick={() => setControlsOpen(false)}>
      <section className="hb-room-picker" role="dialog" aria-modal="true" aria-label={t.summonWho} onClick={event => event.stopPropagation()}>
      <div className="hb-room-picker-title"><strong>{t.summonWho}</strong><button aria-label={t.close} onClick={() => setControlsOpen(false)}>×</button></div>
      <div className="hb-room-invite">
        <FormSelect aria-label={t.summonWho} value={invite} onValueChange={value => setInvite(value)}
          autoFocus
          disabled={!room || busy || state.isStreaming || !choices.length}>
          <option value="">{t.choose}</option>{choices.map(id => <option key={id} value={id}>{roomName(id)}</option>)}
        </FormSelect>
        <button className="hb-btn glass-round" disabled={!invite || !room || busy || state.isStreaming}
          onClick={() => void change('invite', invite)}>{busy ? t.summoning : t.summon}</button>
      </div>
      {!room && <p className="hb-room-caption">{t.unbound}</p>}
      {error && <p className="hb-room-error" role="alert">{error}</p>}
      </section></div>, document.body)}
    {!party && members.length > 1 && <div className="hb-room-members" aria-label={t.members}>
      {members.map(id => <span className="hb-room-member" key={id}><AgentMark agentId={id} size={20} finish="flat" />
        <span lang="en">{roomName(id)}</span>{id === room?.lead && <small>{t.leadLabel}</small>}
        {id !== room?.lead && <button disabled={busy || state.isStreaming} aria-label={t.dismiss(roomName(id))}
          onClick={() => void change('dismiss', id)}>×</button>}
      </span>)}
    </div>}
    <div className="hb-room-layout"><div className="hb-room-transcript">{children}</div>
      {party && <aside className="hb-room-party" aria-label={t.members}>
        <div className="hb-room-party-heading">{t.fullRoster}</div>
        {!room && <p className="hb-room-caption">{t.startFirst}</p>}
        {members.map(id => <div className="hb-room-player" key={id}><AgentMark agentId={id} size={30} />
          <div><strong lang="en">{roomName(id)}</strong><div className="hb-room-caption">
            {speaking === id ? t.speaking : id === room?.lead ? t.leadLabel : t.inChat}</div></div></div>)}
      </aside>}
    </div>
    {room && reply && <div className="hb-room-reply-context">
      <span>{t.replying(roomName(reply.speaker ?? room.lead))}</span>
      <button aria-label={t.clearReply} onClick={onClearReply}>×</button>
    </div>}
    {(error || connection) && <div className="hb-room-error" role={error ? 'alert' : 'status'}>{error || connection}</div>}
  </>
}
