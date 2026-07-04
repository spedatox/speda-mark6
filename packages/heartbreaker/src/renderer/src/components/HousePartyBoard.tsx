import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import {
  deleteSession, fetchAgentComms, fetchAgentModels, fetchMessages, fetchModels,
  fetchSessions, pinAgentModel, setHouseParty,
} from '../lib/api'
import type { AgentCommEntry, AgentModelInfo } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'
import { WARROOM_PROFILE } from '../profile/warroom'
import { ROSTER, agentColor } from '../lib/agents'
import { useIsMobile } from '../lib/useIsMobile'
import { ChatContext, chatReducer, initialState } from '../store/chat'
import { ProfileContext } from './Sidebar'
import AgentModelPicker from './AgentModelPicker'
import ChatMain from './ChatMain'
import { Avatar } from './CommBubble'

/**
 * THE WAR ROOM — review board (protocol OFFLINE).
 *
 * Opened from the header's WAR ROOM button while the protocol is NOT engaged:
 * past operations, roster model pins, and a command channel to the warroom
 * agent. When the protocol IS engaged, the whole app transforms into the
 * war-room profile instead (App.tsx takeover) and this board stays closed —
 * the takeover, not this overlay, is the House Party experience.
 * The main pane IS the real chat stack — ChatMain
 * with streaming, tool badges, attachments, files, regenerate/edit/delete —
 * addressed to the backend "warroom" profile (SPEDA's brain behind a separate
 * agent_id), so the war room keeps its own conversation story exactly like any
 * agent on the menu. Left rail = the roster with live WORKING/STANDBY status
 * (from GET /agents/comms, house_party traffic only) plus the OPERATIONS log —
 * the war room's own session history. Chrome colors loop through the whole
 * roster's signature palette (hbPartyCycle).
 */

const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"
const POLL_MS = 2500

/** Survives minimize/reopen within the app session — reopening the war room
 *  restores the operation that was on screen. Cleared on STAND DOWN. */
let lastOpSession: number | null = null

