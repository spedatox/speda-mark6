import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { SettingsContext, useSettingsProvider } from './store/settings'
import { ProfileContext } from './components/Sidebar'
import DEFAULT_PROFILE from './profile'
import { BRANDS } from './profile/brands'
import {
  applyTheme, morphTheme, deriveAccents,
  startPartyCycle, stopPartyCycle, isPartyCycling,
} from './profile/theme'
import { WARROOM_PROFILE } from './profile/warroom'
import type { AppProfile } from './profile/types'
import Layout from './components/Layout'
import HudFrame from './components/HudFrame'
import NeuralBackground from './components/NeuralBackground'
import PartyActivation from './components/PartyActivation'
import type { AppConfig } from './lib/types'
import { fetchSessions, getHouseParty, setHouseParty } from './lib/api'
import 'katex/dist/katex.min.css'
import './theme/heartbreaker.css'

function buildProfile(agentId: string): AppProfile {
  // The war room is a real profile, not an overlay — switching to it is a
  // takeover exactly like any agent. It just never appears in BRANDS (so it
  // stays out of the switcher menu).
  if (agentId === 'warroom') return WARROOM_PROFILE
  const brand = BRANDS[agentId] || BRANDS['speda']
  return { ...brand, accentHover: deriveAccents(brand.accent).bright }
}

function AppInner() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [profile, setProfile] = useState<AppProfile>(DEFAULT_PROFILE)
  const [config, setConfig] = useState<AppConfig | null>(null)

  // ── House Party Protocol / War Room ───────────────────────────────────────
  // Three states. off: a normal agent. standby: the owner opened the war room
  // from the UI — full branded takeover (roster colour parade, warroom
  // profile) but the protocol is NOT engaged. engaged: the owner told SPEDA to
  // run the protocol (backend flag) — same takeover, plus live dispatch and
  // STAND DOWN. Both war-room states parade the palette through the roster's
  // colours, so the background is never a flat single hue. Entering either
  // plays the activation cinematic; leaving plays the stand-down cinematic.
  type WarMode = 'off' | 'standby' | 'engaged'
  const [warMode, setWarMode] = useState<WarMode>('off')
  const warModeRef = useRef<WarMode>('off')
  const setWar = useCallback((m: WarMode) => { warModeRef.current = m; setWarMode(m) }, [])

  const [activation, setActivation] = useState<'engage' | 'standby' | 'standdown' | null>(null)
  const activationRef = useRef(activation)
  activationRef.current = activation
  /** Where an in-flight ENTER cinematic is heading (standby vs engaged). */
  const pendingRef = useRef<WarMode>('standby')
  /** The agent to return to after stand down — the last non-warroom profile. */
  const prevAgentRef = useRef(DEFAULT_PROFILE.agentId)
  const profileRef = useRef(profile)
  profileRef.current = profile
  const configRef = useRef(config)
  configRef.current = config

  // While the party cycle owns the palette, a profile change must not snap
  // the theme back to a single accent.
  useEffect(() => { if (!isPartyCycling()) applyTheme(profile.accent) }, [profile.accent])

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

  /** Point the chat store + config at `agentId` and reload its sessions. */
  const retarget = useCallback((agentId: string) => {
    const cfg: AppConfig = {
      apiBase: configRef.current?.apiBase || 'http://localhost:8000',
      apiKey: configRef.current?.apiKey || '',
      agentId,
    }
    setConfig(cfg)
    dispatch({ type: 'SET_CONFIG', payload: cfg })
    dispatch({ type: 'NEW_CHAT' })
    fetchSessions(cfg).then(s => dispatch({ type: 'SET_SESSIONS', payload: s })).catch(() => {})
  }, [])

  /** Open the war room from the UI — branded STANDBY (protocol offline). */
  const enterWarRoom = useCallback(() => {
    if (warModeRef.current !== 'off' || activationRef.current) return
    pendingRef.current = 'standby'
    setActivation('standby')
  }, [])

  /** Backend flag engaged from a cold start — full protocol boot. */
  const engageParty = useCallback(() => {
    pendingRef.current = 'engaged'
    setActivation('engage')
  }, [])

  /** Leave the war room entirely (EXIT from standby, or STAND DOWN / flag drop
   *  from engaged). userInitiated stand-downs also clear the backend flag. */
  const exitWarRoom = useCallback((userInitiated: boolean) => {
    if (warModeRef.current === 'off' || activationRef.current) return
    if (userInitiated && warModeRef.current === 'engaged' && configRef.current) {
      setHouseParty(configRef.current, false).catch(() => {})
    }
    setActivation('standdown')
  }, [])

  // Mid-cinematic, screen fully covered: swap the world underneath.
  const igniteActivation = useCallback(() => {
    if (activationRef.current === 'standdown') {
      stopPartyCycle()
      const prev = buildProfile(prevAgentRef.current)
      setProfile(prev)   // applyTheme effect snaps the palette back under the veil
      retarget(prev.agentId)
    } else {
      // Entering the war room (standby or engaged) — same takeover either way.
      const cur = profileRef.current
      if (cur.agentId !== 'warroom') prevAgentRef.current = cur.agentId
      startPartyCycle(cur.accent)
      setProfile(WARROOM_PROFILE)
      retarget('warroom')
    }
  }, [retarget])

  const activationDone = useCallback(() => {
    const mode = activationRef.current
    setActivation(null)
    if (mode === 'standdown') setWar('off')
    else setWar(pendingRef.current)   // enter → 'standby' or 'engaged'
  }, [setWar])

  // Watch the backend flag — the protocol is engaged by TELLING SPEDA.
  useEffect(() => {
    if (!config) return
    let live = true
    const check = async () => {
      const engaged = await getHouseParty(config)
      if (!live || activationRef.current) return   // never interrupt a running cinematic
      const wm = warModeRef.current
      if (engaged && wm === 'off') engageParty()
      else if (engaged && wm === 'standby') setWar('engaged')   // escalate in place — parade already running
      else if (!engaged && wm === 'engaged') exitWarRoom(false)
    }
    check()
    const t = setInterval(check, 4000)
    return () => { live = false; clearInterval(t) }
  }, [config, engageParty, exitWarRoom, setWar])

  const switchAgent = useCallback(async (agentId: string) => {
    // Leaving the war room by picking a real agent from the switcher: route it
    // through the stand-down cinematic, returning to the chosen agent.
    if (agentId !== 'warroom' && warModeRef.current !== 'off') {
      prevAgentRef.current = agentId
      exitWarRoom(true)
      return
    }
    const next = buildProfile(agentId)
    const prevAccent = profile.accent
    const root = document.getElementById('root')
    if (agentId !== 'warroom') prevAgentRef.current = agentId

    // 1. Start the color morph (backgrounds, rims, icons, glass — everything).
    //    Unless the party cycle owns the palette — then it keeps parading.
    if (!isPartyCycling()) morphTheme(prevAccent, next.accent, 500)

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
  }, [config, profile.accent, exitWarRoom])

  if (!config) {
    return (
      <div style={{
        height: '100%', display: 'flex', flexDirection: 'column', gap: '0.5rem',
        alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)',
        fontFamily: "var(--font-mono)", letterSpacing: '0.12em',
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
        <Layout
          profile={profile}
          config={config}
          switchAgent={switchAgent}
          partyEngaged={warMode === 'engaged'}
          inWarRoom={warMode !== 'off'}
          onEnterWarRoom={enterWarRoom}
          onExitWarRoom={() => exitWarRoom(true)}
        />
        {activation && (
          <PartyActivation mode={activation} onIgnite={igniteActivation} onDone={activationDone} />
        )}
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
