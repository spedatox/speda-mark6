import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchAgentComms, fetchAgentModels, fetchModels, pinAgentModel, setHouseParty, streamChat,
} from '../lib/api'
import type { AgentCommEntry, AgentModelInfo } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'
import { ROSTER, agentColor, fmtCommTime } from '../lib/agents'
import { useIsMobile } from '../lib/useIsMobile'
import AgentModelPicker from './AgentModelPicker'
import { Avatar, Bubble, CommMarkdown, CopyBtn } from './CommBubble'

/**
 * HOUSE PARTY PROTOCOL — the war room.
 *
 * Full-screen group chat that the UI transforms into while the protocol is
 * engaged (owner-voice-activated through SPEDA; the Layout polls the flag and
 * mounts this automatically). Left rail = the roster with live WORKING/STANDBY
 * status; main = the agent network channel rendered as a group chat — SPEDA the
 * commander on the right, operatives on the left, every bubble in its agent's
 * signature color. Data source: GET /agents/comms (same log the tray reads).
 */

const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"
const POLL_MS = 2500

/** Owner / SPEDA direct exchange in the war room (local to this view; the
 *  owner speaks from the right, everyone else from the left). */
interface LocalMsg {
  id: string
  who: 'owner' | 'speda'
  text: string
  at: number
  live?: boolean
}

function LocalBubble({ m }: { m: LocalMsg }) {
  const owner = m.who === 'owner'
  const c = owner ? '#f2b75c' : agentColor('speda')
  return (
    <div style={{
      display: 'flex', gap: 8, padding: '0.3rem 0',
      flexDirection: owner ? 'row-reverse' : 'row',
      animation: 'hbRise 0.3s ease both',
    }}>
      {owner ? (
        <span style={{
          width: 26, height: 26, flexShrink: 0, borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: `1px solid ${c}88`, background: `${c}1f`, color: c,
          fontFamily: UI, fontWeight: 700, fontSize: 10, letterSpacing: '0.06em',
          boxShadow: `inset 0 1px 0 0 rgba(255,255,255,0.14), 0 0 8px ${c}33`,
        }}>SIR</span>
      ) : (
        <Avatar id="speda" />
      )}
      <div className="hb-glass-sm" style={{
        maxWidth: 'min(72%, 640px)', padding: '0.45rem 0.6rem 0.5rem',
        border: `1px solid ${c}44`, background: `${c}0d`,
        backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 7, marginBottom: 3,
          fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.08em',
        }}>
          <span style={{ color: c, fontWeight: 700 }}>{owner ? 'OWNER' : 'SPEDA'}</span>
          <span style={{ color: 'var(--hb-icon-dim)' }}>{fmtCommTime(new Date(m.at).toISOString())}</span>
          <span style={{ flex: 1 }} />
          {!m.live && m.text && <CopyBtn text={m.text} tint={c} />}
        </div>
        {m.live ? (
          // Streaming: plain text (partial markdown renders broken); selectable.
          <p style={{
            margin: 0, fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '0.76rem', lineHeight: 1.45, color: 'var(--hb-text-dim)',
            whiteSpace: 'pre-wrap', userSelect: 'text',
          }}>
            {m.text}
            <span style={{ animation: 'hbBlink 1.1s ease-in-out infinite', color: c }}>▌</span>
          </p>
        ) : (
          <CommMarkdown text={m.text || '…'} size="0.76rem" />
        )}
      </div>
    </div>
  )
}

