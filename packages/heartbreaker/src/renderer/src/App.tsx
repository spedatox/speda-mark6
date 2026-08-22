import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { ChatContext, chatReducer, initialState } from './store/chat'
import { saveMessages } from './store/messageCache'
import { SettingsContext, useSettingsProvider, useSettings } from './store/settings'
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
import NeuralBackground from './components/NeuralBackground'
import PartyActivation from './components/PartyActivation'
import LockdownActivation from './components/LockdownActivation'
import PendingAsksTray from './components/PendingAsksTray'
import ConnectionSetupModal from './components/ConnectionSetupModal'
import { Skeleton } from './components/Skeleton'
import type { AppConfig } from './lib/types'
import { fetchSessions, getHouseParty, setHouseParty, getLockdown } from './lib/api'
import { resolveConnection } from './lib/connection'
import { useT } from './lib/i18n'
import 'katex/dist/katex.min.css'
import './theme/heartbreaker.css'

/** The containment is still on. Deliberately unmissable and unclosable — the
 *  failure this guards against is the owner forgetting the server is sealed and
 *  spending an afternoon debugging "why can't I SSH in". */
function LockdownStrip() {
  const t = useT()
  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9500,
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
      height: 26, pointerEvents: 'none',
      background: 'linear-gradient(180deg, rgba(232,86,74,0.9), rgba(232,86,74,0.55))',
      borderBottom: '1px solid rgba(232,86,74,0.9)',
      fontFamily: 'var(--font-mono)', fontSize: '0.58rem',
      letterSpacing: '0.28em', textTransform: 'uppercase', color: '#fff',
      textShadow: '0 1px 2px rgba(0,0,0,0.5)',
    }}>
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
        <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
      {t.app.lockdownStripActive}
    </div>
  )
}

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
  // Raised on boot when nothing (env, a build-time bake, or a prior save) chose
  // a real server — see lib/connection.ts. `firstRun` only changes the copy;
  // Settings → Account reopens the same modal with it false.
  const [connectionPrompt, setConnectionPrompt] = useState<{ firstRun: boolean } | null>(null)

  // Turkish's special-casing rules (dotless ı/İ) only apply to CSS
  // `text-transform: uppercase` — used all over the deck's FUI labels — when
  // the document's language is actually declared. Without this, every such
  // label in Turkish renders with an ASCII-only uppercase pass.
  //
  // The flip side: brand text (agent name, model number, tagline — all English
  // constants from profile/brands.ts) must NOT get those rules, or Atomix reads
  // ATOMİX and Sentinel SENTİNEL. Every element carrying brand text declares
  // lang="en" of its own; see ChatMain, Sidebar and AgentSwitcherOverlay.
  const { settings: rootSettings } = useSettings()
  useEffect(() => {
    document.documentElement.lang = rootSettings.locale
  }, [rootSettings.locale])

  // ── House Party Protocol / War Room ───────────────────────────────────────
  // Three states. off: a normal agent. standby: the owner opened the war room
  // from the UI — full branded takeover (roster colour parade, warroom
  // profile) but the protocol is NOT engaged. engaged: the owner told Speda to
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

  // Mirror each session's transcript to local storage as turns SETTLE (not on
  // every streamed chunk — cheap, and the point is durability, not liveness). A
  // finished OR errored turn flips isStreaming off, so this captures the answer
  // even when the connection dropped mid-turn and the server never saved it.
  // Read back offline by the session loader (store/messageCache).
  useEffect(() => {
    if (state.isStreaming) return
    const sid = state.activeSessionId
    if (sid == null || !state.messages.length) return
    saveMessages(configRef.current?.agentId ?? profile.agentId, sid, state.messages)
  }, [state.isStreaming, state.messages, state.activeSessionId, profile.agentId])

  useEffect(() => {
    const load = async () => {
      const resolved = await resolveConnection()
      const cfg: AppConfig = { apiBase: resolved.apiBase, apiKey: resolved.apiKey, agentId: profile.agentId }
      dispatch({ type: 'SET_CONFIG', payload: cfg })
      setConfig(cfg)
      // Never for a local dev run — it already has a working default. Otherwise,
      // nobody chose this address, so ask instead of silently sitting on it.
      if (!resolved.configured && !resolved.isDev) setConnectionPrompt({ firstRun: true })
      try {
        const sessions = await fetchSessions(cfg)
        dispatch({ type: 'SET_SESSIONS', payload: sessions })
      } catch {
        // Backend unreachable — still mark sessions "loaded" (empty) so the
        // sidebar shows "No sessions yet" instead of skeletoning forever.
        dispatch({ type: 'SET_SESSIONS', payload: [] })
      }
    }
    load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Settings → Account's "Edit" reopens the same modal, outside first-run.
  useEffect(() => {
    const onOpen = () => setConnectionPrompt({ firstRun: false })
    window.addEventListener('speda:connection-setup', onOpen)
    return () => window.removeEventListener('speda:connection-setup', onOpen)
  }, [])

  const handleConnectionSaved = useCallback((apiBase: string, apiKey: string) => {
    const nextConfig: AppConfig = { apiBase, apiKey, agentId: configRef.current?.agentId ?? profile.agentId }
    setConfig(nextConfig)
    dispatch({ type: 'SET_CONFIG', payload: nextConfig })
    fetchSessions(nextConfig)
      .then(s => dispatch({ type: 'SET_SESSIONS', payload: s }))
      .catch(() => dispatch({ type: 'SET_SESSIONS', payload: [] }))
  }, [profile.agentId])

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
    dispatch({ type: 'SESSIONS_LOADING' })
    fetchSessions(cfg)
      .then(s => dispatch({ type: 'SET_SESSIONS', payload: s }))
      .catch(() => dispatch({ type: 'SET_SESSIONS', payload: [] }))
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

  // Watch the backend flag — the protocol is engaged by TELLING Speda.
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

  // Instant trigger: the House Party authorization modal fires this the moment
  // the backend accepts the passphrase, so the war room ignites without waiting
  // for the 4s poll above (which stays as the fallback / stand-down watcher).
  useEffect(() => {
    const onEngaged = () => {
      if (activationRef.current) return
      const wm = warModeRef.current
      if (wm === 'off') engageParty()
      else if (wm === 'standby') setWar('engaged')
    }
    window.addEventListener('speda:hpp-engaged', onEngaged)
    return () => window.removeEventListener('speda:hpp-engaged', onEngaged)
  }, [engageParty, setWar])

  // ── Lockdown Protocol ─────────────────────────────────────────────────────
  // Independent of the war room: containment can be engaged while the roster is
  // mobilized or while nothing else is happening, so it gets its own flag, its
  // own cinematic, and a persistent indicator (the cinematic is one-shot; the
  // strip is how the owner knows it is STILL on).
  const [lockdown, setLockdown] = useState(false)
  const lockdownRef = useRef(false)
  const [lockCine, setLockCine] = useState<'seal' | 'release' | null>(null)
  const lockCineRef = useRef<'seal' | 'release' | null>(null)
  lockCineRef.current = lockCine

  useEffect(() => {
    if (!config) return
    let live = true
    const check = async () => {
      const state = await getLockdown(config)
      if (!live || lockCineRef.current) return   // never interrupt a running cinematic
      if (state.engaged && !lockdownRef.current) setLockCine('seal')
      else if (!state.engaged && lockdownRef.current) setLockCine('release')
    }
    check()
    const t = setInterval(check, 4000)
    return () => { live = false; clearInterval(t) }
  }, [config])

  // Instant trigger from the authorization modal, so the seal plays immediately
  // rather than up to 4s later on the poll above.
  useEffect(() => {
    const onEngaged = () => { if (!lockCineRef.current && !lockdownRef.current) setLockCine('seal') }
    window.addEventListener('speda:lockdown-engaged', onEngaged)
    return () => window.removeEventListener('speda:lockdown-engaged', onEngaged)
  }, [])

  const lockIgnite = useCallback(() => {
    const sealing = lockCineRef.current === 'seal'
    lockdownRef.current = sealing
    setLockdown(sealing)
  }, [])

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
    dispatch({ type: 'SESSIONS_LOADING' })
    await new Promise(r => setTimeout(r, 30))
    root?.classList.remove('agent-morphing')

    try {
      const sessions = await fetchSessions(nextConfig)
      dispatch({ type: 'SET_SESSIONS', payload: sessions })
    } catch {
      dispatch({ type: 'SET_SESSIONS', payload: [] })
    }
  }, [config, profile.accent, exitWarRoom])

  if (!config) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-primary)',
      }}>
        <div style={{ width: 220, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Skeleton height={16} width="70%" />
          <Skeleton height={10} width="45%" />
        </div>
      </div>
    )
  }

  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      <ProfileContext.Provider value={profile}>
        <NeuralBackground />
        {/* HudFrame (the fixed top telemetry strip) is deliberately NOT mounted.
            Every readout it carried — host, link state, RTT, tool count, active
            model, session count — now lives in the deck's telemetry column,
            where it is readable rather than 10px tall, and the clock is on the
            welcome screen. It also drew corner brackets, which the design
            contract bans. The component is kept on disk for reference. */}
        <Layout
          profile={profile}
          config={config}
          switchAgent={switchAgent}
          partyEngaged={warMode === 'engaged'}
          inWarRoom={warMode !== 'off'}
          onEnterWarRoom={enterWarRoom}
          onExitWarRoom={() => exitWarRoom(true)}
        />
        <PendingAsksTray config={config} />
        {connectionPrompt && (
          <ConnectionSetupModal
            initialApiBase={config.apiBase}
            initialApiKey={config.apiKey}
            firstRun={connectionPrompt.firstRun}
            onClose={() => setConnectionPrompt(null)}
            onSaved={handleConnectionSaved}
          />
        )}
        {lockdown && <LockdownStrip />}
        {activation && (
          <PartyActivation mode={activation} onIgnite={igniteActivation} onDone={activationDone} />
        )}
        {lockCine && (
          <LockdownActivation mode={lockCine} onIgnite={lockIgnite} onDone={() => setLockCine(null)} />
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