export default function HousePartyBoard({ config, engaged, onMinimize, onStoodDown }: {
  config: AppConfig
  /** Whether the House Party Protocol is live. False = review/standby mode:
   *  the full command-center chat and OPERATIONS history still work (it is
   *  just the warroom agent), but there is nothing to stand down. */
  engaged: boolean
  onMinimize: () => void
  onStoodDown: () => void
}) {
  const isMobile = useIsMobile()

  // ── The war room's own chat store — a full ChatMain stack scoped to the
  //    "warroom" agent, fully independent of the main chat's state. ──────────
  const [chat, chatDispatch] = useReducer(chatReducer, initialState)
  const warConfig = useMemo<AppConfig>(() => ({ ...config, agentId: 'warroom' }), [config])

  const selectOp = useCallback(async (sessionId: number) => {
    chatDispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: [] } })
    try {
      const messages = await fetchMessages(warConfig, sessionId)
      chatDispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages } })
    } catch { /* keep empty state on error */ }
  }, [warConfig])

  const newOp = useCallback(() => {
    lastOpSession = null
    chatDispatch({ type: 'NEW_CHAT' })
  }, [])

  const deleteOp = useCallback(async (sessionId: number) => {
    await deleteSession(warConfig, sessionId)
    if (lastOpSession === sessionId) lastOpSession = null
    chatDispatch({ type: 'DELETE_SESSION', payload: { id: sessionId } })
  }, [warConfig])

  useEffect(() => {
    chatDispatch({ type: 'SET_CONFIG', payload: warConfig })
    fetchSessions(warConfig).then(s => chatDispatch({ type: 'SET_SESSIONS', payload: s })).catch(() => {})
    if (lastOpSession != null) selectOp(lastOpSession)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warConfig])

  // Remember the on-screen operation so minimize → reopen restores it.
  useEffect(() => {
    if (chat.activeSessionId != null) lastOpSession = chat.activeSessionId
  }, [chat.activeSessionId])

  // ── Roster status — engagement traffic only ───────────────────────────────
  const [entries, setEntries] = useState<AgentCommEntry[]>([])
  const [agentInfos, setAgentInfos] = useState<AgentModelInfo[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])

  useEffect(() => {
    const load = () => fetchAgentComms(config, 150).then(setEntries)
    load()
    fetchAgentModels(config).then(setAgentInfos)
    fetchModels(config).then(setModels).catch(() => {})
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [config])

  const partyEntries = useMemo(
    () => entries.filter(e => e.protocol === 'house_party'),
    [entries],
  )

  const working = useMemo(() => {
    const w = new Set<string>()
    for (const e of partyEntries) if (e.status === 'running') w.add(e.to_agent)
    return w
  }, [partyEntries])

  const doneCount = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of partyEntries) if (e.status === 'ok') c[e.to_agent] = (c[e.to_agent] ?? 0) + 1
    return c
  }, [partyEntries])

  const pin = async (agentId: string, model: string | null) => {
    const infos = await pinAgentModel(config, agentId, model)
    if (infos.length) setAgentInfos(infos)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onMinimize() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onMinimize])

  const standDown = async () => {
    await setHouseParty(config, false)
    lastOpSession = null
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
      {/* Title plate — chrome loops through the roster's colors */}
      <div className="hb-head-light" style={{ minHeight: 0, gap: '0.7rem' }}>
        <span className={engaged ? 'hb-party-cycle' : undefined} style={{
          width: 7, height: 7, borderRadius: '50%',
          background: engaged ? 'currentColor' : 'var(--hb-icon-dim)',
          boxShadow: engaged ? '0 0 8px currentColor' : 'none',
        }} />
        <span className="hb-party-cycle" style={{ fontSize: '0.82rem' }}>HOUSE PARTY PROTOCOL // WAR ROOM</span>
        <span className="hb-hide-sm" style={{
          fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.1em',
          color: 'var(--hb-icon)', textTransform: 'none',
        }}>
          {engaged ? 'ALL HANDS · FULL GRADE' : 'REVIEW // PROTOCOL OFFLINE'}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{
          fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.1em', textTransform: 'none',
          color: working.size > 0 ? 'var(--hb-amber)' : 'var(--hb-icon)',
        }}>
          {working.size > 0
            ? `${working.size} WORKING`
            : engaged ? 'CHANNEL OPEN' : 'STANDBY'}
        </span>
        {engaged && (
          <button
            className="hb-btn hb-btn-tint"
            onClick={standDown}
            title="End the protocol — dispatches return to the background tier"
            style={{
              height: 22, padding: '0 0.6rem', color: '#e8a196',
              fontFamily: UI, fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.16em',
            }}
          >
            STAND DOWN
          </button>
        )}
        <button
          onClick={onMinimize}
          title={engaged ? 'Minimize — the protocol stays engaged (Esc)' : 'Close the war room (Esc)'}
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

      {/* Mobile roster strip — the rail is desktop-only, but status must not vanish */}
      {isMobile && (
        <div className="hb-holo" style={{
          display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
          padding: '0.35rem 0.6rem', overflowX: 'auto',
        }}>
          {ROSTER.map(id => {
            const busy = working.has(id)
            return (
              <span
                key={id}
                title={`${id.toUpperCase()} — ${busy ? 'working' : 'standby'}${doneCount[id] ? ` · ${doneCount[id]} done` : ''}`}
                style={{ position: 'relative', flexShrink: 0, opacity: busy ? 1 : 0.55 }}
              >
                <Avatar id={id} size={24} />
                <span style={{
                  position: 'absolute', right: -1, bottom: -1, width: 7, height: 7, borderRadius: '50%',
                  background: busy ? 'var(--hb-amber)' : 'var(--hb-icon-dim)',
                  boxShadow: busy ? '0 0 6px rgba(242,183,92,0.9)' : 'none',
                  border: '1px solid rgba(4,9,12,0.8)',
                  animation: busy ? 'hbBlink 1.6s ease-in-out infinite' : 'none',
                }} />
              </span>
            )
          })}
        </div>
      )}

      <div style={{ flex: 1, display: 'flex', gap: 8, minHeight: 0 }}>
        {/* Left rail: roster status + the war room's own conversation story */}
        {!isMobile && (
          <div style={{ width: 208, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
            <section className="hb-holo" style={{ flexShrink: 0, maxHeight: '55%', overflowY: 'auto' }}>
              <header className="hb-head-glass hb-party-cycle" style={{ flexShrink: 0 }}>ROSTER</header>
              {ROSTER.map((id, i) => {
                const busy = working.has(id)
                const info = agentInfos.find(a => a.agent_id === id)
                return (
                  <div key={id} style={{
                    display: 'flex', flexDirection: 'column', gap: 4,
                    padding: '0.4rem 0.55rem',
                    borderTop: i > 0 ? '1px solid var(--hb-edge)' : 'none',
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

            {/* OPERATIONS — the war room's session history */}
            <section className="hb-holo" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <header className="hb-head-glass hb-party-cycle" style={{ flexShrink: 0, justifyContent: 'space-between' }}>
                <span>OPERATIONS</span>
                <button
                  onClick={newOp}
                  title="Start a new operation"
                  style={{
                    border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
                    fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.12em',
                    color: 'inherit',
                  }}
                >
                  NEW_
                </button>
              </header>
              <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
                {chat.sessions.length === 0 ? (
                  <p style={{
                    margin: 0, padding: '0.55rem 0.6rem',
                    fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.12em',
                    color: 'var(--hb-icon-dim)',
                  }}>
                    // NO PRIOR OPS
                  </p>
                ) : chat.sessions.map(s => {
                  const active = s.id === chat.activeSessionId
                  return (
                    <div key={s.id} style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '0.3rem 0.55rem',
                      borderLeft: `2px solid ${active ? 'var(--hb-amber)' : 'transparent'}`,
                      background: active ? 'rgba(217,156,68,0.08)' : 'transparent',
                    }}>
                      <button
                        onClick={() => selectOp(s.id)}
                        title={s.title || 'Untitled operation'}
                        style={{
                          flex: 1, minWidth: 0, border: 'none', background: 'transparent',
                          cursor: 'pointer', padding: 0, textAlign: 'left',
                          fontFamily: UI, fontSize: '0.66rem', fontWeight: active ? 700 : 500,
                          letterSpacing: '0.04em',
                          color: active ? 'var(--hb-amber-bright)' : 'var(--hb-text-dim)',
                          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                          transition: 'color 0.12s',
                        }}
                      >
                        {s.title || 'UNTITLED OP'}
                      </button>
                      <button
                        onClick={() => deleteOp(s.id)}
                        title="Delete this operation"
                        style={{
                          border: 'none', background: 'transparent', cursor: 'pointer',
                          padding: 0, display: 'flex', alignItems: 'center', flexShrink: 0,
                          color: 'var(--hb-icon-dim)',
                        }}
                      >
                        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
                    </div>
                  )
                })}
              </div>
            </section>
          </div>
        )}

        {/* Command channel — the REAL chat stack, scoped to the warroom agent */}
        <ChatContext.Provider value={{ state: chat, dispatch: chatDispatch }}>
          <ProfileContext.Provider value={WARROOM_PROFILE}>
            <section className="hb-holo hb-party-rim" style={{
              flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden',
            }}>
              <ChatMain config={warConfig} onSelectSession={selectOp} />
            </section>
          </ProfileContext.Provider>
        </ChatContext.Provider>
      </div>
    </div>
  )
}
