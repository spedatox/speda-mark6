import { useEffect, useReducer, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { SettingsContext, useSettingsProvider } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import PROFILE from './profile/speda'
import Layout from './components/Layout'
import HudFrame from './components/HudFrame'
import type { AppConfig } from './lib/types'
import { fetchSessions } from './lib/api'
import 'katex/dist/katex.min.css'
import './theme/heartbreaker.css'

function injectProfileTheme(accent: string, accentHover: string) {
  const root = document.documentElement
  root.style.setProperty('--accent', accent)
  root.style.setProperty('--accent-hover', accentHover)
  root.style.setProperty('--accent-muted', accent + '26')
}

function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [config, setConfig] = useState<AppConfig | null>(null)

  useEffect(() => { injectProfileTheme(PROFILE.accent, PROFILE.accentHover) }, [])

  useEffect(() => {
    const load = async () => {
      let cfg: AppConfig
      if (window.api?.getConfig) {
        const raw = await window.api.getConfig()
        cfg = { apiBase: raw.apiBase, apiKey: raw.apiKey }
      } else {
        cfg = {
          apiBase: (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8000',
          apiKey: (import.meta.env.VITE_API_KEY as string) || 'dev-key',
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
        height: '100%', display: 'flex', flexDirection: 'column', gap: '0.5rem',
        alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)',
        fontFamily: "'Share Tech Mono', monospace", letterSpacing: '0.12em',
        fontSize: '0.72rem',
      }}>
        <span style={{ color: 'var(--hb-cyan)' }}>Loading configuration…</span>
      </div>
    )
  }

  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      <ProfileContext.Provider value={PROFILE}>
        <HudFrame />
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
