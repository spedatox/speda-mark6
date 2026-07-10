import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useIsPeerOnline } from '../lib/useOnlineAgents'
import type { AppConfig } from '../lib/types'

/**
 * FORGE LINK — engine indicator for Optimus. When the standalone Forge peer is
 * connected the chat is running on it (full agentic execution in the Cell);
 * when it is offline Optimus answers from its in-process profile. A quiet jewel
 * states which, with no layout shift between states. For Optimus it also carries
 * a compact workspace field: the directory the Forge runs the job in (the Cell
 * workspace + Graphify root), persisted in settings and sent as `cwd`.
 */
function ForgeLink({ config, agentId }: { config: AppConfig; agentId: string }) {
  const online = useIsPeerOnline(config, 'optimus')
  const { settings, update } = useSettings()
  if (agentId !== 'optimus') return null

  const color = online ? 'var(--hb-green)' : 'var(--hb-text-faint)'
  const cwd = settings.forgeCwd
  // Show the trailing folder name (the meaningful part) with the parent dimmed.
  const folderName = cwd ? (cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || cwd) : ''

  const pickWorkspace = async () => {
    // Native folder picker in the Electron app; the browser dev build has no
    // native dialog, so fall back to a manual prompt there.
    if (window.api?.selectDirectory) {
      const chosen = await window.api.selectDirectory(cwd || undefined)
      if (chosen) update({ forgeCwd: chosen })
    } else {
      const entered = window.prompt('Forge workspace directory (absolute path):', cwd)
      if (entered != null) update({ forgeCwd: entered.trim() })
    }
  }
  return (
    <span className="hb-hide-sm" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <span
        title={online
          ? 'Optimus is running on the Forge (Mark II) — full agentic execution in an isolated Cell.'
          : 'The Forge peer is offline — Optimus is answering from its in-process fallback engine.'}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
          letterSpacing: '0.12em', color,
        }}
      >
        <span style={{
          width: 6, height: 6, borderRadius: '50%', background: color,
          boxShadow: online ? '0 0 6px var(--hb-green)' : 'none',
          animation: online ? 'hbBlink 1.6s ease-in-out infinite' : 'none',
        }} />
        {online ? 'FORGE LINK' : 'IN-PROCESS'}
      </span>
      <span className="hb-query-box" style={{
        display: 'flex', alignItems: 'center', gap: 6, height: 22,
        maxWidth: 200, padding: '0 0.2rem 0 0.45rem',
      }}>
        <button
          onClick={pickWorkspace}
          title={cwd
            ? `Forge workspace: ${cwd}\nClick to choose another folder.`
            : 'Choose the folder the Forge runs Optimus jobs in (the Cell workspace + codebase-graph root). Blank = the peer’s default.'}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, minWidth: 0,
            border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
            fontFamily: 'var(--font-mono)', fontSize: '0.66rem', letterSpacing: '0.02em',
            color: cwd ? 'var(--hb-text-dim)' : 'var(--hb-text-faint)',
          }}
        >
          {/* Folder glyph */}
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          </svg>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {folderName || 'SET WORKSPACE'}
          </span>
        </button>
        {cwd && (
          <button
            onClick={() => update({ forgeCwd: '' })}
            title="Clear the workspace — use the peer’s default"
            style={{
              display: 'flex', alignItems: 'center', flexShrink: 0,
              border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
              color: 'var(--hb-text-faint)',
            }}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
      </span>
    </span>
  )
}

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
  commsOpen?: boolean
  onToggleComms?: () => void
  /** True when the app IS the war room (standby or engaged takeover). While
   *  true the strip under the header carries the exit control, so the header
   *  WAR ROOM button hides. */
  inWarRoom?: boolean
  onOpenWarRoom?: () => void
}

export default function Header({
  config, agentId,
  sidebarOpen, onToggleSidebar, boardOpen, onToggleBoard,
  commsOpen, onToggleComms, inWarRoom, onOpenWarRoom,
}: Props) {
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

      {/* Forge link — Optimus engine state + workspace (Optimus only) */}
      <ForgeLink config={config} agentId={agentId} />

      {/* Right readout cluster — real session state */}
      <span className="hb-readout hb-hide-sm" style={{ fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>
        MSGS {String(state.messages.length).padStart(3, '0')}
      </span>
      {state.isStreaming ? (
        <span className="hb-hide-sm" style={{
          display: 'flex', alignItems: 'center', gap: 5,
          fontFamily: "var(--font-mono)", fontSize: '0.62rem',
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
          fontFamily: "var(--font-mono)", fontSize: '0.62rem',
          letterSpacing: '0.1em',
          color: hasMessages ? 'var(--hb-amber)' : 'var(--hb-text-faint)',
        }}>
          {hasMessages ? 'QUERY COMPLETE' : 'STANDBY'}
        </span>
      )}

      {/* War room — a profile takeover, not a window. The button enters it
          (branded STANDBY cinematic); once inside, the roster strip under the
          header owns the exit/stand-down, so this hides in standby AND engaged. */}
      {!inWarRoom && onOpenWarRoom && (
        <button
          className="hb-btn"
          onClick={onOpenWarRoom}
          title="Enter the war room — brief SPEDA and stage the roster (protocol stays offline until engaged)"
          style={{
            height: 24, padding: '0 0.55rem', gap: '0.4rem', flexShrink: 0,
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem', fontWeight: 700,
            letterSpacing: '0.16em',
          }}
        >
          {/* Command-table glyph — the roster converging on a center point */}
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <circle cx="12" cy="3.5" r="1.6" /><circle cx="19.5" cy="16.5" r="1.6" /><circle cx="4.5" cy="16.5" r="1.6" />
            <line x1="12" y1="5.1" x2="12" y2="9" />
            <line x1="18.1" y1="15.6" x2="14.6" y2="13.5" />
            <line x1="5.9" y1="15.6" x2="9.4" y2="13.5" />
          </svg>
          WAR ROOM
        </button>
      )}

      {/* Inter-agent comms tray toggle */}
      {onToggleComms && (
        <button
          className={commsOpen ? 'hb-btn hb-btn-tint' : 'hb-btn'}
          onClick={onToggleComms}
          title={commsOpen ? 'Close agent comms' : 'Open inter-agent comms traffic'}
          style={{
            height: 24, padding: '0 0.5rem', gap: '0.35rem', flexShrink: 0,
            ...(commsOpen ? { color: 'var(--hb-amber-bright)' } : {}),
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem', fontWeight: 700,
            letterSpacing: '0.16em',
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="5" cy="12" r="2.4"/><circle cx="19" cy="5" r="2.4"/><circle cx="19" cy="19" r="2.4"/>
            <line x1="7.2" y1="11" x2="16.8" y2="5.9"/><line x1="7.2" y1="13" x2="16.8" y2="18.1"/>
          </svg>
          COMMS
        </button>
      )}

      {/* Systems board toggle */}
      {onToggleBoard && (
        <button
          className={boardOpen ? 'hb-btn hb-btn-tint' : 'hb-btn'}
          onClick={onToggleBoard}
          title={boardOpen ? 'Close systems board' : 'Open systems board'}
          style={{
            height: 24, padding: '0 0.5rem', gap: '0.35rem', flexShrink: 0,
            ...(boardOpen ? { color: 'var(--hb-amber-bright)' } : {}),
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
