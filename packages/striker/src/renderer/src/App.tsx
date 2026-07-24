import { useEffect, useReducer, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { saveMessages } from './store/messageCache'
import { SettingsContext, useSettingsProvider } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import PROFILE from './profile'
import Layout from './components/Layout'
import NeuralBackground from './components/NeuralBackground'
import type { AppConfig } from './lib/types'
import { fetchSessions } from './lib/api'
import 'katex/dist/katex.min.css'
import './theme/striker.css'

/**
 * SPEDA Mark VI Core — the single-agent shell. Unlike Heartbreaker there is no
 * roster, no agent switcher, no war room / House Party takeover and no runtime
 * theme morph: the agent is always SPEDA and the palette is static (striker.css).
 * The ambient NeuralBackground stays; the HUD frame does not.
 */
function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [config, setConfig] = useState<AppConfig | null>(null)

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

  if (!config) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)', color: 'var(--text-muted)', fontSize: '0.9rem',
      }}>
        Loading…
      </div>
    )
  }

  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      <ProfileContext.Provider value={PROFILE}>
        <NeuralBackground />
        <Layout profile={PROFILE} config={config} />
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
