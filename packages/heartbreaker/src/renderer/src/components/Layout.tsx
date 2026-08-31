// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useState, useEffect } from 'react'
import type { AppProfile } from '../profile/types'
import type { AppConfig } from '../lib/types'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchMessages } from '../lib/api'
import { loadMessages, saveMessages } from '../store/messageCache'
import Sidebar from './Sidebar'
import Header, { DeckRail } from './Header'
import ChatMain from './ChatMain'
import SettingsModal from './SettingsModal'
import SystemsBoard from './SystemsBoard'
import TelemetryColumn from './TelemetryColumn'
import CommsTray from './CommsTray'
import PartyRosterStrip from './PartyRosterStrip'
import RosterModelWindow from './RosterModelWindow'
import AgentSwitcherOverlay from './AgentSwitcherOverlay'
import HousePartyModal from './HousePartyModal'
import LockdownModal from './LockdownModal'
import SkyfallCountdown from './SkyfallCountdown'
import type { SkyfallArm } from '../lib/api'

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
  // Voice mode is desktop-only: the orb takeover assumes room the phone layout
  // does not have, and the composer would be under the keyboard anyway.
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [switcherOpen, setSwitcherOpen] = useState(false)
  // ROSTER CORES model-config window — only meaningful inside the war room.
  const [coresOpen, setCoresOpen] = useState(false)
  // House Party authorization modal — opened when Speda emits the hpp-warning
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

  // Lockdown authorization modal. Two ways in, one modal: the Protocols tab
  // opens it directly (setLockAuth below), and an agent asking for authorization
  // mid-chat raises the same event House Party uses.
  const [lockAuth, setLockAuth] = useState<{ reason?: string } | null>(null)
  // Skyfall arming, from EITHER route: Speda's tool raises the SSE event that
  // ChatMain forwards here, and the settings pane dispatches the identical
  // event after arming from the project list. One listener, one screen, so
  // there is no second path that could skip the countdown.
  const [skyfallArm, setSkyfallArm] = useState<SkyfallArm | null>(null)
  useEffect(() => {
    const onAuth = (e: Event) => {
      const detail = (e as CustomEvent).detail || {}
      setLockAuth({ reason: detail.reason || undefined })
    }
    const onArm = (e: Event) => setSkyfallArm((e as CustomEvent).detail as SkyfallArm)
    window.addEventListener('speda:lockdown-authorize', onAuth)
    window.addEventListener('speda:skyfall-arm', onArm as EventListener)
    return () => {
      window.removeEventListener('speda:lockdown-authorize', onAuth)
      window.removeEventListener('speda:skyfall-arm', onArm as EventListener)
    }
  }, [])

  // Esc leaves voice mode. Registered before the agent-switcher handler below
  // so the mode is what Esc closes while it is the thing on screen.
  useEffect(() => {
    if (!voiceOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setVoiceOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [voiceOpen])

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

  // The right telemetry column. Permanent by default — it is the glanceable
  // half of the Systems Board — but collapsible from the rail, because two
  // 280px columns is a lot of horizontal room on a laptop.
  const telemetryOpen = settings.telemetryOpen !== false

  const isMobile = useIsMobile()
  // Rail geometry, stated once: five 44px tiles on 8px gaps, one of which
  // (war room) hides inside the war room itself.
  const railTiles = inWarRoom ? 4 : 5
  const railWidth = railTiles * 44 + (railTiles - 1) * 8
  // Mobile drawer state is session-local and starts closed — the drawer only
  // ever opens from an explicit tap on the header menu button.
  const [drawerOpen, setDrawerOpen] = useState(false)

  const sidebarOpen = settings.sidebarOpen

  // True only while a just-selected session has NO local cache to show
  // meanwhile — lets ChatMain skeleton the transcript instead of flashing the
  // "new chat" welcome screen for a conversation that already has history.
  const [historyLoading, setHistoryLoading] = useState(false)

  const handleSelectSession = useCallback(async (sessionId: number) => {
    setDrawerOpen(false)
    // Show the cached transcript instantly (also the offline fallback), then let
    // the server refresh it. If the fetch fails (no network), the cache stays.
    const cached = loadMessages(config.agentId, sessionId)
    dispatch({ type: 'SELECT_SESSION', payload: { sessionId, messages: cached ?? [] } })
    if (!cached) setHistoryLoading(true)
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
    finally { setHistoryLoading(false) }
  }, [config, dispatch])

  const handleNewChat = useCallback(() => {
    setDrawerOpen(false)
    dispatch({ type: 'NEW_CHAT' })
  }, [dispatch])

  return (
    <div style={{ position: 'relative', display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg-primary)' }}>
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

      {/* The chat column. Inset on all four sides so it reads as an island
          floating on the void rather than a pane bolted to the window edge —
          the telemetry column supplies its own right margin, so this one only
          closes the gap when the column is folded away. */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0,
        margin: isMobile ? 0 : `20px ${telemetryOpen ? 0 : 20}px 20px 0`,
      }}>
        <Header
          config={config}
          agentId={profile.agentId}
          sidebarOpen={isMobile ? false : sidebarOpen}
          onToggleSidebar={() => (isMobile ? setDrawerOpen(true) : update({ sidebarOpen: !sidebarOpen }))}
          // The rail floats over this row's right end when the telemetry column
          // is folded away; while it is open the column already holds that
          // space, so the title row needs none.
          railClearance={isMobile || telemetryOpen ? 0 : railWidth}
        />
        {inWarRoom && (
          <PartyRosterStrip
            config={config}
            engaged={partyEngaged}
            onExit={onExitWarRoom}
            onOpenConfig={() => setCoresOpen(true)}
          />
        )}
        <ChatMain
          config={config}
          onSelectSession={handleSelectSession}
          voiceOpen={voiceOpen && !isMobile}
          onCloseVoice={() => setVoiceOpen(false)}
          partyEngaged={partyEngaged}
          historyLoading={historyLoading}
        />
      </div>

      {/* Right island — telemetry. Starts below the rail's row so the floating
          tiles clear it, exactly as the deck has it. */}
      {!isMobile && telemetryOpen && (
        <div style={{ display: 'flex', flexDirection: 'column', paddingTop: 84 }}>
          <TelemetryColumn config={config} agentId={profile.agentId} open />
        </div>
      )}

      {/* The mode switches — floated over the deck, above the telemetry column */}
      {!isMobile && (
        <DeckRail
          boardOpen={boardOpen}
          onToggleBoard={() => setBoardOpen(v => !v)}
          commsOpen={commsOpen}
          onToggleComms={() => setCommsOpen(v => !v)}
          voiceOpen={voiceOpen}
          onToggleVoice={() => setVoiceOpen(v => !v)}
          telemetryOpen={telemetryOpen}
          onToggleTelemetry={() => update({ telemetryOpen: !telemetryOpen })}
          inWarRoom={inWarRoom}
          onOpenWarRoom={onEnterWarRoom}
        />
      )}

      {boardOpen && <SystemsBoard config={config} onClose={() => setBoardOpen(false)} />}
      {commsOpen && <CommsTray config={config} onClose={() => setCommsOpen(false)} />}
      {coresOpen && inWarRoom && <RosterModelWindow config={config} onClose={() => setCoresOpen(false)} />}
      {settingsOpen && (
        <SettingsModal
          config={config}
          onClose={() => setSettingsOpen(false)}
          onEngageLockdown={() => setLockAuth({})}
        />
      )}
      {hppAuth && (
        <HousePartyModal
          config={config}
          objective={hppAuth.objective}
          onClose={() => setHppAuth(null)}
          onEngaged={() => window.dispatchEvent(new CustomEvent('speda:hpp-engaged'))}
        />
      )}
      {lockAuth && (
        <LockdownModal
          config={config}
          reason={lockAuth.reason}
          onClose={() => setLockAuth(null)}
          onEngaged={() => window.dispatchEvent(new CustomEvent('speda:lockdown-engaged'))}
        />
      )}
      {/* Above the settings window on purpose: the owner can arm from the
          Protocols pane and the countdown must not open behind the pane they
          armed it from. */}
      {skyfallArm && (
        <SkyfallCountdown
          config={config}
          arm={skyfallArm}
          onClose={() => setSkyfallArm(null)}
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
