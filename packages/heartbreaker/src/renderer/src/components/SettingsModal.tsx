import { useState, useEffect, useCallback, useRef } from 'react'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { useChatContext } from '../store/chat'
import { importChats, fetchSessions, indexHistory, getConnections, setConnection, googleLoginUrl, googleStatus, googleDisconnect, notionLoginUrl, notionStatus, notionDisconnect, getAutomations, toggleAutomation, deleteAutomation, getAutomationsStatus, telegramConnect, telegramStatus } from '../lib/api'
import { memoryStatus } from '../lib/api'
import type { ConnectionInfo, AutomationInfo, AutomationsStatus, MemoryStatus } from '../lib/api'
import RemindersTab from './RemindersTab'
import ProtocolsTab from './ProtocolsTab'
import type { AppConfig } from '../lib/types'
import ConfigTab from './ConfigTab'
import GlassSelect from './GlassSelect'
import {
  SettingsSection, SettingsField, SettingsRow, Switch, PillBtn, ServiceRow, LiveDot, fieldStyle,
} from './settingsUI'

/** Languages replies can be spoken in — mirrors VoiceMode's own switcher. */
const VOICE_LOCALES = [
  { value: 'en-US', label: 'English' },
  { value: 'tr-TR', label: 'Türkçe' },
]

interface Props {
  config: AppConfig
  onClose: () => void
  /** Opens the Lockdown authorization modal. Owned by Layout so the modal
   *  renders above this window rather than inside its scroll pane. */
  onEngageLockdown: () => void
}

type Tab = 'general' | 'config' | 'connections' | 'automations' | 'reminders' | 'protocols' | 'interface' | 'data' | 'account'

/* ── Rail glyphs ─────────────────────────────────────────────────────────── */
const ico = { width: 17, height: 17, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6 } as const
const IcoGear = () => <svg {...ico}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
const IcoSliders = () => <svg {...ico} strokeLinecap="round"><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></svg>
const IcoLink = () => <svg {...ico}><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1-1" /></svg>
const IcoBolt = () => <svg {...ico} strokeLinejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" /></svg>
const IcoCalendar = () => <svg {...ico}><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg>
const IcoMonitor = () => <svg {...ico}><rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>
const IcoDatabase = () => <svg {...ico}><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5" /><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3" /></svg>
const IcoUser = () => <svg {...ico}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
const IcoShield = () => <svg {...ico}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></svg>


