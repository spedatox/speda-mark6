import { createContext, useContext, useState, useMemo, useEffect, useRef } from 'react'
import type { AppProfile } from '../profile/types'
import type { Session, AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { deleteSession, renameSession } from '../lib/api'

const ProfileContext = createContext<AppProfile | null>(null)
export const useProfile = () => useContext(ProfileContext)!
export { ProfileContext }

/* ── Time grouping ────────────────────────────────────────────────────────── */
function groupSessions(sessions: Session[]): { label: string; items: Session[] }[] {
  const now = new Date()
  const tod = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yest = new Date(tod.getTime() - 86400_000)
  const week = new Date(tod.getTime() - 7 * 86400_000)
  const month = new Date(tod.getTime() - 30 * 86400_000)
  const groups: Record<string, Session[]> = {
    'Today': [], 'Yesterday': [], 'This week': [], 'This month': [], 'Older': [],
  }
  for (const s of sessions) {
    const d = new Date(s.started_at)
    if (d >= tod)   groups['Today'].push(s)
    else if (d >= yest)  groups['Yesterday'].push(s)
    else if (d >= week)  groups['This week'].push(s)
    else if (d >= month) groups['This month'].push(s)
    else groups['Older'].push(s)
  }
  return Object.entries(groups).filter(([, v]) => v.length).map(([label, items]) => ({ label, items }))
}

/* ── Shared micro-styles ──────────────────────────────────────────────────── */
const mono: React.CSSProperties = {
  fontFamily: "'Share Tech Mono', monospace",
}

/* ── Session item ─────────────────────────────────────────────────────────── */
function SessionItem({ session, active, onSelect, config }: {
  session: Session; active: boolean; onSelect: () => void; config: AppConfig
}) {
  const { dispatch } = useChatContext()
  const [hover, setHover]       = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState(session.title ?? '')

  // Typewriter on title arrival
  const [displayTitle, setDisplayTitle] = useState(session.title ?? '')
  const prevRef = useRef<string | null>(session.title ?? null)
  useEffect(() => {
    const prev = prevRef.current
    const next = session.title ?? ''
    prevRef.current = next
    if (!prev && next) {
      setDisplayTitle('')
      setRenameVal(next)
      let i = 0
      const id = setInterval(() => {
        i++
        setDisplayTitle(next.slice(0, i))
        if (i >= next.length) clearInterval(id)
      }, 36)
      return () => clearInterval(id)
    } else {
      setDisplayTitle(next)
    }
  }, [session.title])

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()
    dispatch({ type: 'DELETE_SESSION', payload: { id: session.id } })
    await deleteSession(config, session.id)
  }
  const handleRenameStart = (e: React.MouseEvent) => {
    e.stopPropagation()
    setRenameVal(session.title ?? '')
    setRenaming(true)
  }
  const handleRenameSave = async () => {
    setRenaming(false)
    if (!renameVal.trim()) return
    dispatch({ type: 'UPDATE_SESSION_TITLE', payload: { sessionId: session.id, title: renameVal.trim() } })
    await renameSession(config, session.id, renameVal.trim())
  }

  const lit = active || hover

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ position: 'relative', marginBottom: '1px' }}
    >
      {renaming ? (
        <input
          autoFocus
          value={renameVal}
          onChange={e => setRenameVal(e.target.value)}
          onBlur={handleRenameSave}
          onKeyDown={e => {
            if (e.key === 'Enter') handleRenameSave()
            if (e.key === 'Escape') setRenaming(false)
          }}
          style={{
            width: '100%',
            padding: '0.4rem 0.6rem 0.4rem 0.85rem',
            background: 'rgba(54,171,202,0.08)',
            border: '1px solid rgba(110,200,228,0.55)',
            borderLeft: '2px solid #36abca',
            color: '#cadbe2',
            fontSize: '0.855rem',
            fontFamily: "'SamsungOne','Inter',sans-serif",
            outline: 'none',
            userSelect: 'text',
          }}
        />
      ) : (
        <button
          className="hb-glass-xs"
          onClick={onSelect}
          style={{
            width: '100%',
            padding: '0.5rem 0.7rem',
            // Selected row goes AMBER — the phone-book highlighted-entry look
            border: `1px solid ${active ? 'rgba(242,183,92,0.3)' : 'transparent'}`,
            borderLeft: active ? '2px solid var(--hb-amber)' : hover ? '2px solid rgba(95,165,188,0.35)' : '2px solid transparent',
            background: active
              ? 'rgba(217, 156, 68, 0.12)'
              : hover
              ? 'rgba(54,171,202,0.07)'
              : 'transparent',
            color: active ? '#f3e2c4' : hover ? '#c2d6de' : '#6e8c97',
            cursor: 'pointer',
            fontSize: '0.875rem',
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontWeight: active ? 600 : 400,
            lineHeight: 1.45,
            textAlign: 'left',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: 'block',
            transition: 'background 0.12s, color 0.12s, border-color 0.12s',
            paddingRight: lit ? '3.75rem' : '0.7rem',
          }}
        >
          {displayTitle || 'New conversation'}
        </button>
      )}

      {/* Action icons */}
      {lit && !renaming && (
        <div style={{
          position: 'absolute', right: '0.3rem', top: '50%', transform: 'translateY(-50%)',
          display: 'flex', alignItems: 'center', gap: '2px',
        }}>
          <ActionIcon title="Rename" onClick={handleRenameStart} hoverColor="#5fcce6">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </ActionIcon>
          <ActionIcon title="Delete" onClick={handleDelete} hoverColor="#c84a3a">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
            </svg>
          </ActionIcon>
        </div>
      )}
    </div>
  )
}

