import { useEffect, useReducer, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { SettingsContext, useSettingsProvider } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import PROFILE from './profile/speda'
import Layout from './components/Layout'
import HudFrame from './components/HudFrame'
import Login from './components/Login'
import type { AppConfig } from './lib/types'
import { fetchSessions, verifyAuth } from './lib/api'
import 'katex/dist/katex.min.css'
import './theme/heartbreaker.css'

// Owner-login session, persisted in the renderer between launches.
const TOKEN_KEY = 'speda.token'
const TOKEN_EXP_KEY = 'speda.token_exp'
const SERVER_KEY = 'speda.server'

function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TOKEN_EXP_KEY)
}

function injectProfileTheme(accent: string, accentHover: string) {
  const root = document.documentElement
  root.style.setProperty('--accent', accent)
  root.style.setProperty('--accent-hover', accentHover)
  root.style.setProperty('--accent-muted', accent + '26')
}

function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [authed, setAuthed] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => { injectProfileTheme(PROFILE.accent, PROFILE.accentHover) }, [])

  // 1. Load base config (server + service key) and restore any valid session.
  useEffect(() => {
    const load = async () => {
      let base: AppConfig
      if (window.api?.getConfig) {
        const raw = await window.api.getConfig()
        base = { apiBase: raw.apiBase, apiKey: raw.apiKey }
      } else {
        base = {
          apiBase: (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8000',
          apiKey: (import.meta.env.VITE_API_KEY as string) || 'dev-key',
        }
      }
      // A server chosen during a previous successful login wins over the default.
      const savedServer = localStorage.getItem(SERVER_KEY)
      if (savedServer) base = { ...base, apiBase: savedServer }

      // Restore a non-expired session token and confirm the backend still honors it.
      const token = localStorage.getItem(TOKEN_KEY)
      const exp = Number(localStorage.getItem(TOKEN_EXP_KEY) || 0)
      if (token && exp * 1000 > Date.now()) {
        const withToken = { ...base, token }
        if (await verifyAuth(withToken)) {
          setConfig(withToken)
          setAuthed(true)
          setChecking(false)
          return
        }
        clearSession()
      }
      setConfig(base)
      setChecking(false)
    }
    load()
  }, [])

  // 2. Once authenticated, load the session list (sent with the Bearer token).
  useEffect(() => {
    if (!authed || !config) return
    dispatch({ type: 'SET_CONFIG', payload: config })
    fetchSessions(config)
      .then((sessions) => dispatch({ type: 'SET_SESSIONS', payload: sessions }))
      .catch(() => { /* backend not available */ })
  }, [authed, config])

  const onLoginSuccess = (token: string, expiresAt: number, server: string) => {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(TOKEN_EXP_KEY, String(expiresAt))
    localStorage.setItem(SERVER_KEY, server)
    setConfig((c) => ({ apiBase: server, apiKey: c?.apiKey ?? '', token }))
    setAuthed(true)
  }

  if (checking || !config) {
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

  if (!authed) {
    return <Login apiBase={config.apiBase} onSuccess={onLoginSuccess} />
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
