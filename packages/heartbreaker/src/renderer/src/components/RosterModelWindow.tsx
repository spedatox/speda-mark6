import { useEffect, useRef, useState } from 'react'
import { fetchAgentModels, fetchModels, pinAgentModel } from '../lib/api'
import type { AgentModelInfo } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'
import { ROSTER } from '../lib/agents'
import { Avatar } from './CommBubble'
import AgentModelPicker from './AgentModelPicker'

const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"

/**
 * ROSTER CORES — a floating, draggable command window for configuring every
 * agent's model at once, surfaced from the war room. Each row pins one agent
 * to a specific model (or PROFILE = its own policy) via the same
 * AgentModelPicker used on the Systems board. Drag by the title bar; the
 * whole thing is a liquid-glass slab so it reads as part of the war-room
 * console rather than a stock modal.
 */
export default function RosterModelWindow({ config, onClose }: {
  config: AppConfig
  onClose: () => void
}) {
  const [infos, setInfos] = useState<AgentModelInfo[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])

  useEffect(() => {
    fetchAgentModels(config).then(setInfos).catch(() => {})
    fetchModels(config).then(setModels).catch(() => {})
  }, [config])

  const pin = async (agentId: string, model: string | null) => {
    const next = await pinAgentModel(config, agentId, model)
    if (next.length) setInfos(next)
  }

  // ── Drag ──────────────────────────────────────────────────────────────────
  const winRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ ox: number; oy: number } | null>(null)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

  const startDrag = (e: React.PointerEvent) => {
    const rect = winRef.current?.getBoundingClientRect()
    if (!rect) return
    dragRef.current = { ox: e.clientX - rect.left, oy: e.clientY - rect.top }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }
  const onMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return
    const w = winRef.current?.offsetWidth ?? 0
    const h = winRef.current?.offsetHeight ?? 0
    const x = Math.min(Math.max(0, e.clientX - dragRef.current.ox), window.innerWidth - w)
    const y = Math.min(Math.max(24, e.clientY - dragRef.current.oy), window.innerHeight - 40)
    setPos({ x, y })
  }
  const endDrag = (e: React.PointerEvent) => {
    dragRef.current = null
    try { (e.target as HTMLElement).releasePointerCapture(e.pointerId) } catch { /* noop */ }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      ref={winRef}
      className="hb-holo"
      style={{
        position: 'fixed', zIndex: 640,
        width: 430, maxWidth: '94vw',
        display: 'flex', flexDirection: 'column', maxHeight: '72vh',
        ...(pos
          ? { left: pos.x, top: pos.y }
          : { left: '50%', top: '18%', transform: 'translateX(-50%)' }),
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.28), 0 18px 60px rgba(0,0,0,0.6)',
        animation: 'modalIn 0.16s ease',
      }}
    >
      {/* Title bar — drag handle */}
      <header
        className="hb-head-glass hb-party-cycle"
        onPointerDown={startDrag}
        onPointerMove={onMove}
        onPointerUp={endDrag}
        style={{ cursor: 'grab', justifyContent: 'space-between', touchAction: 'none', flexShrink: 0 }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
          </svg>
          ROSTER CORES
        </span>
        <button
          onClick={onClose}
          title="Close (Esc)"
          style={{
            border: 'none', background: 'transparent', cursor: 'pointer', padding: 2,
            color: 'var(--hb-icon-dim)', display: 'flex',
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </header>

      <p style={{
        flexShrink: 0, margin: 0, padding: '0.4rem 0.7rem',
        fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.1em',
        color: 'var(--hb-icon)', borderBottom: '1px solid var(--hb-edge)',
      }}>
        {'// PROFILE = AGENT\'S OWN POLICY · A PIN OVERRIDES INTERACTIVE + DISPATCH RUNS'}
      </p>

      {/* Roster list */}
      <div style={{ overflowY: 'auto', minHeight: 0, padding: '0.3rem 0' }}>
        {ROSTER.map((id, i) => {
          const info = infos.find(a => a.agent_id === id)
          return (
            <div key={id} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '0.5rem 0.7rem',
              borderTop: i > 0 ? '1px solid var(--hb-edge)' : 'none',
            }}>
              <Avatar id={id} size={30} />
              <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                <span style={{
                  fontFamily: UI, fontSize: '0.74rem', fontWeight: 700,
                  letterSpacing: '0.1em', color: 'var(--hb-text)', textTransform: 'uppercase',
                }}>
                  {id}
                </span>
                <span style={{
                  fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.08em',
                  color: 'var(--hb-icon-dim)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {info ? info.domain : 'OFFLINE'}
                </span>
              </span>
              <div style={{ width: 168, flexShrink: 0 }}>
                {info
                  ? <AgentModelPicker info={info} models={models} onPin={m => pin(id, m)} />
                  : <span style={{ fontFamily: MONO, fontSize: '0.55rem', color: 'var(--hb-icon-dim)' }}>—</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