export default function SettingsModal({ config, onClose, onEngageLockdown }: Props) {
  const { settings, update } = useSettings()
  const { dispatch } = useChatContext()
  const profile = useProfile()
  const [tab, setTab] = useState<Tab>('general')
  const [localPrompt, setLocalPrompt] = useState(settings.systemPrompt)
  const [localTemp, setLocalTemp] = useState(settings.temperature)
  const [localUserName, setLocalUserName] = useState(settings.userName || profile?.userName || '')

  // ── Chat import ──────────────────────────────────────────────────────────
  const fileRef = useRef<HTMLInputElement>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importStatus, setImportStatus] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle')
  const [importMsg, setImportMsg] = useState('')

  const [indexStatus, setIndexStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [indexMsg, setIndexMsg] = useState('')
  const [memory, setMemory] = useState<MemoryStatus | null>(null)

  // ── Connections ───────────────────────────────────────────────────────────
  const [conns, setConns] = useState<ConnectionInfo[]>([])
  const [connBudget, setConnBudget] = useState({ used: 0, limit: 30000 })
  const loadConns = async () => {
    const r = await getConnections(config)
    setConns(r.servers)
    setConnBudget({ used: r.active_tool_tokens, limit: r.itpm_limit })
  }
  useEffect(() => { if (tab === 'connections') loadConns() }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps
  const toggleConn = async (server: string, active: boolean) => {
    setConns(cs => cs.map(c => c.server === server ? { ...c, active } : c)) // optimistic
    await setConnection(config, server, active)
    loadConns()
  }
  // ── Automations ───────────────────────────────────────────────────────────
  const [autos, setAutos] = useState<AutomationInfo[]>([])
  const [autoStatus, setAutoStatus] = useState<AutomationsStatus | null>(null)
  const [tgMsg, setTgMsg] = useState('')
  const loadAutos = async () => {
    setAutos(await getAutomations(config))
    setAutoStatus(await getAutomationsStatus(config))
  }
  useEffect(() => { if (tab === 'automations') loadAutos() }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps
  const handleToggleAuto = async (id: number, active: boolean) => {
    setAutos(as => as.map(a => a.id === id ? { ...a, active } : a)) // optimistic
    await toggleAutomation(config, id, active)
    loadAutos()
  }
  const handleDeleteAuto = async (id: number) => {
    setAutos(as => as.filter(a => a.id !== id))
    await deleteAutomation(config, id)
    loadAutos()
  }
  const handleTelegramConnect = async () => {
    setTgMsg('Opening Telegram…')
    const r = await telegramConnect(config)
    if (r.error || !r.link) { setTgMsg(r.error || 'Could not start the connect flow.'); return }
    window.api?.openExternal ? window.api.openExternal(r.link) : window.open(r.link, '_blank')
    setTgMsg('Tap START in Telegram — connecting automatically…')
    let n = 0
    const poll = setInterval(async () => {
      n++
      const s = await telegramStatus(config)
      if (s.connected) {
        clearInterval(poll)
        setTgMsg('')
        loadAutos()
      } else if (n > 40) {
        clearInterval(poll)
        setTgMsg('No response from Telegram — try Connect again.')
      }
    }, 3000)
  }

  const [googleMsg, setGoogleMsg] = useState('')
  const [googleConnected, setGoogleConnected] = useState(false)
  useEffect(() => { googleStatus(config).then(setGoogleConnected) }, [config])

  const signInGoogle = async () => {
    setGoogleMsg('Opening Google sign-in…')
    const r = await googleLoginUrl(config)
    if (r.auth_url) {
      window.api?.openExternal ? window.api.openExternal(r.auth_url) : window.open(r.auth_url, '_blank')
      setGoogleMsg('Complete sign-in in your browser, then come back — it connects automatically.')
      // Poll for the connection to come online, then flip the UI to "connected".
      let n = 0
      const poll = setInterval(async () => {
        n++
        await loadConns()
        if (await googleStatus(config)) { setGoogleConnected(true); setGoogleMsg(''); clearInterval(poll) }
        else if (n > 20) clearInterval(poll)
      }, 3000)
    } else {
      setGoogleMsg(r.error || 'Could not start Google sign-in.')
    }
  }

  const disconnectGoogle = async () => {
    await googleDisconnect(config)
    setGoogleConnected(false)
    setGoogleMsg('')
    loadConns()
  }

  const [notionMsg, setNotionMsg] = useState('')
  const [notionConnected, setNotionConnected] = useState(false)
  useEffect(() => { notionStatus(config).then(setNotionConnected) }, [config])

  const signInNotion = async () => {
    setNotionMsg('Opening Notion sign-in…')
    const r = await notionLoginUrl(config)
    if (r.auth_url) {
      window.api?.openExternal ? window.api.openExternal(r.auth_url) : window.open(r.auth_url, '_blank')
      setNotionMsg('Complete sign-in in your browser, then come back — it connects automatically.')
      let n = 0
      const poll = setInterval(async () => {
        n++
        await loadConns()
        if (await notionStatus(config)) { setNotionConnected(true); setNotionMsg(''); clearInterval(poll) }
        else if (n > 20) clearInterval(poll)
      }, 3000)
    } else {
      setNotionMsg(r.error || 'Could not start Notion sign-in.')
    }
  }

  const disconnectNotion = async () => {
    await notionDisconnect(config)
    setNotionConnected(false)
    setNotionMsg('')
    loadConns()
  }

  // The rebuild runs on the backend's durable job queue, so the button's job is
  // to start it and then keep showing the verdict — a bare "started in the
  // background" would leave the one thing worth reading (whether anything is at
  // risk) invisible. Polling stops on its own when the job settles.
  const pollMemory = useCallback(async () => {
    try {
      const s = await memoryStatus(config)
      setMemory(s)
      const running = s.job?.status === 'pending' || s.job?.status === 'running'
      setIndexStatus(running ? 'running' : s.job?.status === 'failed' ? 'error' : 'done')
      setIndexMsg(
        s.job?.status === 'failed'
          ? `Rebuild failed after ${s.job.attempts} attempt(s): ${s.job.last_error ?? 'unknown error'}`
          : `${s.observations} fact(s) recorded — ${s.verdict}`
      )
      return running
    } catch {
      return false
    }
  }, [config])

  useEffect(() => {
    if (indexStatus !== 'running') return
    const t = setInterval(() => { void pollMemory() }, 5000)
    return () => clearInterval(t)
  }, [indexStatus, pollMemory])

  // Show where memory stands as soon as the tab is opened, without pressing
  // anything — an empty record is worth knowing about before it is needed.
  useEffect(() => {
    if (tab === 'data') void pollMemory()
  }, [tab, pollMemory])

  const handleIndex = async () => {
    if (indexStatus === 'running') return
    setIndexStatus('running')
    setIndexMsg('Rebuilding memory from your whole history…')
    try {
      const res = await indexHistory(config)
      if (!res.accepted) {
        setIndexMsg(res.message)
      }
      await pollMemory()
    } catch (e) {
      setIndexStatus('error')
      setIndexMsg(e instanceof Error ? e.message : 'Rebuild failed')
    }
  }

  const handleImport = async () => {
    if (!importFile || importStatus === 'uploading') return
    setImportStatus('uploading')
    setImportMsg('Uploading & starting import…')
    try {
      const res = await importChats(config, importFile)
      setImportStatus('done')
      setImportMsg(res.message || 'Import started in the background.')
      // The import runs server-side; poll a few times so sessions populate live.
      const refresh = async () => {
        try {
          const s = await fetchSessions(config)
          dispatch({ type: 'SET_SESSIONS', payload: s })
        } catch { /* non-fatal */ }
      }
      refresh()
      setTimeout(refresh, 5000)
      setTimeout(refresh, 15000)
      setTimeout(refresh, 40000)
    } catch (e) {
      setImportStatus('error')
      setImportMsg(e instanceof Error ? e.message : 'Import failed')
    }
  }

  // Debounced save for system prompt
  useEffect(() => {
    const t = setTimeout(() => update({ systemPrompt: localPrompt }), 400)
    return () => clearTimeout(t)
  }, [localPrompt]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { update({ temperature: localTemp }) }, [localTemp]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // Each row carries its glyph. A settings rail of eight identical text labels
  // is read linearly every time; with marks the owner goes straight to the one
  // they want. `blurb` is the line under the pane title — what this tab is for,
  // said once, instead of the owner inferring it from the fields.
  const tabs: { id: Tab; label: string; blurb: string; icon: React.ReactNode }[] = [
    { id: 'general', label: 'General', blurb: 'Identity, behaviour and voice-mode preferences', icon: <IcoGear /> },
    { id: 'config', label: 'Configuration', blurb: 'Managed server settings, applied live', icon: <IcoSliders /> },
    { id: 'connections', label: 'Connections', blurb: 'Accounts and services SPEDA can reach', icon: <IcoLink /> },
    { id: 'automations', label: 'Automations', blurb: 'Scheduled work n8n runs on your behalf', icon: <IcoBolt /> },
    { id: 'reminders', label: 'Reminders', blurb: 'What SPEDA is holding for you, and when', icon: <IcoCalendar /> },
    { id: 'protocols', label: 'Protocols', blurb: 'Standing operational modes — containment and all-hands', icon: <IcoShield /> },
    { id: 'interface', label: 'Interface', blurb: 'How the deck itself looks and behaves', icon: <IcoMonitor /> },
    { id: 'data', label: 'Data', blurb: 'Stored history, memory files and exports', icon: <IcoDatabase /> },
    { id: 'account', label: 'Account', blurb: 'The owner this client is signed in as', icon: <IcoUser /> },
  ]
  const current = tabs.find(t => t.id === tab)

  return (
    <div
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,0.65)',
        backdropFilter: 'blur(6px)',
        animation: 'fadeIn 0.15s ease',
      }}
    >
      {/* The settings surface is a proper WINDOW, not a dialog box — it is where
          the owner configures the whole system, and the old 720×600 panel made
          eight tabs of real content scroll in a letterbox. */}
      <div className="hb-island" style={{
        width: 'min(1240px, 94vw)', height: 'min(820px, 90vh)',
        background: 'var(--glass-sheen), var(--glass-fill)',
        backdropFilter: 'blur(44px) saturate(170%)',
        WebkitBackdropFilter: 'blur(44px) saturate(170%)',
        border: '1px solid rgba(255,255,255,0.12)',
        display: 'flex',
        overflow: 'hidden',
        animation: 'modalIn 0.15s ease',
        boxShadow: 'inset 0 1.5px 0 rgba(255,255,255,0.22), 0 40px 90px rgba(0,0,0,0.55)',
        position: 'relative',
      }}>
        {/* Left nav */}
        <div style={{
          width: 260, flexShrink: 0,
          borderRight: '1px solid rgba(255,255,255,0.07)',
          padding: '26px 16px',
          display: 'flex', flexDirection: 'column', gap: 2,
        }}>
          <div style={{
            fontFamily: 'var(--font-ui)', fontSize: '1.375rem', fontWeight: 700,
            letterSpacing: '0.04em', color: 'var(--hb-text)',
            padding: '0 10px', marginBottom: 20,
          }}>
            Settings
          </div>
          {tabs.map(({ id, label, icon }) => {
            const on = tab === id
            return (
              <button
                key={id}
                onClick={() => setTab(id)}
                className="hb-tile"
                style={{
                  width: '100%', height: 44, padding: '0 14px',
                  display: 'flex', alignItems: 'center', gap: 11, textAlign: 'left',
                  border: on ? '1px solid rgba(var(--hb-accent-rgb),0.3)' : '1px solid transparent',
                  background: on
                    ? 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.18), rgba(var(--hb-accent-rgb),0.05))'
                    : 'transparent',
                  color: on ? 'var(--hb-text)' : 'var(--hb-text-dim)',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-read)',
                  fontSize: '0.9375rem', fontWeight: on ? 500 : 400,
                  transition: 'background 0.12s, color 0.12s, border-color 0.12s',
                }}
                onMouseEnter={e => { if (!on) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.04)' }}
                onMouseLeave={e => { if (!on) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
              >
                <span style={{ display: 'flex', color: on ? 'var(--hb-cyan-bright)' : 'currentColor' }}>{icon}</span>
                {label}
              </button>
            )
          })}
        </div>

        {/* Right content */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
          {/* The pane names itself in its own content, the way the reference
              deck does — no separate title bar plate above it. */}
          <button
            onClick={onClose}
            title="Close (Esc)"
            className="hb-tile"
            style={{
              position: 'absolute', top: 22, right: 26, zIndex: 2,
              width: 38, height: 38,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: '1px solid rgba(255,255,255,0.08)',
              background: 'var(--glass-sheen)',
              color: 'var(--hb-text-dim)',
              cursor: 'pointer', transition: 'color 0.12s, border-color 0.12s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-text)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-text-dim)' }}
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>

          {/* Scrollable content */}
          <div className="hb-settings" style={{ flex: 1, overflowY: 'auto', padding: '30px 36px' }}>
            <div style={{
              fontFamily: 'var(--font-ui)', fontSize: '1.5rem', fontWeight: 700,
              letterSpacing: '0.03em', color: 'var(--hb-text)', marginBottom: 4,
              paddingRight: 56,
            }}>
              {current?.label}
            </div>
            <div style={{ fontSize: '0.905rem', color: 'var(--hb-text-faint)', marginBottom: 26 }}>
              {current?.blurb}
            </div>

            {/* General tab */}
            {tab === 'general' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
                <SettingsField label="Display name">
                  <input
                    className="hb-tile"
                    value={settings.userName}
                    onChange={e => update({ userName: e.target.value })}
                    placeholder={profile?.userName ?? 'Your name'}
                    style={fieldStyle}
                    onFocus={e => (e.currentTarget as HTMLInputElement).style.borderColor = 'rgba(var(--hb-accent-rgb),0.45)'}
                    onBlur={e => (e.currentTarget as HTMLInputElement).style.borderColor = 'rgba(255,255,255,0.09)'}
                  />
                </SettingsField>

                <SettingsField
                  label="System instruction"
                  hint="Defines the agent's behaviour and personality for every conversation."
                >
                  <textarea
                    className="hb-tile"
                    value={localPrompt}
                    onChange={e => setLocalPrompt(e.target.value)}
                    placeholder="You are a helpful assistant…"
                    rows={4}
                    style={{
                      ...fieldStyle,
                      height: 96, padding: '12px 16px',
                      lineHeight: 1.55, resize: 'vertical',
                    }}
                    onFocus={e => (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'rgba(var(--hb-accent-rgb),0.45)'}
                    onBlur={e => (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'rgba(255,255,255,0.09)'}
                  />
                </SettingsField>

                <div>
                  <div style={{
                    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                    marginBottom: 10,
                  }}>
                    <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)' }}>
                      Creativity (temperature)
                    </span>
                    <span style={{
                      fontSize: '0.845rem', color: 'var(--hb-cyan)',
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      {localTemp.toFixed(1)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0} max={1} step={0.1}
                    value={localTemp}
                    onChange={e => setLocalTemp(parseFloat(e.target.value))}
                    style={{ width: '100%' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
                    <span style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>Precise</span>
                    <span style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>Creative</span>
                  </div>
                </div>

                <SettingsRow
                  title="Voice-mode language"
                  desc="Used when replies are read aloud."
                >
                  <GlassSelect
                    value={settings.voiceLocale}
                    options={VOICE_LOCALES}
                    onChange={v => update({ voiceLocale: v })}
                    tint="var(--hb-cyan-bright)"
                    title="Language replies are spoken in"
                  />
                </SettingsRow>
              </div>
            )}

            {/* Configuration tab — every backend setting, API keys included */}
            {tab === 'config' && <ConfigTab config={config} />}

            {/* Connections tab — toggle MCP servers live */}
            {tab === 'connections' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
                <SettingsSection title="Accounts" first />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: -8 }}>
                  <ServiceRow
                    tint="#5cc98f"
                    icon={<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>}
                    name="Google Workspace"
                    desc={googleConnected
                      ? 'Gmail, Calendar, Drive and Contacts are live.'
                      : 'Sign in for Gmail, Calendar and Drive.'}
                  >
                    {googleConnected ? (
                      <>
                        <LiveDot />
                        <PillBtn onClick={disconnectGoogle} tone="danger">Disconnect</PillBtn>
                      </>
                    ) : (
                      <PillBtn onClick={signInGoogle}>
                        <svg width="14" height="14" viewBox="0 0 48 48">
                          <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.8c-.5 2.7-2 5-4.4 6.6v5.5h7.1c4.1-3.8 6.6-9.4 6.6-16.1z"/>
                          <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.4l-7.1-5.5c-2 1.3-4.5 2.1-7.4 2.1-5.7 0-10.5-3.8-12.2-9h-7.3v5.7C8.1 41.1 15.4 46 24 46z"/>
                          <path fill="#FBBC05" d="M11.8 28.2c-.4-1.3-.7-2.7-.7-4.2s.2-2.9.7-4.2v-5.7H4.5C3 17.2 2.1 20.5 2.1 24s.9 6.8 2.4 9.9l7.3-5.7z"/>
                          <path fill="#EA4335" d="M24 10.7c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3C34.9 4.1 29.9 2 24 2 15.4 2 8.1 6.9 4.5 14.1l7.3 5.7c1.7-5.2 6.5-9.1 12.2-9.1z"/>
                        </svg>
                        Connect
                      </PillBtn>
                    )}
                  </ServiceRow>
                  {googleMsg && (
                    <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '0 0 0 4px' }}>
                      {googleMsg}
                    </p>
                  )}

                  <ServiceRow
                    icon={<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M4.45877 4.54133L19.5397 5.25732V6.33128L18.8252 6.47466C18.2526 6.54632 17.8938 6.76132 17.8938 7.33465V18.1532C17.8938 18.5832 18.2526 18.7982 18.8252 18.8698L19.5397 19.0132V20.0872L12.3924 19.3712V18.2972L13.107 18.1539C13.6796 18.0822 14.0384 17.8672 14.0384 17.2939V8.83863L7.75338 18.6545H6.53612V8.04996C6.53612 7.47664 6.17737 7.26164 5.6047 7.18997L4.89018 7.04664V5.97268L12.0374 6.68867V7.76262L11.3229 7.90595C10.7503 7.97762 10.3915 8.19262 10.3915 8.76595V16.3626L15.932 7.90595C16.1466 7.61929 16.4328 7.40429 16.7196 7.33262L17.2917 7.18929L17.1486 6.11532L4.45877 4.54133Z"/></svg>}
                    name="Notion"
                    desc="Search, fetch and create pages in your workspace."
                  >
                    {notionConnected ? (
                      <>
                        <LiveDot />
                        <PillBtn onClick={disconnectNotion} tone="danger">Disconnect</PillBtn>
                      </>
                    ) : (
                      <PillBtn onClick={signInNotion}>Connect</PillBtn>
                    )}
                  </ServiceRow>
                  {notionMsg && (
                    <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '0 0 0 4px' }}>
                      {notionMsg}
                    </p>
                  )}
                </div>

                <SettingsSection title="Tool servers" />
                <div style={{ marginTop: -8 }}>
                  <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: '0 0 14px' }}>
                    Turning one off hides its tools instantly — no restart. That shrinks the
                    prompt and keeps you under the rate limit.
                  </p>

                  {/* Prefix budget bar */}
                  {(() => {
                    const pct = Math.min(100, Math.round((connBudget.used / connBudget.limit) * 100))
                    const over = connBudget.used > connBudget.limit
                    const col = over ? '#e5897c' : pct > 80 ? 'var(--hb-amber-bright)' : 'var(--hb-cyan)'
                    return (
                      <div style={{ marginBottom: 16 }}>
                        <div style={{
                          display: 'flex', justifyContent: 'space-between',
                          fontSize: '0.845rem', marginBottom: 8,
                        }}>
                          <span style={{ color: 'var(--hb-text-dim)' }}>Active tool tokens</span>
                          <span style={{ color: col, fontVariantNumeric: 'tabular-nums' }}>
                            ~{connBudget.used.toLocaleString()} / {connBudget.limit.toLocaleString()}
                          </span>
                        </div>
                        <div className="hb-round" style={{
                          height: 6, background: 'rgba(255,255,255,0.07)', overflow: 'hidden',
                        }}>
                          <div style={{ width: `${pct}%`, height: '100%', background: col, transition: 'width 0.2s' }} />
                        </div>
                        {over && (
                          <p style={{ fontSize: '0.8125rem', color: '#e5897c', marginTop: 8 }}>
                            Over the 30k cold-write limit — turn a server off (Notion is heaviest) or upgrade to Tier 2.
                          </p>
                        )}
                      </div>
                    )
                  })()}

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {conns.length === 0 && (
                      <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>
                        No MCP servers loaded.
                      </p>
                    )}
                    {conns.map(c => (
                      <ServiceRow
                        key={c.server}
                        tint={c.connected ? '#5cc98f' : undefined}
                        icon={<span style={{
                          width: 8, height: 8, borderRadius: '50%',
                          background: c.connected ? 'var(--hb-green)' : 'var(--hb-text-faint)',
                          boxShadow: c.connected ? '0 0 6px var(--hb-green)' : 'none',
                        }} />}
                        name={c.label}
                        desc={c.connected
                          ? `${c.tools} tools · ${c.always_on ? 'always on' : 'loaded on demand'}`
                          : (c.needs ? `needs ${c.needs}` : 'offline')}
                      >
                        <Switch
                          on={c.active && c.connected}
                          disabled={!c.connected}
                          onChange={v => toggleConn(c.server, v)}
                          title={c.connected
                            ? (c.active ? 'Active — click to disable' : 'Disabled — click to enable')
                            : 'Not connected'}
                        />
                      </ServiceRow>
                    ))}
                  </div>

                  <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: '14px 0 0' }}>
                    A server shown as “needs …” wants its API key in the backend{' '}
                    <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--hb-cyan)' }}>.env</code>, then a restart.
                  </p>
                </div>
              </div>
            )}

            {/* Reminders tab — standing questions that nag until answered */}
            {tab === 'reminders' && <RemindersTab config={config} />}
            {tab === 'protocols' && <ProtocolsTab config={config} onEngageLockdown={onEngageLockdown} />}

            {/* Automations tab — SPEDA's proactive n8n watchers */}
            {tab === 'automations' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
                <SettingsSection title="Pipeline" first />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: -8 }}>
                  {[
                    {
                      label: 'n8n engine',
                      ok: !!autoStatus?.n8n_online,
                      detail: !autoStatus?.n8n_configured
                        ? 'needs N8N_API_KEY in the backend .env (n8n → Settings → n8n API)'
                        : autoStatus?.n8n_online ? autoStatus.n8n_url : 'unreachable — is the n8n container running?',
                    },
                    {
                      label: 'Telegram delivery',
                      ok: !!autoStatus?.telegram_connected,
                      detail: !autoStatus?.telegram_configured
                        ? 'needs TELEGRAM_BOT_TOKEN in the backend .env (create a bot with @BotFather)'
                        : autoStatus?.telegram_connected ? 'connected — SPEDA can reach you' : 'bot ready — connect your chat below',
                    },
                  ].map(row => (
                    <ServiceRow
                      key={row.label}
                      tint={row.ok ? '#5cc98f' : '#d99c44'}
                      icon={<span style={{
                        width: 8, height: 8, borderRadius: '50%',
                        background: row.ok ? 'var(--hb-green)' : 'var(--hb-amber)',
                        boxShadow: row.ok ? '0 0 6px var(--hb-green)' : 'none',
                      }} />}
                      name={row.label}
                      desc={row.detail}
                    >
                      {row.ok ? <LiveDot label="Online" /> : (
                        <span style={{ fontSize: '0.845rem', color: 'var(--hb-amber-bright)' }}>Not ready</span>
                      )}
                    </ServiceRow>
                  ))}

                  {autoStatus?.telegram_configured && !autoStatus?.telegram_connected && (
                    <div>
                      <PillBtn onClick={handleTelegramConnect} tone="accent">Connect Telegram</PillBtn>
                      {tgMsg && (
                        <p style={{ marginTop: 8, fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
                          {tgMsg}
                        </p>
                      )}
                    </div>
                  )}
                </div>

                <SettingsSection title="Watchers" />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: -8 }}>
                  {autos.length === 0 && (
                    <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>
                      Nothing is being watched yet.
                    </p>
                  )}
                  {autos.map(a => (
                    <div key={a.id} className="hb-tile" style={{
                      display: 'flex', alignItems: 'center', gap: 12,
                      padding: '12px 16px',
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.07)',
                      opacity: a.active ? 1 : 0.55,
                    }}>
                      <span className="glass-round" style={{
                        flexShrink: 0, height: 24, padding: '0 11px',
                        display: 'inline-flex', alignItems: 'center',
                        fontSize: '0.75rem',
                        color: a.active ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
                        background: a.active ? 'rgba(var(--hb-accent-rgb),0.12)' : 'rgba(255,255,255,0.03)',
                        border: `1px solid ${a.active ? 'rgba(var(--hb-accent-rgb),0.3)' : 'rgba(255,255,255,0.08)'}`,
                      }}>
                        {{ web_watch: 'web', rss_watch: 'rss', schedule: 'cron', webhook: 'hook' }[a.kind] ?? a.kind}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: '0.9375rem', color: 'var(--hb-text)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {a.name}
                        </div>
                        <div style={{
                          fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 2,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {a.summary}
                          {a.last_fired_at && ` · last fired ${new Date(a.last_fired_at).toLocaleString()}`}
                          {a.expires_at && ` · until ${new Date(a.expires_at).toLocaleDateString()}`}
                        </div>
                      </div>
                      <Switch
                        on={a.active}
                        onChange={v => handleToggleAuto(a.id, v)}
                        title={a.active ? 'Active — click to pause' : 'Paused — click to resume'}
                      />
                      <button
                        onClick={() => handleDeleteAuto(a.id)}
                        title="Delete watcher (also removes the n8n workflow)"
                        className="hb-tile"
                        style={{
                          width: 30, height: 30, flexShrink: 0,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          border: '1px solid transparent', background: 'transparent',
                          color: 'var(--hb-text-faint)', cursor: 'pointer', transition: 'color 0.12s',
                        }}
                        onMouseEnter={e => ((e.currentTarget as HTMLButtonElement).style.color = '#e5897c')}
                        onMouseLeave={e => ((e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-text-faint)')}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
                          <polyline points="3 6 5 6 21 6"/>
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                          <path d="M10 11v6M14 11v6"/>
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: 0 }}>
                  SPEDA creates these itself — just ask:{' '}
                  <em style={{ color: 'var(--hb-amber-bright)', fontStyle: 'normal' }}>
                    “track this page for a month and tell me when my results are up”
                  </em>. When a watcher fires, SPEDA composes the message and pings you on Telegram.
                </p>
              </div>
            )}

            {/* Interface tab */}
            {tab === 'interface' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
                <SettingsField label="Theme">
                  <div style={{ display: 'flex', gap: 10 }}>
                    {['Dark', 'Light'].map(t => (
                      <button
                        key={t}
                        className="glass-round"
                        style={{
                          height: 38, padding: '0 20px',
                          fontFamily: 'var(--font-read)', fontSize: '0.9375rem',
                          border: t === 'Dark'
                            ? '1px solid rgba(var(--hb-accent-rgb),0.32)'
                            : '1px solid rgba(255,255,255,0.08)',
                          background: t === 'Dark'
                            ? 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.18), rgba(var(--hb-accent-rgb),0.05))'
                            : 'var(--glass-sheen)',
                          color: t === 'Dark' ? 'var(--hb-text)' : 'var(--hb-text-faint)',
                          cursor: t === 'Dark' ? 'default' : 'not-allowed',
                        }}
                      >
                        {t}{t === 'Light' ? ' — soon' : ''}
                      </button>
                    ))}
                  </div>
                </SettingsField>

                <SettingsRow
                  title="Telemetry column"
                  desc="The right-hand column: uplink, session budget, routing and toolsets."
                >
                  <Switch
                    on={settings.telemetryOpen}
                    onChange={v => update({ telemetryOpen: v })}
                  />
                </SettingsRow>

                <SettingsRow
                  title="Sidebar"
                  desc="Drag the sidebar edge to resize it."
                >
                  <Switch
                    on={settings.sidebarOpen}
                    onChange={v => update({ sidebarOpen: v })}
                  />
                </SettingsRow>
              </div>
            )}

            {/* Data tab — import Claude chat export */}
            {tab === 'data' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
                <div>
                  <SettingsSection title="Import Claude conversations" first />
                  <p style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)', margin: '8px 0 14px', lineHeight: 1.55 }}>
                    Upload the <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--hb-cyan)' }}>.zip</code> from
                    your Claude data export. Each conversation becomes a session; messages keep their
                    original dates. Runs in the background — sessions appear as they process.
                  </p>

                  {/* Hidden native input + custom trigger */}
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".zip"
                    style={{ display: 'none' }}
                    onChange={e => {
                      const f = e.target.files?.[0] ?? null
                      setImportFile(f)
                      setImportStatus('idle')
                      setImportMsg('')
                    }}
                  />

                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <PillBtn onClick={() => fileRef.current?.click()}>Choose .zip…</PillBtn>

                    <span style={{
                      fontSize: '0.845rem',
                      color: importFile ? 'var(--hb-text)' : 'var(--hb-text-faint)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 300,
                    }}>
                      {importFile ? importFile.name : 'No file selected'}
                    </span>

                    <div style={{ flex: 1 }} />

                    <button
                      onClick={handleImport}
                      disabled={!importFile || importStatus === 'uploading'}
                      className="glass-round"
                      style={{
                        height: 32, padding: '0 16px', flexShrink: 0,
                        border: '1px solid rgba(var(--hb-accent-rgb),0.32)',
                        background: 'rgba(var(--hb-accent-rgb),0.12)',
                        color: 'var(--hb-cyan-bright)',
                        fontFamily: 'var(--font-read)', fontSize: '0.845rem',
                        cursor: !importFile || importStatus === 'uploading' ? 'default' : 'pointer',
                        opacity: !importFile || importStatus === 'uploading' ? 0.45 : 1,
                      }}
                    >
                      {importStatus === 'uploading' ? 'Importing…' : 'Import'}
                    </button>
                  </div>

                  {/* Status line */}
                  {importMsg && (
                    <p style={{
                      marginTop: 14, fontSize: '0.845rem',
                      color: importStatus === 'error' ? '#e5897c'
                           : importStatus === 'done' ? '#8fdcb3'
                           : 'var(--hb-text-dim)',
                    }}>
                      {importStatus === 'done' ? '✓ ' : importStatus === 'error' ? '✕ ' : '› '}{importMsg}
                    </p>
                  )}
                </div>

                {/* Index past conversations */}
                <div>
                  <SettingsSection title="Rebuild memory from history" />
                  <p style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)', margin: '8px 0 14px', lineHeight: 1.55 }}>
                    Mines durable facts about you from your entire conversation history and
                    rebuilds the memory record from them. Safe to run more than once: anything
                    you wrote yourself is preserved and only re-derived facts are replaced.
                    Runs in the background; a few minutes.
                  </p>

                  {/* The record's state, shown without pressing anything — an empty
                      record is worth knowing about before it is needed. */}
                  {memory && (
                    <div className="hb-tile" style={{
                      marginBottom: 14, padding: '12px 16px',
                      border: '1px solid rgba(255,255,255,0.07)',
                      background: 'rgba(255,255,255,0.03)',
                      fontSize: '0.845rem', lineHeight: 1.55,
                      color: 'var(--hb-text-dim)',
                    }}>
                      <div style={{ color: 'var(--hb-text)', marginBottom: 4 }}>
                        {memory.observations} fact(s) recorded
                        {Object.keys(memory.by_origin).length > 0 && (
                          <span style={{ color: 'var(--hb-text-faint)' }}>
                            {'  ·  '}
                            {Object.entries(memory.by_origin)
                              .map(([k, v]) => `${k}: ${v}`)
                              .join('  ')}
                          </span>
                        )}
                      </div>
                      <div style={{
                        color: memory.job?.status === 'running' || memory.job?.status === 'pending'
                          ? 'var(--hb-cyan-bright)'
                          : memory.thin_compositions.length > 0 || memory.observations === 0
                            ? '#e5897c'
                            : memory.at_risk_facts > 0 ? 'var(--hb-amber-bright)' : '#8fdcb3',
                      }}>
                        {memory.verdict}
                      </div>

                      {/* A long rebuild has to show movement, not just a spinner —
                          the first real run sat at a flat count for hours because
                          nothing was written until the very end. */}
                      {memory.job?.progress && (
                        <div style={{ marginTop: 8 }}>
                          <div className="hb-round" style={{
                            height: 6,
                            background: 'rgba(255,255,255,0.07)', overflow: 'hidden',
                          }}>
                            <div style={{
                              height: '100%',
                              width: `${Math.round(
                                (memory.job.progress.done / Math.max(memory.job.progress.total, 1)) * 100
                              )}%`,
                              background: 'var(--hb-cyan-bright)',
                              transition: 'width 0.4s ease',
                            }} />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <button
                    onClick={handleIndex}
                    disabled={indexStatus === 'running'}
                    className="glass-round"
                    style={{
                      height: 32, padding: '0 16px',
                      border: '1px solid rgba(var(--hb-accent-rgb),0.32)',
                      background: 'rgba(var(--hb-accent-rgb),0.12)',
                      color: 'var(--hb-cyan-bright)',
                      fontFamily: 'var(--font-read)', fontSize: '0.845rem',
                      cursor: indexStatus === 'running' ? 'default' : 'pointer',
                      opacity: indexStatus === 'running' ? 0.45 : 1,
                    }}
                  >
                    {indexStatus === 'running' ? 'Rebuilding…' : 'Rebuild memory'}
                  </button>
                  {indexMsg && (
                    <p style={{
                      marginTop: 14, fontSize: '0.845rem',
                      color: indexStatus === 'error' ? '#e5897c'
                           : indexStatus === 'done' ? '#8fdcb3'
                           : 'var(--hb-text-dim)',
                    }}>
                      {indexStatus === 'done' ? '✓ ' : indexStatus === 'error' ? '✕ ' : '› '}{indexMsg}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Account tab */}
            {tab === 'account' && profile && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>

                {/* Who this client is signed in as */}
                <div className="hb-tile" style={{
                  display: 'flex', alignItems: 'center', gap: 14,
                  padding: '16px 18px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.07)',
                }}>
                  <div style={{
                    width: 48, height: 48, borderRadius: '50%', flexShrink: 0,
                    background: 'linear-gradient(160deg, var(--hb-cyan-bright), var(--hb-cyan-dim))',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontFamily: 'var(--font-ui)', fontSize: '1.25rem', fontWeight: 700,
                    color: '#0a1420',
                  }}>
                    {(localUserName[0] || profile.avatarInitial).toUpperCase()}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <p style={{ fontSize: '1rem', color: 'var(--hb-text)' }}>
                      {localUserName || profile.userName || profile.name}
                    </p>
                    <p style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)', marginTop: 2 }}>
                      {profile.tagline}
                    </p>
                  </div>
                </div>

                <SettingsField label="Your name" hint="Used in the greeting on the home screen.">
                  <input
                    className="hb-tile"
                    type="text"
                    value={localUserName}
                    onChange={e => setLocalUserName(e.target.value)}
                    onBlur={() => update({ userName: localUserName.trim() })}
                    onKeyDown={e => { if (e.key === 'Enter') { update({ userName: localUserName.trim() }); (e.currentTarget as HTMLInputElement).blur() } }}
                    placeholder="Enter your name…"
                    style={fieldStyle}
                    onFocus={e => (e.currentTarget.style.borderColor = 'rgba(var(--hb-accent-rgb),0.45)')}
                    onBlurCapture={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.09)')}
                  />
                </SettingsField>

                {/* There was a "Suggested prompts" list here reading
                    `profile.suggestedPrompts`. That field does not exist —
                    not on AppProfile, not in brands.ts, nowhere in the app —
                    so the read was `undefined.length` and this tab threw on
                    every open. Removed rather than guarded: guarding would
                    have left a section that can never render anything. */}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