function ActionIcon({ title, onClick, hoverColor, children }: {
  title: string; onClick: (e: React.MouseEvent) => void; hoverColor: string; children: React.ReactNode
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 22, height: 22,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: 'none', background: hover ? 'rgba(54,171,202,0.12)' : 'transparent',
        color: hover ? hoverColor : '#3a5a65',
        cursor: 'pointer', transition: 'color 0.1s, background 0.1s',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

/* ── Group label ──────────────────────────────────────────────────────────── */
function GroupLabel({ label }: { label: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.5rem',
      padding: '0.6rem 0.85rem 0.25rem',
      marginBottom: '1px',
    }}>
      <span style={{
        fontFamily: "'Share Tech Mono', monospace",
        fontSize: '0.6rem',
        color: 'rgba(54,171,202,0.55)',
        whiteSpace: 'nowrap',
      }}>
        {'>>:'}
      </span>
      <span style={{
        fontFamily: "'Rajdhani', sans-serif",
        fontSize: '0.7rem',
        fontWeight: 700,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color: 'rgba(160,200,215,0.6)',
        whiteSpace: 'nowrap',
      }}>
        {label}
      </span>
      <span style={{
        flex: 1, height: '1px',
        background: 'linear-gradient(90deg, rgba(95,165,188,0.28), rgba(95,165,188,0.03))',
      }} />
    </div>
  )
}

/* ── New chat button ──────────────────────────────────────────────────────── */
function NewChatBtn({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      className="hb-seam-b"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="New conversation"
      style={{
        width: '100%',
        padding: '0.5rem 0.85rem',
        display: 'flex', alignItems: 'center', gap: '0.55rem',
        border: 'none',
        background: hover ? 'rgba(54,171,202,0.08)' : 'transparent',
        color: hover ? '#cadbe2' : '#5d7f8a',
        cursor: 'pointer',
        transition: 'background 0.15s, border-color 0.15s, color 0.15s',
        textAlign: 'left',
      }}
    >
      {/* plus icon */}
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" style={{ flexShrink: 0, color: hover ? '#36abca' : '#2e5260' }}>
        <line x1="12" y1="5" x2="12" y2="19"/>
        <line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
      <span style={{
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.76rem', fontWeight: 600,
        letterSpacing: '0.14em', textTransform: 'uppercase',
      }}>
        New conversation
      </span>
    </button>
  )
}

/* ── Search bar ───────────────────────────────────────────────────────────── */
function SearchBar({ value, onChange, onClose }: {
  value: string; onChange: (v: string) => void; onClose: () => void
}) {
  return (
    <div className="hb-seam-b" style={{
      display: 'flex', alignItems: 'center', gap: '0.4rem',
      padding: '0 0.75rem 0.5rem',
    }}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" style={{ color: '#36abca', flexShrink: 0 }}>
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <input
        autoFocus
        type="text"
        placeholder="SEARCH SESSIONS"
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          flex: 1,
          background: 'transparent',
          border: 'none',
          outline: 'none',
          color: '#cadbe2',
          fontSize: '0.72rem',
          fontFamily: "'Share Tech Mono', monospace",
          letterSpacing: '0.1em',
          userSelect: 'text',
        }}
      />
      <button onClick={onClose} style={{
        background: 'transparent', border: 'none',
        color: '#2e5260', cursor: 'pointer', padding: '2px',
        lineHeight: 1, flexShrink: 0,
      }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
  )
}

/* ── Settings menu popup ──────────────────────────────────────────────────── */
function SettingsPopup({ onSettings, onClose }: { onSettings: () => void; onClose: () => void }) {
  return (
    <div className="hb-glass" style={{
      position: 'absolute', bottom: 'calc(100% + 4px)', left: 0, right: 0,
      background: 'rgba(150, 190, 225, 0.07)',
      backdropFilter: 'var(--hb-holo-blur)',
      WebkitBackdropFilter: 'var(--hb-holo-blur)',
      border: '1px solid var(--hb-edge)',
      boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.15)',
      animation: 'dropDown 0.12s ease',
      zIndex: 50,
      overflow: 'hidden',
    }}>
      <PopupItem
        icon={<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06-.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>}
        label="Settings"
        onClick={() => { onSettings(); onClose() }}
      />
    </div>
  )
}

