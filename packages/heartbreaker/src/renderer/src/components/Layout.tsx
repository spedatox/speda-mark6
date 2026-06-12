import { useCallback, useState } from 'react'
import type { AppProfile } from '../profile/types'
import type { AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchMessages } from '../lib/api'
import Sidebar from './Sidebar'
import Header from './Header'
import ChatMain from './ChatMain'
import SettingsModal from './SettingsModal'
import SystemsBoard from './SystemsBoard'

interface LayoutProps {
  profile: AppProfile
  config: AppConfig
}

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
    dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: [] } })
    try {
      const messages = await fetchMessages(config, sessionId)
      dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages } })
    } catch { /* keep empty state on error */ }
  }, [config, dispatch])

  const handleNewChat = useCallback(() => {
    setDrawerOpen(false)
    dispatch({ type: 'NEW_CHAT' })
  }, [dispatch])

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      {/* Mobile drawer backdrop — full glassmorphic blur sheet; tap to dismiss */}
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
