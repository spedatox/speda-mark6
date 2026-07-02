import { useCallback, useEffect, useRef, useState } from 'react'
import type { AppProfile } from '../profile/types'
import type { AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchMessages, getHouseParty } from '../lib/api'
import Sidebar from './Sidebar'
import Header from './Header'
import ChatMain from './ChatMain'
import SettingsModal from './SettingsModal'
import SystemsBoard from './SystemsBoard'
import CommsTray from './CommsTray'
import HousePartyBoard from './HousePartyBoard'

interface LayoutProps {
  profile: AppProfile
  config: AppConfig
  switchAgent: (agentId: string) => void
}

export default function Layout({ profile, config, switchAgent }: LayoutProps) {
  const { dispatch } = useChatContext()
  const { settings, update } = useSettings()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [boardOpen, setBoardOpen] = useState(false)
  const [commsOpen, setCommsOpen] = useState(false)

  // House Party Protocol — engaged by the owner TELLING SPEDA, never by the UI.
  // The Layout just watches the flag: when it flips on, the UI transforms into
  // the war-room group chat; when it flips off, everything reverts.
  const [party, setParty] = useState(false)
  const [warRoomOpen, setWarRoomOpen] = useState(false)
  const partyWas = useRef(false)
  useEffect(() => {
    const check = () => getHouseParty(config).then(engaged => {
      setParty(engaged)
      if (engaged && !partyWas.current) setWarRoomOpen(true)   // auto-transform on engage
      partyWas.current = engaged
    })
    check()
    const t = setInterval(check, 4000)
    return () => clearInterval(t)
  }, [config])
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
          sidebarOpen={isMobile ? false : sidebarOpen}
          onToggleSidebar={() => (isMobile ? setDrawerOpen(true) : update({ sidebarOpen: !sidebarOpen }))}
          boardOpen={boardOpen}
          onToggleBoard={() => setBoardOpen(v => !v)}
          commsOpen={commsOpen}
          onToggleComms={() => setCommsOpen(v => !v)}
          partyEngaged={party}
          warRoomOpen={warRoomOpen}
          onOpenWarRoom={() => setWarRoomOpen(true)}
        />
        <ChatMain config={config} onSelectSession={handleSelectSession} />
      </div>

      {boardOpen && <SystemsBoard config={config} onClose={() => setBoardOpen(false)} />}
      {commsOpen && <CommsTray config={config} onClose={() => setCommsOpen(false)} />}
      {warRoomOpen && (
        <HousePartyBoard
          config={config}
          engaged={party}
          onMinimize={() => setWarRoomOpen(false)}
          onStoodDown={() => { setParty(false); setWarRoomOpen(false); partyWas.current = false }}
        />
      )}
      {settingsOpen && <SettingsModal config={config} onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