function PopupItem({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', padding: '0.55rem 0.85rem',
        display: 'flex', alignItems: 'center', gap: '0.6rem',
        background: hover ? 'rgba(54,171,202,0.1)' : 'transparent',
        border: 'none',
        borderLeft: hover ? '2px solid #36abca' : '2px solid transparent',
        color: hover ? '#cadbe2' : '#5d7f8a',
        cursor: 'pointer',
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.76rem', fontWeight: 600,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        textAlign: 'left',
        transition: 'background 0.1s, color 0.1s, border-color 0.1s',
      }}
    >
      <span style={{ color: hover ? '#36abca' : '#2e5260', flexShrink: 0 }}>{icon}</span>
      {label}
    </button>
  )
}

/* ── Header ───────────────────────────────────────────────────────────────── */
function SidebarHeader({ profile, onToggle, onSearch, searchActive }: {
  profile: AppProfile; onToggle: () => void; onSearch: () => void; searchActive: boolean
}) {
  return (
    <div className="hb-seam-b" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 0.5rem 0 0.85rem',
      height: 40,  // matches the session header — seams align across the full width
      flexShrink: 0,
      position: 'relative',
    }}>
      {/* Brand — horizontal lockup */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
        <span style={{
          fontFamily: "'Rajdhani',sans-serif",
          fontSize: '1.15rem', fontWeight: 800,
          letterSpacing: '0.18em', textTransform: 'uppercase',
          color: '#ffffff',
          lineHeight: 1.1,
        }}>
          {profile.name}
        </span>
        <span style={{
          fontFamily: "'Rajdhani',sans-serif",
          fontSize: '1.15rem', fontWeight: 800,
          letterSpacing: '0.18em', textTransform: 'uppercase',
          color: '#36abca',
          lineHeight: 1.1,
        }}>
          {profile.modelNumber}
        </span>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
        <HeaderBtn title="Search" active={searchActive} onClick={onSearch}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
          </svg>
        </HeaderBtn>
        <HeaderBtn title="Close sidebar" onClick={onToggle}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="21" y2="12"/>
            <line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </HeaderBtn>
      </div>

    </div>
  )
}