export default function HousePartyBoard({ config, onMinimize, onStoodDown }: {
  config: AppConfig
  onMinimize: () => void
  onStoodDown: () => void
}) {
  const isMobile = useIsMobile()
  const [entries, setEntries] = useState<AgentCommEntry[]>([])
  const feedRef = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)  // stick to the newest message unless the user scrolled up

  // Owner composer: messages go straight to SPEDA (the commander), which plans
  // and dispatches — its dispatches then stream into the feed via the comms poll.
  const [localMsgs, setLocalMsgs] = useState<LocalMsg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const warSession = useRef<number | null>(null)

  // Per-agent model routing
  const [agentInfos, setAgentInfos] = useState<AgentModelInfo[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])

  useEffect(() => {
    const load = () => fetchAgentComms(config, 150).then(rows => setEntries(rows.slice().reverse()))
    load()
    fetchAgentModels(config).then(setAgentInfos)
    fetchModels(config).then(setModels).catch(() => {})
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [config])

  const send = async () => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    const at = Date.now()
    const spedaId = `speda-${at}`
    setLocalMsgs(ms => [
      ...ms,
      { id: `owner-${at}`, who: 'owner', text, at },
      { id: spedaId, who: 'speda', text: '', at: at + 1, live: true },
    ])
    pinned.current = true
    try {
      const ctrl = new AbortController()
      // Always address SPEDA here, whatever agent the main chat is set to.
      for await (const ev of streamChat(text, warSession.current, { ...config, agentId: 'speda' }, ctrl.signal)) {
        if (ev.session_id) warSession.current = ev.session_id
        if (ev.type === 'chunk' && typeof ev.data === 'string') {
          const delta = ev.data
          setLocalMsgs(ms => ms.map(m => m.id === spedaId ? { ...m, text: m.text + delta } : m))
        } else if (ev.type === 'error') {
          const err = `\n[ERROR] ${ev.data}`
          setLocalMsgs(ms => ms.map(m => m.id === spedaId ? { ...m, text: m.text + err } : m))
        }
      }
    } catch (e) {
      const err = `\n[LINK ERROR] ${e instanceof Error ? e.message : String(e)}`
      setLocalMsgs(ms => ms.map(m => m.id === spedaId ? { ...m, text: m.text + err } : m))
    } finally {
      setLocalMsgs(ms => ms.map(m => m.id === spedaId ? { ...m, live: false } : m))
      setSending(false)
    }
  }

  const pin = async (agentId: string, model: string | null) => {
    const infos = await pinAgentModel(config, agentId, model)
    if (infos.length) setAgentInfos(infos)
  }

  // Merge dispatch traffic and the local owner↔SPEDA exchange chronologically.
  const feed = useMemo(() => {
    const items: { at: number; node: React.ReactNode }[] = [
      ...entries.map(e => ({
        at: new Date(e.created_at.endsWith('Z') || e.created_at.includes('+') ? e.created_at : e.created_at + 'Z').getTime(),
        node: <Bubble key={`c${e.id}`} e={e} mine={false} />,
      })),
      ...localMsgs.map(m => ({ at: m.at, node: <LocalBubble key={m.id} m={m} /> })),
    ]
    return items.sort((a, b) => a.at - b.at).map(i => i.node)
  }, [entries, localMsgs])

  useEffect(() => {
    const el = feedRef.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }, [entries, localMsgs])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onMinimize() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onMinimize])

  const working = useMemo(() => {
    const w = new Set<string>()
    for (const e of entries) if (e.status === 'running') w.add(e.to_agent)
    return w
  }, [entries])

  const doneCount = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of entries) if (e.status === 'ok') c[e.to_agent] = (c[e.to_agent] ?? 0) + 1
    return c
  }, [entries])

  const standDown = async () => {
    await setHouseParty(config, false)
    onStoodDown()
  }

  return (
    <div style={{
      position: 'fixed', top: 22, bottom: 4, left: 0, right: 0, zIndex: 520,
      display: 'flex', flexDirection: 'column', gap: 8, padding: 10,
      background: 'rgba(4, 9, 12, 0.55)',
      backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
      animation: 'fadeIn 0.2s ease',
    }}>
      {/* Title plate */}
      <div className="hb-head-light" style={{ minHeight: 0, gap: '0.7rem' }}>
        <span style={{
          width: 7, height: 7, borderRadius: '50%', background: 'var(--hb-amber)',
          boxShadow: '0 0 8px rgba(242,183,92,0.9)', animation: 'hbBlink 1.6s ease-in-out infinite',
        }} />
        <span style={{ fontSize: '0.82rem' }}>HOUSE PARTY PROTOCOL // WAR ROOM</span>
        <span className="hb-hide-sm" style={{
          fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.1em',
          color: 'var(--hb-icon)', textTransform: 'none',
        }}>
          ALL HANDS · FULL GRADE
        </span>
        <span style={{ flex: 1 }} />
        <button
          onClick={standDown}
          title="End the protocol — dispatches return to the background tier"
          style={{
            height: 22, padding: '0 0.6rem',
            border: '1px solid rgba(200,74,58,0.6)', background: 'rgba(120,40,32,0.18)',
            color: '#e8a196', cursor: 'pointer',
            fontFamily: UI, fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.16em',
            transition: 'all 0.12s',
          }}
        >
          STAND DOWN
        </button>
        <button
          onClick={onMinimize}
          title="Minimize — the protocol stays engaged (Esc)"
          style={{
            border: 'none', background: 'transparent', cursor: 'pointer',
            color: 'var(--hb-icon-dim)', display: 'flex', alignItems: 'center', padding: '0 2px',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>

      <div style={{ flex: 1, display: 'flex', gap: 8, minHeight: 0 }}>
        {/* Roster rail */}
        {!isMobile && (
          <section className="hb-holo" style={{ width: 196, flexShrink: 0, overflowY: 'auto' }}>
            <header className="hb-head-glass" style={{ flexShrink: 0 }}>ROSTER</header>
            {ROSTER.map(id => {
              const busy = working.has(id)
              const info = agentInfos.find(a => a.agent_id === id)
              return (
                <div key={id} style={{
                  display: 'flex', flexDirection: 'column', gap: 4,
                  padding: '0.4rem 0.55rem',
                  borderLeft: `2px solid ${busy ? agentColor(id) : 'transparent'}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Avatar id={id} size={24} />
                    <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                      <span style={{
                        fontFamily: UI, fontSize: '0.68rem', fontWeight: 700,
                        letterSpacing: '0.1em', color: 'var(--hb-text-dim)', textTransform: 'uppercase',
                      }}>
                        {id}
                      </span>
                      <span style={{
                        fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.1em',
                        color: busy ? 'var(--hb-amber)' : 'var(--hb-icon-dim)',
                      }}>
                        {busy ? 'WORKING…' : `STANDBY${doneCount[id] ? ` · ${doneCount[id]} DONE` : ''}`}
                      </span>
                    </span>
                  </div>
                  {info && (
                    <AgentModelPicker info={info} models={models} onPin={m => pin(id, m)} />
                  )}
                </div>
              )
            })}
          </section>
        )}

        {/* Group chat feed */}
        <section className="hb-holo" style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <header className="hb-head-glass" style={{ flexShrink: 0, justifyContent: 'space-between' }}>
            <span>AGENT NETWORK // GROUP CHANNEL</span>
            <span style={{ fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.08em', textTransform: 'none', color: 'var(--hb-icon)' }}>
              {working.size > 0 ? `${working.size} WORKING` : `${entries.length} MESSAGES`}
            </span>
          </header>
          <div
            ref={feedRef}
            onScroll={() => {
              const el = feedRef.current
              if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
            }}
            style={{ flex: 1, overflowY: 'auto', padding: '0.5rem 0.75rem', minHeight: 0 }}
          >
            {feed.length === 0 ? (
              <p style={{
                fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.14em',
                color: 'var(--hb-icon-dim)', padding: '0.6rem 0',
              }}>
                // CHANNEL OPEN — GIVE SPEDA THE OBJECTIVE BELOW
              </p>
            ) : feed}
          </div>

          {/* Owner composer — straight line to the commander */}
          <div style={{
            flexShrink: 0, display: 'flex', gap: 8, alignItems: 'center',
            padding: '0.5rem 0.6rem',
            borderTop: '1px solid var(--hb-edge)',
          }}>
            <span style={{
              fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.12em',
              color: sending ? 'var(--hb-amber)' : 'var(--hb-cyan)', flexShrink: 0,
            }}>
              {sending ? 'SPEDA▸' : 'OWNER▸'}
            </span>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              placeholder={sending ? 'SPEDA is commanding the roster…' : 'Give SPEDA the objective — it plans and dispatches the roster'}
              disabled={sending}
              autoFocus
              style={{
                flex: 1, height: 30, padding: '0 0.6rem',
                border: '1px solid var(--hb-line)', background: 'rgba(8, 15, 23, 0.5)',
                color: 'var(--hb-text-dim)', outline: 'none',
                fontFamily: "'SamsungOne','Inter',sans-serif", fontSize: '0.78rem',
              }}
            />
            <button
              onClick={send}
              disabled={sending || !input.trim()}
              title="Send to SPEDA (Enter)"
              style={{
                height: 30, padding: '0 0.8rem', flexShrink: 0,
                border: `1px solid ${sending || !input.trim() ? 'var(--hb-line)' : 'rgba(242,183,92,0.6)'}`,
                background: sending || !input.trim() ? 'transparent' : 'rgba(217,156,68,0.14)',
                color: sending || !input.trim() ? 'var(--hb-icon-dim)' : 'var(--hb-amber-bright)',
                cursor: sending || !input.trim() ? 'default' : 'pointer',
                fontFamily: UI, fontSize: '0.64rem', fontWeight: 700, letterSpacing: '0.16em',
                transition: 'all 0.12s',
              }}
            >
              DISPATCH
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
