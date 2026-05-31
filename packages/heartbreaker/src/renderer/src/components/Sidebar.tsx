import { createContext, useContext, useState, useMemo, useEffect, useRef } from 'react'
import type { AppProfile } from '../profile/types'
import type { Session, AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { deleteSession, renameSession } from '../lib/api'

const ProfileContext = createContext<AppProfile | null>(null)
export const useProfile = () => useContext(ProfileContext)!
export { ProfileContext }

function groupSessions(sessions: Session[]): { label: string; items: Session[] }[] {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday.getTime() - 86400_000)
  const startOfWeek = new Date(startOfToday.getTime() - 7 * 86400_000)
  const startOfMonth = new Date(startOfToday.getTime() - 30 * 86400_000)

  const groups: Record<string, Session[]> = {
    'Today': [], 'Yesterday': [], 'Previous 7 days': [], 'Previous 30 days': [], 'Older': [],
  }
  for (const s of sessions) {
    const d = new Date(s.started_at)
    if (d >= startOfToday) groups['Today'].push(s)
    else if (d >= startOfYesterday) groups['Yesterday'].push(s)
    else if (d >= startOfWeek) groups['Previous 7 days'].push(s)
    else if (d >= startOfMonth) groups['Previous 30 days'].push(s)
    else groups['Older'].push(s)
  }
  return Object.entries(groups).filter(([, items]) => items.length > 0).map(([label, items]) => ({ label, items }))
}

function SessionItem({
  session, active, onSelect, config,
}: {
  session: Session; active: boolean; onSelect: () => void; config: AppConfig
}) {
  const { dispatch } = useChatContext()
  const [hover, setHover] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState(session.title ?? '')

  // Typewriter display — animates when title first arrives (was null, now a string)
  const [displayTitle, setDisplayTitle] = useState(session.title ?? '')
  const prevTitleRef = useRef<string | null>(session.title ?? null)

  useEffect(() => {
    const prev = prevTitleRef.current
    const next = session.title ?? ''
    prevTitleRef.current = next

    // Only animate when going from no title → real title
    if (!prev && next) {
      setDisplayTitle('')
      setRenameVal(next)
      let i = 0
      const id = setInterval(() => {
        i++
        setDisplayTitle(next.slice(0, i))
        if (i >= next.length) clearInterval(id)
      }, 38)
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

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ position: 'relative', borderRadius: '0.625rem', marginBottom: '1px' }}
    >
      {renaming ? (
        <input
          autoFocus
          value={renameVal}
          onChange={e => setRenameVal(e.target.value)}
          onBlur={handleRenameSave}
          onKeyDown={e => { if (e.key === 'Enter') handleRenameSave(); if (e.key === 'Escape') setRenaming(false) }}
          style={{
            width: '100%', padding: '0.4rem 0.75rem',
            background: 'var(--bg-active)', border: '1px solid var(--border-focus)',
            borderRadius: '0.625rem', color: 'var(--text-primary)',
            fontSize: '0.84rem', fontFamily: 'inherit', outline: 'none', userSelect: 'text',
          }}
        />
      ) : (
        <button
          onClick={onSelect}
          style={{
            width: '100%', padding: '0.45rem 0.75rem',
            borderRadius: '0.625rem', border: 'none',
            background: active ? 'var(--bg-active)' : hover ? 'var(--bg-hover)' : 'transparent',
            color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
            cursor: 'pointer', fontSize: '0.84rem',
            textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            display: 'block', transition: 'background 0.15s, color 0.15s',
            paddingRight: (hover || active) ? '4rem' : '0.75rem',
          }}
        >
          {displayTitle || 'New conversation'}
        </button>
      )}

      {(hover || active) && !renaming && (
        <div style={{
          position: 'absolute', right: '0.375rem', top: '50%', transform: 'translateY(-50%)',
          display: 'flex', alignItems: 'center', gap: '0.125rem',
        }}>
          <button title="Rename" onClick={handleRenameStart} style={iconBtnStyle}
            onMouseEnter={e => (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)'}
            onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>
          <button title="Delete" onClick={handleDelete} style={iconBtnStyle}
            onMouseEnter={e => (e.currentTarget as HTMLButtonElement).style.color = '#f87171'}
            onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
            </svg>
          </button>
        </div>
      )}
    </div>
  )
}

const iconBtnStyle: React.CSSProperties = {
  width: 24, height: 24, display: 'flex', alignItems: 'center', justifyContent: 'center',
  borderRadius: '0.375rem', border: 'none',
  background: 'transparent', color: 'var(--text-muted)',
  cursor: 'pointer', transition: 'color 0.1s',
}

function MenuItem({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', padding: '0.55rem 0.875rem',
        display: 'flex', alignItems: 'center', gap: '0.625rem',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        border: 'none', color: hover ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: 'pointer', fontSize: '0.875rem', textAlign: 'left',
        transition: 'background 0.1s, color 0.1s',
      }}
    >
      <span style={{ opacity: 0.75, flexShrink: 0 }}>{icon}</span>
      {label}
    </button>
  )
}

interface Props {
  profile: AppProfile
  config: AppConfig
  isOpen: boolean
  onSelectSession: (id: number) => void
  onToggle: () => void
  onNewChat: () => void
  onOpenSettings: () => void
}

