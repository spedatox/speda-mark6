import { useCallback, useState } from 'react'
import type { AppProfile } from '../profile/types'
import type { AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
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

  const sidebarOpen = settings.sidebarOpen

  const handleSelectSession = useCallback(async (sessionId: number) => {
    dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: [] } })
    try {
      const messages = await fetchMessages(config, sessionId)
      dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages } })
    } catch { /* keep empty state on error */ }
  }, [config, dispatch])

  const handleNewChat = useCallback(() => {
    dispatch({ type: 'NEW_CHAT' })
  }, [dispatch])

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      <Sidebar
        profile={profile}
        config={config}
        isOpen={sidebarOpen}
        onSelectSession={handleSelectSession}
        onToggle={() => update({ sidebarOpen: !sidebarOpen })}
        onNewChat={handleNewChat}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <Header
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => update({ sidebarOpen: !sidebarOpen })}
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
