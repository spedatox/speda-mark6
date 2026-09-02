// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useReducer, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { saveMessages } from './store/messageCache'
import { SettingsContext, useSettingsProvider, useSettings } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import PROFILE from './profile'
import Layout from './components/Layout'
import NeuralBackground from './components/NeuralBackground'
import LockScreen from './components/LockScreen'
import type { AppConfig } from './lib/types'
import { fetchSessions } from './lib/api'
import { useScreenLock } from './lib/useScreenLock'
import 'katex/dist/katex.min.css'
import './theme/striker.css'

/**
 * Speda Mark VI Core — the single-agent shell. Unlike Heartbreaker there is no
 * roster, no agent switcher, no war room / House Party takeover and no runtime
 * theme morph: the agent is always Speda and the palette is static (striker.css).
 * The ambient NeuralBackground stays; the HUD frame does not.
 */
function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const { settings } = useSettings()

  // Screen lock — Ctrl+L at any moment, on launch when the owner asked for it,
  // and after an idle stretch. Rendered last and over everything: a lock a
  // modal can sit on top of is not a lock.
  const screenLock = useScreenLock()

  // Mirror each session's transcript to local storage as turns SETTLE (not on
  // every streamed chunk). A finished OR errored turn flips isStreaming off, so
  // this captures the answer even when the connection dropped mid-turn and the
  // server never saved it. Read back offline by the session loader.
  useEffect(() => {
    if (state.isStreaming) return
    const sid = state.activeSessionId
    if (sid == null || !state.messages.length) return
    saveMessages(PROFILE.agentId, sid, state.messages)
  }, [state.isStreaming, state.messages, state.activeSessionId])

  useEffect(() => {
    const load = async () => {
      let cfg: AppConfig
      if (window.api?.getConfig) {
        const raw = await window.api.getConfig()
        cfg = { apiBase: raw.apiBase, apiKey: raw.apiKey, agentId: PROFILE.agentId }
      } else {
        cfg = {
          apiBase: (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8000',
          apiKey: (import.meta.env.VITE_API_KEY as string) || 'dev-key',
          agentId: PROFILE.agentId,
        }
      }
      dispatch({ type: 'SET_CONFIG', payload: cfg })
      setConfig(cfg)
      try {
        const sessions = await fetchSessions(cfg)
        dispatch({ type: 'SET_SESSIONS', payload: sessions })
      } catch { /* backend not available */ }
    }
    load()
  }, [])

  // Built once and rendered in BOTH returns: the app spends its first moments
  // on the loading screen, and a lock that only appears after the connection
  // resolves is a lock with a window in it.
  const lockOverlay = screenLock.locked ? (
    <LockScreen
      agentName={PROFILE.name}
      modelNumber={PROFILE.modelNumber}
      hasPasscode={screenLock.hasPasscode}
      screensaverSeconds={settings.lockScreensaverSeconds}
      onUnlock={screenLock.unlock}
    />
  ) : null

  if (!config) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)', color: 'var(--text-muted)', fontSize: '0.9rem',
      }}>
        Loading…
        {lockOverlay}
      </div>
    )
  }

  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      <ProfileContext.Provider value={PROFILE}>
        <NeuralBackground />
        <Layout profile={PROFILE} config={config} />
        {lockOverlay}
      </ProfileContext.Provider>
    </ChatContext.Provider>
  )
}

export default function App() {
  const settingsCtx = useSettingsProvider()
  return (
    <SettingsContext.Provider value={settingsCtx}>
      <AppInner />
    </SettingsContext.Provider>
  )
}
