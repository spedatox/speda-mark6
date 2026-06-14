import { useState } from 'react'
import { useChatContext } from '../store/chat'

function IconBtn({ onClick, title, children }: { onClick: () => void; title: string; children: React.ReactNode }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 30, height: 26,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `1px solid ${hover ? 'var(--hb-cyan)' : 'var(--hb-line)'}`,
        background: hover ? 'rgba(54,171,202,0.1)' : 'transparent',
        color: hover ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
        cursor: 'pointer', transition: 'all 0.12s', flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

interface Props {
  sidebarOpen?: boolean
  onToggleSidebar?: () => void
  boardOpen?: boolean
  onToggleBoard?: () => void
}

export default function Header({ sidebarOpen, onToggleSidebar, boardOpen, onToggleBoard }: Props) {
  const { state } = useChatContext()
  const activeSession = state.sessions.find(s => s.id === state.activeSessionId)
  const hasMessages = state.messages.length > 0

  return (
    <header className="hb-seam-b" style={{
      height: 40, flexShrink: 0,
      display: 'flex', alignItems: 'center', gap: '0.6rem',
      padding: '0 0.85rem',
      // Structural plate: zero tint, pure frost; fading hairline seam at its base
      background: 'transparent',
      backdropFilter: 'var(--hb-holo-blur)',
      WebkitBackdropFilter: 'var(--hb-holo-blur)',
      position: 'relative', zIndex: 10,
    }}>
      {!sidebarOpen && onToggleSidebar && (
        <IconBtn onClick={onToggleSidebar} title="Open panel">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </IconBtn>
      )}

      {/* Section marker — "MONITOR No. 1" style */}
      <span className="hb-label hb-hide-sm" style={{ color: 'var(--hb-cyan)', whiteSpace: 'nowrap' }}>
        MONITOR <span style={{ color: 'var(--hb-text-faint)' }}>No. 1</span>
      </span>

      {/* Magnifier — the reference search glyph */}
      <svg className="hb-hide-sm" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" style={{ color: 'var(--hb-text-faint)', flexShrink: 0 }}>
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>

      {/* Active session title — the ":ANTON VANKO" query box */}
      <span className="hb-query-box" style={{
        fontSize: '0.76rem', height: 22, maxWidth: '40%',
        overflow: 'hidden',
      }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {activeSession?.title || 'NEW LINK'}
        </span>
      </span>


      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Right readout cluster — real session state */}
      <span className="hb-readout hb-hide-sm" style={{ fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>
        MSGS {String(state.messages.length).padStart(3, '0')}
      </span>
      {state.isStreaming ? (
        <span className="hb-hide-sm" style={{
          display: 'flex', alignItems: 'center', gap: 5,
          fontFamily: "'Share Tech Mono', monospace", fontSize: '0.62rem',
          letterSpacing: '0.1em', color: 'var(--hb-amber-bright)',
        }}>
          <span style={{
            width: 6, height: 6, display: 'inline-block',
            background: 'var(--hb-amber-bright)',
            animation: 'hbBlink 0.8s step-end infinite',
          }} />
          PROCESSING
        </span>
      ) : (
        <span className="hb-hide-sm" style={{
          fontFamily: "'Share Tech Mono', monospace", fontSize: '0.62rem',
          letterSpacing: '0.1em',
          color: hasMessages ? 'var(--hb-amber)' : 'var(--hb-text-faint)',
        }}>
          {hasMessages ? 'QUERY COMPLETE' : 'STANDBY'}
        </span>
      )}

      {/* Systems board toggle */}
      {onToggleBoard && (
        <button
          onClick={onToggleBoard}
          title={boardOpen ? 'Close systems board' : 'Open systems board'}
          style={{
            height: 24, padding: '0 0.5rem',
            display: 'flex', alignItems: 'center', gap: '0.35rem',
            border: `1px solid ${boardOpen ? 'rgba(242,183,92,0.6)' : 'var(--hb-line)'}`,
            background: boardOpen ? 'rgba(217,156,68,0.14)' : 'transparent',
            color: boardOpen ? 'var(--hb-amber-bright)' : 'var(--hb-text-dim)',
            cursor: 'pointer', transition: 'all 0.12s', flexShrink: 0,
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem', fontWeight: 700,
            letterSpacing: '0.16em',
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
            <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
          </svg>
          SYS
        </button>
      )}
    </header>
  )
}
