// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useState } from 'react'
import type { AppProfile } from '../profile/types'
import type { AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchMessages } from '../lib/api'
import { loadMessages, saveMessages } from '../store/messageCache'
import Sidebar from './Sidebar'
import Header from './Header'
import ChatMain from './ChatMain'
import ProjectsView from './ProjectsView'
import SettingsModal from './SettingsModal'
import SystemsBoard from './SystemsBoard'

interface LayoutProps {
  profile: AppProfile
  config: AppConfig
}

/**
 * Core layout — single agent, no roster. Just the sidebar, header, chat deck,
 * the systems board and settings (plus the mobile drawer). Heartbreaker's war
 * room, comms tray, roster strip and agent switcher are deliberately absent.
 */
export default function Layout({ profile, config }: LayoutProps) {
  const { dispatch } = useChatContext()
  const { settings, update } = useSettings()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [boardOpen, setBoardOpen] = useState(false)

  const isMobile = useIsMobile()
  // Mobile drawer state is session-local and starts closed — the drawer only
  // ever opens from an explicit tap on the header menu button.
  const [drawerOpen, setDrawerOpen] = useState(false)

  const sidebarOpen = settings.sidebarOpen

  // The projects surface replaces the chat column rather than floating over it:
  // it is a place you go, not a dialog you dismiss, and the sidebar stays put so
  // the conversation list is still one click away. `null` = the chat view.
  const [projectsAt, setProjectsAt] = useState<{ projectId: number | null } | null>(null)

  const handleSelectSession = useCallback(async (sessionId: number, projectId?: number | null) => {
    setDrawerOpen(false)
    setProjectsAt(null)
    // Show the cached transcript instantly (also the offline fallback), then let
    // the server refresh it. If the fetch fails (no network), the cache stays.
    const cached = loadMessages(config.agentId, sessionId)
    dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: cached ?? [], projectId } })
    try {
      const messages = await fetchMessages(config, sessionId)
      // Server is authoritative when it actually returned the turn; if it came
      // back empty but we have a cached copy (e.g. an answer lost to a mid-turn
      // restart), keep showing the cache rather than blanking the view.
      if (messages.length || !cached) {
        dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages, projectId } })
        if (messages.length) saveMessages(config.agentId, sessionId, messages)
      }
    } catch { /* offline — keep the cached transcript already shown */ }
  }, [config, dispatch])

  const handleNewChat = useCallback(() => {
    setDrawerOpen(false)
    setProjectsAt(null)
    // No projectId — New chat from the sidebar is always a LOOSE chat, even
    // while a project is open. Inheriting the open project here is how a
    // workspace's standing instructions end up on an unrelated conversation.
    dispatch({ type: 'NEW_CHAT' })
  }, [dispatch])

  /** New chat bound to a project — the only path that carries a projectId. */
  const handleNewProjectChat = useCallback((projectId: number) => {
    setDrawerOpen(false)
    setProjectsAt(null)
    dispatch({ type: 'NEW_CHAT', payload: { projectId } })
  }, [dispatch])

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      {/* Mobile drawer backdrop — blur sheet; tap to dismiss */}
      {isMobile && drawerOpen && (
        <div
          onClick={() => setDrawerOpen(false)}
          style={{
            position: 'fixed', inset: 0, zIndex: 9000,
            background: 'rgba(4, 8, 10, 0.45)',
            backdropFilter: 'var(--hb-holo-blur)',
            WebkitBackdropFilter: 'var(--hb-holo-blur)',
            animation: 'fadeIn 0.2s ease both',
          }}
        />
      )}

      <Sidebar
        profile={profile}
        config={config}
        isOpen={isMobile ? drawerOpen : sidebarOpen}
        mobile={isMobile}
        onSelectSession={handleSelectSession}
        onToggle={() => (isMobile ? setDrawerOpen(false) : update({ sidebarOpen: !sidebarOpen }))}
        onNewChat={handleNewChat}
        onOpenSettings={() => { setDrawerOpen(false); setSettingsOpen(true) }}
        onOpenProjects={(projectId) => {
          setDrawerOpen(false)
          setProjectsAt({ projectId: projectId ?? null })
        }}
        projectsOpen={projectsAt !== null}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <Header
          config={config}
          agentId={profile.agentId}
          sidebarOpen={isMobile ? false : sidebarOpen}
          onToggleSidebar={() => (isMobile ? setDrawerOpen(true) : update({ sidebarOpen: !sidebarOpen }))}
          boardOpen={boardOpen}
          onToggleBoard={() => setBoardOpen(v => !v)}
        />
        {projectsAt ? (
          <ProjectsView
            config={config}
            locale={settings.locale}
            initialProjectId={projectsAt.projectId}
            onClose={() => setProjectsAt(null)}
            onOpenChat={(sessionId, projectId) => handleSelectSession(sessionId, projectId)}
            onNewChat={handleNewProjectChat}
          />
        ) : (
          <ChatMain config={config} onSelectSession={handleSelectSession} />
        )}
      </div>

      {boardOpen && <SystemsBoard config={config} onClose={() => setBoardOpen(false)} />}
      {settingsOpen && <SettingsModal config={config} onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
