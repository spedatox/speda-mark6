import { useCallback, useState, useEffect } from 'react'
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
import CommsTray from './CommsTray'
import PartyRosterStrip from './PartyRosterStrip'
import RosterModelWindow from './RosterModelWindow'
import AgentSwitcherOverlay from './AgentSwitcherOverlay'
import HousePartyModal from './HousePartyModal'

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
  // House Party authorization modal — opened when SPEDA emits the hpp-warning
  // marker (via the in-chat trigger's `speda:hpp-authorize` event).
  const [hppAuth, setHppAuth] = useState<{ objective?: string } | null>(null)
  useEffect(() => {
    const onAuth = (e: Event) => {
      const detail = (e as CustomEvent).detail || {}
      setHppAuth({ objective: detail.objective || undefined })
    }
    window.addEventListener('speda:hpp-authorize', onAuth)
    return () => window.removeEventListener('speda:hpp-authorize', onAuth)
  }, [])

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
      {hppAuth && (
        <HousePartyModal
          config={config}
          objective={hppAuth.objective}
          onClose={() => setHppAuth(null)}
          onEngaged={() => window.dispatchEvent(new CustomEvent('speda:hpp-engaged'))}
        />
      )}
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
