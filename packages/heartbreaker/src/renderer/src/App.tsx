import { useCallback, useEffect, useReducer, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { SettingsContext, useSettingsProvider } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import DEFAULT_PROFILE from './profile'
import { BRANDS } from './profile/brands'
import { applyTheme, morphTheme, deriveAccents } from './profile/theme'
import type { AppProfile } from './profile/types'
import Layout from './components/Layout'
import HudFrame from './components/HudFrame'
import NeuralBackground from './components/NeuralBackground'
import type { AppConfig } from './lib/types'
import { fetchSessions } from './lib/api'
import 'katex/dist/katex.min.css'
import './theme/heartbreaker.css'

function buildProfile(agentId: string): AppProfile {
  const brand = BRANDS[agentId] || BRANDS['speda']
  return { ...brand, accentHover: deriveAccents(brand.accent).bright }
}

function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [profile, setProfile] = useState<AppProfile>(DEFAULT_PROFILE)
  const [config, setConfig] = useState<AppConfig | null>(null)

  useEffect(() => { applyTheme(profile.accent) }, [profile.accent])

  useEffect(() => {
    const load = async () => {
      let cfg: AppConfig
      if (window.api?.getConfig) {
        const raw = await window.api.getConfig()
        cfg = { apiBase: raw.apiBase, apiKey: raw.apiKey, agentId: profile.agentId }
      } else {
        cfg = {
          apiBase: (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8000',
          apiKey: (import.meta.env.VITE_API_KEY as string) || 'dev-key',
          agentId: profile.agentId,
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const switchAgent = useCallback(async (agentId: string) => {
    const next = buildProfile(agentId)
    const prevAccent = profile.accent
    const root = document.getElementById('root')

    // 1. Start the color morph (backgrounds, rims, icons, glass — everything).
    morphTheme(prevAccent, next.accent, 500)

    // 2. Dissolve brand-specific text out (agent name, tagline, prompts).
    root?.classList.add('agent-morphing')

    // 3. At the morph midpoint (~200ms), the text is invisible — swap the
    //    profile state so React renders the new name/tagline/prompts, then
    //    remove the class so the new text fades back in.
    const nextConfig: AppConfig = {
      apiBase: config?.apiBase || 'http://localhost:8000',
      apiKey: config?.apiKey || '',
      agentId,
    }
    await new Promise(r => setTimeout(r, 200))
    setProfile(next)
    setConfig(nextConfig)
    dispatch({ type: 'SET_CONFIG', payload: nextConfig })
    dispatch({ type: 'NEW_CHAT' })
    await new Promise(r => setTimeout(r, 30))
    root?.classList.remove('agent-morphing')

    try {
      const sessions = await fetchSessions(nextConfig)
      dispatch({ type: 'SET_SESSIONS', payload: sessions })
    } catch { /* backend not available */ }
  }, [config, profile.accent])

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
      <ProfileContext.Provider value={profile}>
        <NeuralBackground />
        <HudFrame />
        <Layout profile={profile} config={config} switchAgent={switchAgent} />
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