function HeaderBtn({ title, onClick, active, children }: {
  title: string; onClick: () => void; active?: boolean; children: React.ReactNode
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 28, height: 28,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: 'none',
        background: active || hover ? 'rgba(54,171,202,0.1)' : 'transparent',
        color: active ? '#36abca' : hover ? '#9bbac5' : '#3a5a65',
        cursor: 'pointer',
        transition: 'background 0.1s, color 0.1s',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

/* ── Footer (user profile row) ────────────────────────────────────────────── */
function SidebarFooter({ profile, onOpenSettings }: { profile: AppProfile; onOpenSettings: () => void }) {
  const { settings } = useSettings()
  const [menuOpen, setMenuOpen] = useState(false)
  const [hover, setHover]       = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [menuOpen])

  const displayName = settings.userName || profile.userName || profile.name

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      {menuOpen && (
        <SettingsPopup onSettings={onOpenSettings} onClose={() => setMenuOpen(false)} />
      )}
      <button
        className="hb-seam-t"
        onClick={() => setMenuOpen(v => !v)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          width: '100%', padding: '0.55rem 0.75rem 0.55rem 0.85rem',
          display: 'flex', alignItems: 'center', gap: '0.6rem',
          background: menuOpen || hover ? 'rgba(54,171,202,0.07)' : 'transparent',
          border: 'none',
          cursor: 'pointer',
          transition: 'background 0.12s',
          textAlign: 'left',
        }}
      >
        {/* Avatar — sharp square with teal border */}
        <div className="hb-glass-xs" style={{
          width: 28, height: 28,
          flexShrink: 0,
          background: 'rgba(54,171,202,0.12)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.15)',
          border: `1px solid ${menuOpen || hover ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: "'Rajdhani',sans-serif",
          fontSize: '0.8rem', fontWeight: 700,
          color: menuOpen || hover ? '#5fcce6' : '#36abca',
          letterSpacing: '0.05em',
          transition: 'border-color 0.12s, color 0.12s',
          userSelect: 'none',
        }}>
          {(settings.userName?.[0] || profile.avatarInitial || '?').toUpperCase()}
        </div>

        {/* Name + tagline */}
        <div style={{ overflow: 'hidden', flex: 1, minWidth: 0 }}>
          <p style={{
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '0.83rem', fontWeight: 500,
            color: menuOpen || hover ? '#cadbe2' : '#7a96a1',
            lineHeight: 1.2,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            transition: 'color 0.12s',
          }}>
            {displayName}
          </p>
          <p style={{
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '0.68rem',
            color: 'rgba(160,200,215,0.45)',
            letterSpacing: '0.01em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            marginTop: '2px',
          }}>
            {profile.tagline}
          </p>
        </div>

        {/* Chevron */}
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          style={{
            color: menuOpen || hover ? '#36abca' : '#2e5260',
            flexShrink: 0,
            transform: menuOpen ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.15s, color 0.12s',
          }}>
          <polyline points="18 15 12 9 6 15"/>
        </svg>
      </button>
    </div>
  )
}

/* ── Main component ───────────────────────────────────────────────────────── */
interface Props {
  profile: AppProfile
  config: AppConfig
  isOpen: boolean
  /** Under the 768px breakpoint the sidebar becomes an off-canvas drawer */
  mobile?: boolean
  onSelectSession: (id: number) => void
  onToggle: () => void
  onNewChat: () => void
  onOpenSettings: () => void
}

export default function Sidebar({ profile, config, isOpen, mobile, onSelectSession, onToggle, onNewChat, onOpenSettings }: Props) {
  const { state } = useChatContext()
  const [search, setSearch]         = useState('')
  const [searchOpen, setSearchOpen] = useState(false)

  const filtered = useMemo(() => {
    if (!search.trim()) return state.sessions
    const q = search.toLowerCase()
    return state.sessions.filter(s => (s.title ?? '').toLowerCase().includes(q))
  }, [state.sessions, search])

  const groups = useMemo(() => groupSessions(filtered), [filtered])

  return (
    <aside className="hb-seam-r" style={mobile ? {
      // Off-canvas drawer — fixed under the HUD strip, slides in from the left
      // as a fully frosted glass sheet. Stays mounted off-screen so the slide
      // animates both ways.
      position: 'fixed', top: 22, bottom: 4, left: 0, zIndex: 9001,
      width: 'min(84vw, 330px)', minWidth: 0, height: 'auto',
      background: 'rgba(8, 14, 20, 0.55)',
      backdropFilter: 'var(--hb-holo-blur)',
      WebkitBackdropFilter: 'var(--hb-holo-blur)',
      boxShadow: '8px 0 40px rgba(0, 0, 0, 0.5), inset 0 1px 0 0 rgba(255,255,255,0.12)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      transform: isOpen ? 'translateX(0)' : 'translateX(-105%)',
      transition: 'transform 0.28s cubic-bezier(0.32, 0.72, 0.33, 1)',
      flexShrink: 0,
    } : {
      width: isOpen ? 'var(--sidebar-width)' : '0px',
      minWidth: isOpen ? 'var(--sidebar-width)' : '0px',
      height: '100%',
      // Reading-hierarchy tint: a touch denser than the floating cards so the
      // conversation list sits back; etched boundary on the right (hb-seam-r).
      background: 'rgba(6, 11, 19, 0.2)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      transition: 'width 0.2s ease, min-width 0.2s ease',
      flexShrink: 0,
    }}>
      <div style={{ width: mobile ? '100%' : 'var(--sidebar-width)', height: '100%', display: 'flex', flexDirection: 'column' }}>

        {/* Header */}
        <SidebarHeader
          profile={profile}
          onToggle={onToggle}
          onSearch={() => { setSearchOpen(v => !v); if (searchOpen) setSearch('') }}
          searchActive={searchOpen}
        />

        {/* Search */}
        {searchOpen && (
          <SearchBar
            value={search}
            onChange={setSearch}
            onClose={() => { setSearchOpen(false); setSearch('') }}
          />
        )}

        {/* New chat */}
        <NewChatBtn onClick={onNewChat} />

        {/* Session list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0.25rem 0.4rem 0.5rem' }}>
          {groups.length === 0 ? (
            <div style={{
              padding: '2rem 0.85rem 1rem',
              textAlign: 'center',
            }}>
              <p style={{
                ...mono,
                fontSize: '0.63rem',
                letterSpacing: '0.12em',
                color: '#2e5260',
                textTransform: 'uppercase',
              }}>
                {search ? '// No results' : '// No sessions'}
              </p>
            </div>
          ) : (
            groups.map(({ label, items }) => (
              <div key={label}>
                <GroupLabel label={label} />
                {items.map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    active={state.activeSessionId === session.id}
                    onSelect={() => onSelectSession(session.id)}
                    config={config}
                  />
                ))}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <SidebarFooter profile={profile} onOpenSettings={onOpenSettings} />

      </div>
    </aside>
  )
}
