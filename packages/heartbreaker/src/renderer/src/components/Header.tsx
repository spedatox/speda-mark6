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
        width: 36, height: 36,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: '50%', border: 'none',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: hover ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: 'pointer', transition: 'background 0.15s, color 0.15s',
        flexShrink: 0,
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
      height: 52, flexShrink: 0,
      display: 'flex', alignItems: 'center',
      padding: '0 0.75rem',
      background: 'transparent',
      border: 'none',
      position: 'relative',
      zIndex: 10,
    }}>
      {!sidebarOpen && onToggleSidebar && (
        <IconBtn onClick={onToggleSidebar} title="Open sidebar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </IconBtn>
      )}
      {activeSession?.title && (
        <span style={{ fontSize: '0.875rem', fontWeight: 400, color: 'var(--text-muted)', marginLeft: '0.25rem' }}>
          {activeSession.title}
        </span>
      )}
    </header>
  )
}
