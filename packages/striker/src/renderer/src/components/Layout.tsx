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

  const handleSelectSession = useCallback(async (sessionId: number) => {
    setDrawerOpen(false)
    // Show the cached transcript instantly (also the offline fallback), then let
    // the server refresh it. If the fetch fails (no network), the cache stays.
    const cached = loadMessages(config.agentId, sessionId)
    dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: cached ?? [] } })
    try {
      const messages = await fetchMessages(config, sessionId)
      // Server is authoritative when it actually returned the turn; if it came
      // back empty but we have a cached copy (e.g. an answer lost to a mid-turn
      // restart), keep showing the cache rather than blanking the view.
      if (messages.length || !cached) {
        dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages } })
        if (messages.length) saveMessages(config.agentId, sessionId, messages)
      }
    } catch { /* offline — keep the cached transcript already shown */ }
  }, [config, dispatch])

  const handleNewChat = useCallback(() => {
    setDrawerOpen(false)
    dispatch({ type: 'NEW_CHAT' })
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
        <ChatMain config={config} onSelectSession={handleSelectSession} />
      </div>

      {boardOpen && <SystemsBoard config={config} onClose={() => setBoardOpen(false)} />}
      {settingsOpen && <SettingsModal config={config} onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
