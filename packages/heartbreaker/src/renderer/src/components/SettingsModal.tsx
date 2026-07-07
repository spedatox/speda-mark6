import { useState, useEffect, useRef } from 'react'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { useChatContext } from '../store/chat'
import { importChats, fetchSessions, indexHistory, getConnections, setConnection, googleLoginUrl, googleStatus, googleDisconnect, notionLoginUrl, notionStatus, notionDisconnect, getAutomations, toggleAutomation, deleteAutomation, getAutomationsStatus, telegramConnect, telegramStatus } from '../lib/api'
import type { ConnectionInfo, AutomationInfo, AutomationsStatus } from '../lib/api'
import type { AppConfig } from '../lib/types'
import ConfigTab from './ConfigTab'

interface Props {
  config: AppConfig
  onClose: () => void
}

type Tab = 'general' | 'config' | 'connections' | 'automations' | 'interface' | 'data' | 'account'

export default function SettingsModal({ config, onClose }: Props) {
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

  const handleIndex = async () => {
    if (indexStatus === 'running') return
    setIndexStatus('running')
    setIndexMsg('Indexing started — mining facts from past conversations (background)…')
    try {
      const res = await indexHistory(config)
      setIndexStatus('done')
      setIndexMsg(res.message || 'Indexing started in the background.')
    } catch (e) {
      setIndexStatus('error')
      setIndexMsg(e instanceof Error ? e.message : 'Indexing failed')
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

  const tabs: { id: Tab; label: string }[] = [
    { id: 'general', label: 'General' },
    { id: 'config', label: 'Configuration' },
    { id: 'connections', label: 'Connections' },
    { id: 'automations', label: 'Automations' },
    { id: 'interface', label: 'Interface' },
    { id: 'data', label: 'Data' },
    { id: 'account', label: 'Account' },
  ]

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
      <div className="hb-glass" style={{
        width: 'min(720px, 95vw)', height: 'min(600px, 88vh)',
        background: 'rgba(150, 190, 225, 0.06)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        border: '1px solid var(--hb-edge)',
        display: 'flex',
        overflow: 'hidden',
        animation: 'modalIn 0.15s ease',
        boxShadow: 'var(--hb-holo-shadow)',
        position: 'relative',
      }}>
        {/* Left nav */}
        <div style={{
          width: 190, flexShrink: 0,
          borderRight: '1px solid var(--hb-line)',
          padding: '1.1rem 0.6rem',
          display: 'flex', flexDirection: 'column',
          background: 'linear-gradient(180deg, rgba(10,24,32,0.5), transparent)',
        }}>
          <p style={{
            fontFamily: "'Share Tech Mono', monospace",
            fontSize: '0.6rem', color: 'var(--hb-cyan)',
            padding: '0 0.625rem 0.75rem', letterSpacing: '0.22em', textTransform: 'uppercase',
          }}>
            {'>>:'} CONFIG
          </p>
          {tabs.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              style={{
                width: '100%', padding: '0.45rem 0.75rem',
                border: 'none', textAlign: 'left',
                borderLeft: tab === id ? '2px solid var(--hb-cyan)' : '2px solid transparent',
                background: tab === id ? 'rgba(var(--hb-accent-rgb),0.12)' : 'transparent',
                color: tab === id ? '#dff2f8' : 'var(--hb-text-dim)',
                cursor: 'pointer',
                fontFamily: "'Rajdhani', sans-serif",
                fontSize: '0.78rem', fontWeight: 700,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                transition: 'background 0.1s, color 0.1s, border-color 0.1s',
                marginBottom: '0.125rem',
              }}
              onMouseEnter={e => { if (tab !== id) (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)' }}
              onMouseLeave={e => { if (tab !== id) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Right content */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header row — white "SEARCH CONFIGURATION" plate */}
          <div style={{
            height: 46, flexShrink: 0, display: 'flex', alignItems: 'stretch',
            gap: 0, borderBottom: '1px solid var(--hb-line)',
          }}>
            <h2 className="hb-head-light" style={{ flex: 1, fontSize: '0.82rem', minHeight: 0 }}>
              {tabs.find(t => t.id === tab)?.label}
              <span style={{ flex: 1 }} />
              <span style={{
                width: 7, height: 14, alignSelf: 'center',
                background: 'rgba(217,156,68,0.5)',
                border: '1px solid rgba(242,183,92,0.7)',
                boxShadow: 'inset 0 1px 0 rgba(255,230,190,0.35)',
              }} />
            </h2>
            <button
              onClick={onClose}
              style={{
                width: 46, display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: 'none', borderLeft: '1px solid var(--hb-edge)',
                background: 'rgba(190, 215, 235, 0.1)',
                boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.25)',
                color: 'var(--hb-icon-bright)',
                cursor: 'pointer', transition: 'color 0.1s, background 0.12s',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = '#eaf6fa' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-icon-bright)' }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          {/* Scrollable content */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem 1.25rem' }}>

            {/* General tab */}
            {tab === 'general' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '0.375rem' }}>
                    System Prompt
                  </label>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.625rem', lineHeight: 1.5 }}>
                    Defines the AI's behavior and personality for all conversations.
                  </p>
                  <textarea
                    className="hb-glass-xs"
                    value={localPrompt}
                    onChange={e => setLocalPrompt(e.target.value)}
                    placeholder="You are a helpful assistant…"
                    rows={5}
                    style={{
                      // Dense translucent well — the modal's backdrop-filter is a
                      // nested backdrop root, so the fill must occlude on its own.
                      width: '100%', background: 'rgba(10, 22, 30, 0.55)',
                      boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.35), inset 0 -1px 0 0 rgba(255,255,255,0.05)',
                      border: '1px solid var(--hb-edge)',
                      padding: '0.75rem',
                      color: 'var(--text-primary)', fontSize: '0.875rem',
                      lineHeight: 1.6, fontFamily: 'inherit', resize: 'vertical',
                      outline: 'none', transition: 'border-color 0.15s',
                      userSelect: 'text',
                    }}
                    onFocus={e => (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'var(--border-focus)'}
                    onBlur={e => (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'var(--border)'}
                  />
                </div>

                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.375rem' }}>
                    <label style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)' }}>
                      Temperature
                    </label>
                    <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                      {localTemp.toFixed(1)}
                    </span>
                  </div>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.75rem', lineHeight: 1.5 }}>
                    Lower = more precise and deterministic. Higher = more creative and varied.
                  </p>
                  <input
                    type="range"
                    min={0} max={1} step={0.1}
                    value={localTemp}
                    onChange={e => setLocalTemp(parseFloat(e.target.value))}
                    style={{ width: '100%' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '0.375rem' }}>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Precise (0.0)</span>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Creative (1.0)</span>
                  </div>
                </div>
              </div>
            )}

            {/* Configuration tab — every backend setting, API keys included */}
            {tab === 'config' && <ConfigTab config={config} />}

            {/* Connections tab — toggle MCP servers live */}
            {tab === 'connections' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.55, margin: 0 }}>
                  Toggle which services SPEDA can use. Disabling one hides its tools instantly
                  (no restart) — which shrinks the prompt and keeps you under the rate limit.
                </p>

                {/* Google one-click sign-in */}
                <div style={{
                  padding: '0.85rem', border: '1px solid var(--border)',
                  background: 'rgba(255,255,255,0.02)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.86rem', color: 'var(--text-primary)', fontWeight: 600 }}>
                        Google Workspace
                      </div>
                      <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                        {googleConnected
                          ? 'Connected — Gmail, Calendar, Drive & Contacts are live.'
                          : 'Connect your account for Gmail, Calendar, Drive.'}
                      </div>
                    </div>
                    {googleConnected ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                        <span style={{
                          display: 'flex', alignItems: 'center', gap: '0.35rem',
                          fontSize: '0.8rem', fontWeight: 600, color: 'var(--hb-green)',
                        }}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                          Connected
                        </span>
                        <button
                          onClick={disconnectGoogle}
                          className="hb-btn"
                          style={{ padding: '0.45rem 0.8rem', fontSize: '0.78rem', fontWeight: 500 }}
                        >
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={signInGoogle}
                        className="hb-btn"
                        style={{
                          gap: '0.5rem', padding: '0.5rem 0.9rem',
                          fontSize: '0.82rem', fontWeight: 600, color: 'var(--hb-text)',
                        }}
                      >
                        <svg width="15" height="15" viewBox="0 0 48 48">
                          <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.8c-.5 2.7-2 5-4.4 6.6v5.5h7.1c4.1-3.8 6.6-9.4 6.6-16.1z"/>
                          <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.4l-7.1-5.5c-2 1.3-4.5 2.1-7.4 2.1-5.7 0-10.5-3.8-12.2-9h-7.3v5.7C8.1 41.1 15.4 46 24 46z"/>
                          <path fill="#FBBC05" d="M11.8 28.2c-.4-1.3-.7-2.7-.7-4.2s.2-2.9.7-4.2v-5.7H4.5C3 17.2 2.1 20.5 2.1 24s.9 6.8 2.4 9.9l7.3-5.7z"/>
                          <path fill="#EA4335" d="M24 10.7c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3C34.9 4.1 29.9 2 24 2 15.4 2 8.1 6.9 4.5 14.1l7.3 5.7c1.7-5.2 6.5-9.1 12.2-9.1z"/>
                        </svg>
                        Sign in with Google
                      </button>
                    )}
                  </div>
                  {googleMsg && (
                    <p style={{ marginTop: '0.6rem', fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                      {googleMsg}
                    </p>
                  )}
                </div>

                {/* Notion one-click auth */}
                <div style={{
                  padding: '1.2rem',
                  borderRadius: '0.6rem',
                  border: '1px solid var(--border)',
                  background: 'rgba(190, 215, 235, 0.03)',
                  boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.08)',
                  marginBottom: '1rem',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <h4 style={{ margin: '0 0 0.3rem', fontSize: '0.85rem', color: 'var(--text-primary)' }}>Notion Workspace</h4>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-secondary)', maxWidth: '280px', lineHeight: 1.4 }}>
                        Connect your Notion workspace to enable powerful search, fetching, and page creation tools for your agents.
                      </p>
                    </div>
                    {notionConnected ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
                        <span style={{ fontSize: '0.75rem', color: 'var(--hb-green)', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--hb-green)' }}></span>
                          Connected
                        </span>
                        <button
                          onClick={disconnectNotion}
                          className="hb-btn"
                          style={{ padding: '0.45rem 0.8rem', fontSize: '0.78rem', fontWeight: 500 }}
                        >
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={signInNotion}
                        className="hb-btn"
                        style={{
                          gap: '0.5rem', padding: '0.5rem 0.9rem',
                          fontSize: '0.82rem', fontWeight: 600, color: 'var(--hb-text)',
                        }}
                      >
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                          <path d="M4.45877 4.54133L19.5397 5.25732V6.33128L18.8252 6.47466C18.2526 6.54632 17.8938 6.76132 17.8938 7.33465V18.1532C17.8938 18.5832 18.2526 18.7982 18.8252 18.8698L19.5397 19.0132V20.0872L12.3924 19.3712V18.2972L13.107 18.1539C13.6796 18.0822 14.0384 17.8672 14.0384 17.2939V8.83863L7.75338 18.6545H6.53612V8.04996C6.53612 7.47664 6.17737 7.26164 5.6047 7.18997L4.89018 7.04664V5.97268L12.0374 6.68867V7.76262L11.3229 7.90595C10.7503 7.97762 10.3915 8.19262 10.3915 8.76595V16.3626L15.932 7.90595C16.1466 7.61929 16.4328 7.40429 16.7196 7.33262L17.2917 7.18929L17.1486 6.11532L4.45877 4.54133Z" fill="currentColor"/>
                        </svg>
                        Sign in with Notion
                      </button>
                    )}
                  </div>
                  {notionMsg && (
                    <p style={{ marginTop: '0.6rem', fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                      {notionMsg}
                    </p>
                  )}
                </div>

                {/* Prefix budget bar */}
                {(() => {
                  const pct = Math.min(100, Math.round((connBudget.used / connBudget.limit) * 100))
                  const over = connBudget.used > connBudget.limit
                  const col = over ? 'var(--hb-red)' : pct > 80 ? 'var(--hb-amber)' : 'var(--hb-green)'
                  return (
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.74rem', marginBottom: '0.3rem' }}>
                        <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                          ACTIVE TOOL TOKENS
                        </span>
                        <span style={{ color: col, fontFamily: 'var(--font-mono)' }}>
                          ~{connBudget.used.toLocaleString()} / {connBudget.limit.toLocaleString()}
                        </span>
                      </div>
                      <div style={{ height: 6, background: 'rgba(var(--hb-accent-rgb),0.12)', overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: col, transition: 'width 0.2s' }} />
                      </div>
                      {over && (
                        <p style={{ fontSize: '0.74rem', color: 'var(--hb-red)', marginTop: '0.4rem' }}>
                          Over the 30k cold-write limit — disable a server (Notion is heaviest) or upgrade to Tier 2.
                        </p>
                      )}
                    </div>
                  )
                })()}

                {/* Server list */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {conns.length === 0 && (
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                      No MCP servers loaded.
                    </p>
                  )}
                  {conns.map(c => (
                    <div key={c.server} style={{
                      display: 'flex', alignItems: 'center', gap: '0.75rem',
                      padding: '0.6rem 0.75rem',
                      border: '1px solid var(--border)',
                      background: 'rgba(255,255,255,0.02)',
                    }}>
                      {/* status dot */}
                      <span style={{
                        width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                        background: c.connected ? 'var(--hb-green)' : 'var(--hb-red)',
                      }} title={c.connected ? 'Connected' : 'Not connected'} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '0.86rem', color: 'var(--text-primary)', fontWeight: 500 }}>{c.label}</div>
                        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginTop: '1px' }}>
                          {c.connected
                            ? `${c.tools} tools · ${c.always_on ? 'always on' : 'loaded on demand'}`
                            : (c.needs ? `needs ${c.needs}` : 'offline')}
                        </div>
                      </div>
                      {/* toggle */}
                      <button
                        onClick={() => toggleConn(c.server, !c.active)}
                        disabled={!c.connected}
                        title={c.active ? 'Active — click to disable' : 'Disabled — click to enable'}
                        style={{
                          width: 42, height: 24, flexShrink: 0, borderRadius: 999,
                          border: 'none', position: 'relative', cursor: c.connected ? 'pointer' : 'not-allowed',
                          background: c.active && c.connected ? 'rgba(var(--hb-accent-rgb),0.55)' : 'rgba(var(--hb-accent-rgb),0.2)',
                          boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.3), inset 0 -1px 0 rgba(255,255,255,0.08)',
                          opacity: c.connected ? 1 : 0.4, transition: 'background 0.15s',
                        }}
                      >
                        <span style={{
                          position: 'absolute', top: 3, left: c.active && c.connected ? 21 : 3,
                          width: 18, height: 18, borderRadius: '50%',
                          background: 'rgba(255,255,255,0.85)',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.6)',
                          transition: 'left 0.15s',
                        }} />
                      </button>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize: '0.74rem', color: 'var(--text-muted)', lineHeight: 1.5, margin: 0 }}>
                  A server greyed out as “needs …” requires its API key in the backend <code style={{ fontFamily: 'var(--font-mono)' }}>.env</code> and a restart.
                </p>
              </div>
            )}

            {/* Automations tab — SPEDA's proactive n8n watchers */}
            {tab === 'automations' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

                {/* Pipeline status — n8n engine + Telegram delivery */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {[
                    {
                      label: 'n8n ENGINE',
                      ok: !!autoStatus?.n8n_online,
                      detail: !autoStatus?.n8n_configured
                        ? 'needs N8N_API_KEY in the backend .env (n8n → Settings → n8n API)'
                        : autoStatus?.n8n_online ? autoStatus.n8n_url : 'unreachable — is the n8n container running?',
                    },
                    {
                      label: 'TELEGRAM DELIVERY',
                      ok: !!autoStatus?.telegram_connected,
                      detail: !autoStatus?.telegram_configured
                        ? 'needs TELEGRAM_BOT_TOKEN in the backend .env (create a bot with @BotFather)'
                        : autoStatus?.telegram_connected ? 'connected — SPEDA can reach you' : 'bot ready — connect your chat below',
                    },
                  ].map(row => (
                    <div key={row.label} style={{
                      display: 'flex', alignItems: 'center', gap: '0.75rem',
                      padding: '0.55rem 0.75rem',
                      border: '1px solid var(--border)',
                      background: 'rgba(255,255,255,0.02)',
                    }}>
                      <span style={{
                        width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                        background: row.ok ? 'var(--hb-green)' : 'var(--hb-amber)',
                      }} />
                      <span style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', letterSpacing: '0.08em', flexShrink: 0 }}>
                        {row.label}
                      </span>
                      <span style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.detail}
                      </span>
                    </div>
                  ))}

                  {autoStatus?.telegram_configured && !autoStatus?.telegram_connected && (
                    <div>
                      <button
                        onClick={handleTelegramConnect}
                        style={{
                          padding: '0.5rem 1rem',
                          border: '1px solid rgba(var(--hb-cyan-bright-rgb),0.5)',
                          background: 'rgba(var(--hb-accent-rgb),0.14)',
                          color: '#cdeefa', cursor: 'pointer',
                          fontFamily: "'Rajdhani',sans-serif", fontSize: '0.76rem', fontWeight: 700,
                          letterSpacing: '0.12em', textTransform: 'uppercase',
                        }}
                      >
                        Connect Telegram
                      </button>
                      {tgMsg && (
                        <p style={{ marginTop: '0.5rem', fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {tgMsg}
                        </p>
                      )}
                    </div>
                  )}
                </div>

                {/* Watcher list */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {autos.length === 0 && (
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                      Nothing is being watched yet.
                    </p>
                  )}
                  {autos.map(a => (
                    <div key={a.id} style={{
                      display: 'flex', alignItems: 'center', gap: '0.75rem',
                      padding: '0.6rem 0.75rem',
                      border: '1px solid var(--border)',
                      background: 'rgba(255,255,255,0.02)',
                      opacity: a.active ? 1 : 0.55,
                    }}>
                      {/* kind chip */}
                      <span style={{
                        flexShrink: 0, padding: '1px 6px',
                        fontSize: '0.58rem', fontFamily: 'var(--font-mono)', letterSpacing: '0.1em',
                        color: a.active ? 'var(--hb-cyan-bright)' : 'var(--text-muted)',
                        border: '1px solid rgba(var(--hb-accent-rgb),0.3)',
                      }}>
                        {{ web_watch: 'WEB', rss_watch: 'RSS', schedule: 'CRON', webhook: 'HOOK' }[a.kind] ?? a.kind.toUpperCase()}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '0.86rem', color: 'var(--text-primary)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {a.name}
                        </div>
                        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginTop: '1px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {a.summary}
                          {a.last_fired_at && ` · last fired ${new Date(a.last_fired_at).toLocaleString()}`}
                          {a.expires_at && ` · until ${new Date(a.expires_at).toLocaleDateString()}`}
                        </div>
                      </div>
                      {/* pause/resume toggle */}
                      <button
                        onClick={() => handleToggleAuto(a.id, !a.active)}
                        title={a.active ? 'Active — click to pause' : 'Paused — click to resume'}
                        style={{
                          width: 42, height: 24, flexShrink: 0, borderRadius: 999,
                          border: 'none', position: 'relative', cursor: 'pointer',
                          background: a.active ? 'rgba(var(--hb-accent-rgb),0.55)' : 'rgba(var(--hb-accent-rgb),0.2)',
                          boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.3), inset 0 -1px 0 rgba(255,255,255,0.08)',
                          transition: 'background 0.15s',
                        }}
                      >
                        <span style={{
                          position: 'absolute', top: 3, left: a.active ? 21 : 3,
                          width: 18, height: 18, borderRadius: '50%',
                          background: 'rgba(255,255,255,0.85)',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.6)',
                          transition: 'left 0.15s',
                        }} />
                      </button>
                      {/* delete */}
                      <button
                        onClick={() => handleDeleteAuto(a.id)}
                        title="Delete watcher (also removes the n8n workflow)"
                        style={{
                          width: 26, height: 26, flexShrink: 0,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          border: '1px solid transparent', background: 'transparent',
                          color: 'var(--text-muted)', cursor: 'pointer', transition: 'color 0.12s',
                        }}
                        onMouseEnter={e => ((e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-red)')}
                        onMouseLeave={e => ((e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)')}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <polyline points="3 6 5 6 21 6"/>
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                          <path d="M10 11v6M14 11v6"/>
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize: '0.74rem', color: 'var(--text-muted)', lineHeight: 1.5, margin: 0 }}>
                  SPEDA creates these itself — just ask: <em style={{ color: 'var(--hb-amber)', fontStyle: 'normal' }}>“track this page for a month and tell me when my results are up”</em>. When a watcher fires, SPEDA composes the message and pings you on Telegram.
                </p>
              </div>
            )}

            {/* Interface tab */}
            {tab === 'interface' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '0.5rem' }}>
                    Theme
                  </label>
                  <div style={{ display: 'flex', gap: '0.625rem' }}>
                    {['Dark', 'Light'].map(t => (
                      <button
                        key={t}
                        className={t === 'Dark' ? 'hb-btn hb-btn-tint' : 'hb-btn'}
                        style={{
                          padding: '0.5rem 1.25rem',
                          fontSize: '0.875rem',
                          ...(t === 'Dark' ? { color: 'var(--hb-cyan-bright)' } : {}),
                          cursor: t === 'Dark' ? 'default' : 'not-allowed',
                          opacity: t === 'Light' ? 0.5 : 1,
                        }}
                      >
                        {t}{t === 'Light' ? ' (soon)' : ''}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '0.375rem' }}>
                    Sidebar width
                  </label>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    Drag the sidebar edge to resize.
                  </p>
                </div>
              </div>
            )}

            {/* Data tab — import Claude chat export */}
            {tab === 'data' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.375rem' }}>
                    Import Claude conversations
                  </label>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.875rem', lineHeight: 1.55 }}>
                    Upload the <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>.zip</code> from
                    your Claude data export. Each conversation becomes a session; messages are imported with their
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

                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flexWrap: 'wrap' }}>
                    <button
                      className="hb-btn"
                      onClick={() => fileRef.current?.click()}
                      style={{ padding: '0.5rem 0.875rem', fontSize: '0.84rem' }}
                    >
                      Choose .zip…
                    </button>

                    <span style={{
                      fontSize: '0.82rem', color: importFile ? 'var(--text-primary)' : 'var(--text-muted)',
                      fontFamily: importFile ? 'var(--font-mono)' : 'inherit',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280,
                    }}>
                      {importFile ? importFile.name : 'No file selected'}
                    </span>

                    <div style={{ flex: 1 }} />

                    <button
                      className="hb-btn hb-btn-tint"
                      onClick={handleImport}
                      disabled={!importFile || importStatus === 'uploading'}
                      style={{
                        padding: '0.5rem 1.1rem', color: 'var(--hb-cyan-bright)',
                        fontSize: '0.84rem', fontWeight: 600, letterSpacing: '0.04em',
                      }}
                    >
                      {importStatus === 'uploading' ? 'Importing…' : 'Import'}
                    </button>
                  </div>

                  {/* Status line */}
                  {importMsg && (
                    <p style={{
                      marginTop: '0.875rem', fontSize: '0.8rem', fontFamily: 'var(--font-mono)',
                      color: importStatus === 'error' ? 'var(--hb-red)'
                           : importStatus === 'done' ? 'var(--hb-green)'
                           : 'var(--text-secondary)',
                    }}>
                      {importStatus === 'done' ? '✓ ' : importStatus === 'error' ? '✕ ' : '› '}{importMsg}
                    </p>
                  )}
                </div>

                <hr style={{ border: 'none', borderTop: '1px solid var(--border)' }} />

                {/* Index past conversations */}
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.375rem' }}>
                    Index past conversations
                  </label>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.875rem', lineHeight: 1.55 }}>
                    One-time: mine durable facts about you from your entire history (Haiku),
                    consolidate them, and write a profile to memory so SPEDA actually knows you.
                    Runs in the background; ~a couple of minutes, ~$2 once.
                  </p>
                  <button
                    className="hb-btn hb-btn-tint"
                    onClick={handleIndex}
                    disabled={indexStatus === 'running'}
                    style={{
                      padding: '0.5rem 1.1rem', color: 'var(--hb-cyan-bright)',
                      fontSize: '0.84rem', fontWeight: 600, letterSpacing: '0.04em',
                    }}
                  >
                    {indexStatus === 'running' ? 'Indexing…' : 'Index history'}
                  </button>
                  {indexMsg && (
                    <p style={{
                      marginTop: '0.875rem', fontSize: '0.8rem', fontFamily: 'var(--font-mono)',
                      color: indexStatus === 'error' ? 'var(--hb-red)'
                           : indexStatus === 'done' ? 'var(--hb-green)'
                           : 'var(--text-secondary)',
                    }}>
                      {indexStatus === 'done' ? '✓ ' : indexStatus === 'error' ? '✕ ' : '› '}{indexMsg}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Account tab */}
            {tab === 'account' && profile && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.75rem' }}>

                {/* Avatar row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                  <div style={{
                    width: 56, height: 56, borderRadius: '50%',
                    background: 'linear-gradient(135deg, #4285F4, #0D9488)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '1.4rem', fontWeight: 700, color: '#fff', flexShrink: 0,
                  }}>
                    {(localUserName[0] || profile.avatarInitial).toUpperCase()}
                  </div>
                  <div>
                    <p style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {localUserName || profile.userName || profile.name}
                    </p>
                    <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>{profile.tagline}</p>
                  </div>
                </div>

                <hr style={{ border: 'none', borderTop: '1px solid var(--border)' }} />

                {/* Editable name */}
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '0.375rem' }}>
                    Your name
                  </label>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.625rem', lineHeight: 1.5 }}>
                    Used in the greeting on the home screen.
                  </p>
                  <input
                    className="hb-glass-xs"
                    type="text"
                    value={localUserName}
                    onChange={e => setLocalUserName(e.target.value)}
                    onBlur={() => update({ userName: localUserName.trim() })}
                    onKeyDown={e => { if (e.key === 'Enter') { update({ userName: localUserName.trim() }); (e.currentTarget as HTMLInputElement).blur() } }}
                    placeholder="Enter your name…"
                    style={{
                      // Dense translucent well — the modal's backdrop-filter is a
                      // nested backdrop root, so the fill must occlude on its own.
                      width: '100%', background: 'rgba(10, 22, 30, 0.55)',
                      boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.35), inset 0 -1px 0 0 rgba(255,255,255,0.05)',
                      border: '1px solid var(--hb-edge)',
                      padding: '0.625rem 0.75rem',
                      color: 'var(--text-primary)', fontSize: '0.9375rem',
                      fontFamily: 'inherit', outline: 'none',
                      transition: 'border-color 0.15s', userSelect: 'text',
                    }}
                    onFocus={e => (e.currentTarget.style.borderColor = 'var(--border-focus)')}
                    onBlurCapture={e => (e.currentTarget.style.borderColor = 'var(--border)')}
                  />
                </div>

                {/* Suggested prompts (read-only) */}
                {profile.suggestedPrompts.length > 0 && (
                  <div>
                    <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                      Suggested prompts
                    </label>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                      {profile.suggestedPrompts.map((p, i) => (
                        <p key={i} style={{ fontSize: '0.84rem', color: 'var(--text-muted)', padding: '0.375rem 0.625rem', background: 'rgba(255,255,255,0.03)', borderRadius: '0.375rem', border: '1px solid var(--border)' }}>
                          {p}
                        </p>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
