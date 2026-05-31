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
}

export default function Header({ sidebarOpen, onToggleSidebar }: Props) {
  const { state } = useChatContext()
  const activeSession = state.sessions.find(s => s.id === state.activeSessionId)

  return (
    <header style={{
      height: 40, flexShrink: 0,
      display: 'flex', alignItems: 'center', gap: '0.6rem',
      padding: '0 0.85rem',
      borderBottom: '1px solid var(--hb-line)',
      background: 'linear-gradient(180deg, rgba(10,24,30,0.6), transparent)',
      position: 'relative', zIndex: 10,
    }}>
      {!sidebarOpen && onToggleSidebar && (
        <IconBtn onClick={onToggleSidebar} title="Open panel">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </IconBtn>
      )}

      {/* Section marker */}
      <span className="hb-label" style={{ color: 'var(--hb-cyan)' }}>// SESSION</span>

      {/* Active session title */}
      <span style={{
        fontSize: '0.8rem', fontWeight: 500, letterSpacing: '0.06em',
        textTransform: 'uppercase', color: 'var(--hb-text)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {activeSession?.title || 'NEW LINK'}
      </span>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Right readout cluster */}
      <span className="hb-readout" style={{ fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>
        BUFFER {String(state.messages.length).padStart(3, '0')}
      </span>
      <span style={{
        display: 'flex', alignItems: 'center', gap: 5,
        fontFamily: "'Share Tech Mono', monospace", fontSize: '0.62rem',
        letterSpacing: '0.1em', color: 'var(--hb-green)',
      }}>
        <span style={{ width: 6, height: 6, background: 'var(--hb-green)', display: 'inline-block' }} />
        LINK ACTIVE
      </span>
    </header>
  )
}
