import { useCallback, useState, useEffect } from 'react'
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
import CommsTray from './CommsTray'
import PartyRosterStrip from './PartyRosterStrip'
import RosterModelWindow from './RosterModelWindow'
import AgentSwitcherOverlay from './AgentSwitcherOverlay'

interface LayoutProps {
  profile: AppProfile
  config: AppConfig
  switchAgent: (agentId: string) => void
  /** War room live — App.tsx owns the state + cinematic takeover. `inWarRoom`
   *  is true in BOTH standby and engaged; `partyEngaged` narrows it to the
   *  engaged protocol. The Layout just shows the roster strip + config window
   *  and hands the enter/exit intents back up. */
  partyEngaged: boolean
  inWarRoom: boolean
  onEnterWarRoom: () => void
  onExitWarRoom: () => void
}

export default function Layout({
  profile, config, switchAgent, partyEngaged, inWarRoom, onEnterWarRoom, onExitWarRoom,
}: LayoutProps) {
  const { dispatch } = useChatContext()
  const { settings, update } = useSettings()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [boardOpen, setBoardOpen] = useState(false)
  const [commsOpen, setCommsOpen] = useState(false)
  const [switcherOpen, setSwitcherOpen] = useState(false)
  // ROSTER CORES model-config window — only meaningful inside the war room.
  const [coresOpen, setCoresOpen] = useState(false)

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.altKey && e.key.toLowerCase() === 'a') {
        e.preventDefault()
        setSwitcherOpen(v => !v)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

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
        switchAgent={switchAgent}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <Header
          config={config}
          agentId={profile.agentId}
          sidebarOpen={isMobile ? false : sidebarOpen}
          onToggleSidebar={() => (isMobile ? setDrawerOpen(true) : update({ sidebarOpen: !sidebarOpen }))}
          boardOpen={boardOpen}
          onToggleBoard={() => setBoardOpen(v => !v)}
          commsOpen={commsOpen}
          onToggleComms={() => setCommsOpen(v => !v)}
          inWarRoom={inWarRoom}
          onOpenWarRoom={onEnterWarRoom}
        />
        {inWarRoom && (
          <PartyRosterStrip
            config={config}
            engaged={partyEngaged}
            onExit={onExitWarRoom}
            onOpenConfig={() => setCoresOpen(true)}
          />
        )}
        <ChatMain config={config} onSelectSession={handleSelectSession} />
      </div>

      {boardOpen && <SystemsBoard config={config} onClose={() => setBoardOpen(false)} />}
      {commsOpen && <CommsTray config={config} onClose={() => setCommsOpen(false)} />}
      {coresOpen && inWarRoom && <RosterModelWindow config={config} onClose={() => setCoresOpen(false)} />}
      {settingsOpen && <SettingsModal config={config} onClose={() => setSettingsOpen(false)} />}
      {switcherOpen && (
        <AgentSwitcherOverlay
          currentAgentId={profile.agentId}
          onSelect={(id) => {
            switchAgent(id)
            setSwitcherOpen(false)
          }}
          onClose={() => setSwitcherOpen(false)}
        />
      )}
    </div>
  )
}
