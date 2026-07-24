import { useChatContext } from '../store/chat'
import type { AppConfig } from '../lib/types'

function IconBtn({ onClick, title, children }: { onClick: () => void; title: string; children: React.ReactNode }) {
  return (
    <button
      className="hb-btn"
      onClick={onClick}
      title={title}
      style={{ width: 30, height: 26, flexShrink: 0 }}
    >
      {children}
    </button>
  )
}

interface Props {
  config: AppConfig
  agentId: string
  sidebarOpen?: boolean
  onToggleSidebar?: () => void
  boardOpen?: boolean
  onToggleBoard?: () => void
}

/**
 * Core header — single agent. No Forge link, no war-room entry, no inter-agent
 * comms; just the sidebar toggle, the active session title, a live message/
 * streaming readout, and the systems-board toggle.
 */
export default function Header({
  sidebarOpen, onToggleSidebar, boardOpen, onToggleBoard,
}: Props) {
  const { state } = useChatContext()
  const activeSession = state.sessions.find(s => s.id === state.activeSessionId)
  const hasMessages = state.messages.length > 0

  return (
    <header className="hb-seam-b" style={{
      height: 40, flexShrink: 0,
      display: 'flex', alignItems: 'center', gap: '0.6rem',
      padding: '0 0.85rem',
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

      {/* Active session title */}
      <span className="hb-query-box" style={{
        fontSize: '0.76rem', height: 22, maxWidth: '40%',
        overflow: 'hidden',
      }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {activeSession?.title || 'New chat'}
        </span>
      </span>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Right readout cluster — real session state */}
      <span className="hb-readout hb-hide-sm" style={{ fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>
        {state.messages.length} msg{state.messages.length === 1 ? '' : 's'}
      </span>
      {state.isStreaming ? (
        <span className="hb-hide-sm" style={{
          display: 'flex', alignItems: 'center', gap: 5,
          fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
          letterSpacing: '0.06em', color: 'var(--hb-cyan)',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--hb-cyan)',
            animation: 'hbBlink 0.8s step-end infinite',
          }} />
          Working
        </span>
      ) : (
        <span className="hb-hide-sm" style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
          letterSpacing: '0.06em',
          color: 'var(--hb-text-faint)',
        }}>
          {hasMessages ? 'Ready' : 'Idle'}
        </span>
      )}

      {/* Systems board toggle */}
      {onToggleBoard && (
        <button
          className={boardOpen ? 'hb-btn hb-btn-tint' : 'hb-btn'}
          onClick={onToggleBoard}
          title={boardOpen ? 'Close systems board' : 'Open systems board'}
          style={{
            height: 24, padding: '0 0.5rem', gap: '0.35rem', flexShrink: 0,
            ...(boardOpen ? { color: 'var(--hb-cyan-bright)' } : {}),
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem', fontWeight: 700,
            letterSpacing: '0.12em',
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