export default function Sidebar({ profile, config, isOpen, onSelectSession, onToggle, onNewChat, onOpenSettings }: Props) {
  const { state, dispatch } = useChatContext()
  const { settings } = useSettings()
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const filtered = useMemo(() => {
    if (!search.trim()) return state.sessions
    const q = search.toLowerCase()
    return state.sessions.filter(s => (s.title ?? '').toLowerCase().includes(q))
  }, [state.sessions, search])

  const groups = useMemo(() => groupSessions(filtered), [filtered])

  return (
    <aside style={{
      width: isOpen ? 'var(--sidebar-width)' : '0px',
      minWidth: isOpen ? 'var(--sidebar-width)' : '0px',
      height: '100%',
      background: 'var(--bg-sidebar)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      transition: 'width 0.22s ease, min-width 0.22s ease',
      flexShrink: 0,
    }}>
      <div style={{ width: 'var(--sidebar-width)', height: '100%', display: 'flex', flexDirection: 'column' }}>

        {/* Top: Title (clicks = new chat) + hamburger */}
        <div style={{ padding: '0.875rem 0.75rem 0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
          <button
            onClick={onNewChat}
            title="New chat"
            style={{
              background: 'none', border: 'none', padding: '0.1rem 0.25rem',
              cursor: 'pointer', borderRadius: '0.375rem',
              transition: 'opacity 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget as HTMLButtonElement).style.opacity = '0.7'}
            onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.opacity = '1'}
          >
            <span style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
              {profile.name} <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>{profile.modelNumber}</span>
            </span>
          </button>
          <button
            onClick={onToggle}
            title="Close sidebar"
            style={{
              width: 30, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderRadius: '50%', border: 'none', background: 'transparent',
              color: 'var(--text-muted)', cursor: 'pointer', transition: 'background 0.15s, color 0.15s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
            </svg>
          </button>
        </div>

        {/* Navigation items — search only */}
        <div style={{ padding: '0.25rem 0.5rem', flexShrink: 0 }}>
          <NavItem
            icon={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>}
            label="Search chats"
            onClick={() => setSearchOpen(v => !v)}
          />
        </div>

        {/* Search input */}
        {searchOpen && (
          <div style={{ padding: '0 0.75rem 0.5rem', flexShrink: 0 }}>
            <input
              autoFocus
              type="text"
              placeholder="Search…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', padding: '0.5rem 0.75rem',
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid var(--border-focus)',
                borderRadius: '0.625rem',
                color: 'var(--text-primary)', fontSize: '0.84rem',
                fontFamily: 'inherit', outline: 'none', userSelect: 'text',
              }}
            />
          </div>
        )}

        <div style={{ height: '1px', background: 'var(--border)', margin: '0.375rem 0.75rem', flexShrink: 0 }} />

        {/* Session list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 0.5rem 0.5rem' }}>
          {groups.length === 0 ? (
            <p style={{ padding: '1.25rem 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>
              {search ? 'No results' : 'No conversations yet'}
            </p>
          ) : (
            groups.map(({ label, items }) => (
              <div key={label} style={{ marginBottom: '0.625rem' }}>
                <p style={{
                  fontSize: '0.69rem', fontWeight: 500, color: 'var(--text-muted)',
                  padding: '0.5rem 0.75rem 0.3rem',
                  letterSpacing: '0.04em', textTransform: 'capitalize',
                }}>
                  {label}
                </p>
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

        {/* Profile footer — click to open menu */}
        <div ref={menuRef} style={{ position: 'relative', flexShrink: 0 }}>

          {/* Popup menu */}
          {menuOpen && (
            <div style={{
              position: 'absolute', bottom: 'calc(100% + 6px)', left: '0.5rem', right: '0.5rem',
              background: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: '0.75rem', overflow: 'hidden',
              boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
              animation: 'dropDown 0.12s ease',
              zIndex: 50,
            }}>
              <MenuItem
                icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06-.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>}
                label="Settings"
                onClick={() => { onOpenSettings(); setMenuOpen(false) }}
              />
            </div>
          )}

          {/* Trigger row */}
          <button
            onClick={() => setMenuOpen(v => !v)}
            style={{
              width: '100%', padding: '0.625rem 0.75rem',
              borderTop: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', gap: '0.625rem',
              background: menuOpen ? 'var(--bg-hover)' : 'transparent',
              border: 'none', borderTop: '1px solid var(--border)',
              cursor: 'pointer', transition: 'background 0.15s',
              textAlign: 'left',
            }}
            onMouseEnter={e => { if (!menuOpen) (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)' }}
            onMouseLeave={e => { if (!menuOpen) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
          >
            <div style={{
              width: 32, height: 32, borderRadius: '50%', flexShrink: 0,
              background: 'linear-gradient(135deg, #4285F4, #0D9488)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 600, fontSize: '0.875rem', color: '#fff',
            }}>
              {(settings.userName?.[0] || profile.avatarInitial).toUpperCase()}
            </div>
            <div style={{ overflow: 'hidden', flex: 1 }}>
              <p style={{ fontWeight: 500, fontSize: '0.84rem', color: 'var(--text-primary)', lineHeight: 1.2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {settings.userName || profile.userName || profile.name}
              </p>
              <p style={{ fontSize: '0.69rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {profile.tagline}
              </p>
            </div>
            {/* Chevron */}
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              style={{ color: 'var(--text-muted)', flexShrink: 0, transform: menuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
              <polyline points="18 15 12 9 6 15"/>
            </svg>
          </button>
        </div>
      </div>
    </aside>
  )
}

function NavItem({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', padding: '0.5rem 0.75rem',
        borderRadius: '0.625rem', border: 'none',
        background: hover ? 'var(--bg-hover)' : 'transparent',
        color: hover ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: 'pointer', fontSize: '0.875rem', fontWeight: 400,
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        textAlign: 'left', transition: 'background 0.15s, color 0.15s',
        marginBottom: '1px',
      }}
    >
      <span style={{ opacity: 0.8, flexShrink: 0 }}>{icon}</span>
      {label}
    </button>
  )
}
