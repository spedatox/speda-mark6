import { useEffect, useRef, useState } from 'react'
import { persistConnection, pingHealth } from '../lib/connection'

const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

const KEYFRAMES = `
@keyframes csBg { from { opacity: 0; } to { opacity: 1; } }
@keyframes csCard {
  0%   { opacity: 0; transform: translateY(26px) scale(0.94); filter: blur(10px); }
  100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes csScan { from { transform: translateX(-120%); } to { transform: translateX(320%); } }
@keyframes csShake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-6px)} 40%,80%{transform:translateX(6px)} }
@keyframes csPulse { 0%,100%{opacity:1} 50%{opacity:0.45} }
@keyframes csSpin { to { transform: rotate(360deg); } }
`

interface Props {
  initialApiBase: string
  initialApiKey: string
  /** Boot prompt vs. reopened from Settings → Account — changes copy only. */
  firstRun?: boolean
  onClose: () => void
  onSaved: (apiBase: string, apiKey: string) => void
}

/**
 * ConnectionSetupModal — the "where does this client actually talk to" prompt.
 *
 * Raised by App.tsx on boot when nothing (env, a build-time bake, or a prior
 * save here) chose a real server, so the app would otherwise sit silently
 * pointed at a localhost the end user's machine will never have. Also reachable
 * any time from Settings → Account, to change the server later.
 *
 * Mirrors LockdownModal's shell (frosted `.hb-holo` card, masked-field
 * treatment) but reads as a calm first-run step rather than a threat: cyan, not
 * red, and it proves the address works — via the backend's unauthenticated
 * `/health` — before saving rather than after.
 */
export default function ConnectionSetupModal({
  initialApiBase, initialApiKey, firstRun, onClose, onSaved,
}: Props) {
  const [apiBase, setApiBase] = useState(initialApiBase === 'http://localhost:8000' ? '' : initialApiBase)
  const [apiKey, setApiKey] = useState(initialApiKey === 'dev-key' ? '' : initialApiKey)
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const baseRef = useRef<HTMLInputElement>(null)

  useEffect(() => { const t = setTimeout(() => baseRef.current?.focus(), 350); return () => clearTimeout(t) }, [])

  const submit = async (skipCheck = false) => {
    const base = apiBase.trim().replace(/\/+$/, '')
    const key = apiKey.trim()
    if (busy || !base || !key) return
    setBusy(true); setError(null); setUnreachable(false)

    if (!skipCheck) {
      const reachable = await pingHealth(base)
      if (!reachable) {
        setBusy(false)
        setUnreachable(true)
        setError("Couldn't reach that address — check the URL, or save anyway if the server is just waking up.")
        return
      }
    }

    await persistConnection(base, key)
    onSaved(base, key)
    onClose()
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) { e.preventDefault(); onClose() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [busy, onClose])

  const CYAN = 'var(--hb-cyan-bright)'
  const canSubmit = Boolean(apiBase.trim() && apiKey.trim())

  return (
    <div
      onClick={() => !busy && onClose()}
      style={{
        position: 'fixed', inset: 0, zIndex: 10050,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1.5rem',
        background: 'radial-gradient(ellipse 70% 60% at 50% 45%, rgba(127,164,196,0.14), rgba(3,7,10,0.82) 55%, rgba(2,4,6,0.94))',
        backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
        animation: 'csBg 0.35s ease both',
      }}
    >
      <style>{KEYFRAMES}</style>
      <div
        onClick={e => e.stopPropagation()}
        className="hb-holo"
        style={{
          position: 'relative', width: 'min(480px, 92vw)', overflow: 'hidden',
          padding: '1.4rem 1.5rem 1.5rem',
          border: `1px solid ${CYAN}55`,
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.16), 0 0 40px rgba(127,164,196,0.20), 0 20px 60px rgba(0,0,0,0.6)',
          animation: 'csCard 0.55s cubic-bezier(0.16,1,0.3,1) both',
        }}
      >
        <span aria-hidden style={{
          position: 'absolute', top: 0, left: 0, height: 2, width: '30%',
          background: `linear-gradient(90deg, transparent, ${CYAN}, transparent)`,
          animation: 'csScan 2.4s linear infinite',
        }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.7rem', marginBottom: '1rem' }}>
          <span style={{ display: 'flex', color: CYAN, animation: 'csPulse 2.2s ease-in-out infinite' }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1 1" />
              <path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1-1" />
            </svg>
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: UI, fontSize: '1.15rem', fontWeight: 800, letterSpacing: '0.14em',
              textTransform: 'uppercase', color: '#fff', lineHeight: 1.05,
            }}>
              Connect to Igor
            </div>
            <div style={{
              fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.24em',
              textTransform: 'uppercase', color: CYAN, marginTop: 3,
            }}>
              {firstRun ? 'First-time setup' : 'Server connection'}
            </div>
          </div>
        </div>

        <p style={{
          margin: 0, fontFamily: 'var(--font-read)', fontSize: '0.84rem', lineHeight: 1.55,
          color: 'var(--hb-text-dim)',
        }}>
          This client needs to know where your Igor backend lives and its API key
          before it can reach SPEDA or anyone on the roster.
        </p>

        {/* Server URL */}
        <div style={{ marginTop: '1.1rem' }}>
          <label style={{
            display: 'block', fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.18em',
            textTransform: 'uppercase', color: 'var(--hb-text-faint)', marginBottom: 6,
          }}>
            Server URL
          </label>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.6rem',
            padding: '0.15rem 0.7rem',
            border: `1px solid ${unreachable ? 'var(--hb-red)' : `${CYAN}44`}`,
            background: 'rgba(6,14,20,0.55)',
            animation: unreachable ? 'csShake 0.4s ease' : 'none',
            transition: 'border-color 0.2s',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={CYAN} strokeWidth="2" style={{ flexShrink: 0 }}>
              <circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" />
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
            </svg>
            <input
              ref={baseRef}
              type="text"
              value={apiBase}
              onChange={e => { setApiBase(e.target.value); if (unreachable) { setUnreachable(false); setError(null) } }}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
              placeholder="https://speda.yourdomain.com"
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
              style={{
                flex: 1, minWidth: 0, height: 40,
                background: 'transparent', border: 'none', outline: 'none',
                color: 'var(--hb-text)', fontFamily: MONO, fontSize: '0.86rem',
              }}
            />
          </div>
        </div>

        {/* API key */}
        <div style={{ marginTop: '0.85rem' }}>
          <label style={{
            display: 'block', fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.18em',
            textTransform: 'uppercase', color: 'var(--hb-text-faint)', marginBottom: 6,
          }}>
            API Key
          </label>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            padding: '0.15rem 0.15rem 0.15rem 0.7rem',
            border: `1px solid ${CYAN}44`,
            background: 'rgba(6,14,20,0.55)',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={CYAN} strokeWidth="2" style={{ flexShrink: 0 }}>
              <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
            </svg>
            <input
              type={reveal ? 'text' : 'password'}
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
              placeholder="SPEDA_API_KEY"
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
              style={{
                flex: 1, minWidth: 0, height: 40,
                background: 'transparent', border: 'none', outline: 'none',
                color: 'var(--hb-text)', fontFamily: MONO, fontSize: '0.86rem',
                letterSpacing: reveal ? 'normal' : '0.2em',
              }}
            />
            <button onClick={() => setReveal(r => !r)} type="button" title={reveal ? 'Hide' : 'Show'}
              style={{
                flexShrink: 0, height: 32, padding: '0 10px', cursor: 'pointer',
                background: 'transparent', border: 'none', color: 'var(--hb-text-faint)',
                fontFamily: 'var(--font-read)', fontSize: '0.72rem',
              }}>
              {reveal ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>

        {error && (
          <div style={{
            marginTop: '0.6rem', fontFamily: MONO, fontSize: '0.62rem', lineHeight: 1.6,
            letterSpacing: '0.02em', color: 'var(--hb-red)',
          }}>
            {error}
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', gap: '0.6rem', marginTop: '1.1rem' }}>
          <button
            onClick={onClose}
            disabled={busy}
            style={{
              flex: '0 0 auto', padding: '0.6rem 1.1rem', cursor: busy ? 'default' : 'pointer',
              background: 'transparent', border: '1px solid var(--hb-edge)', color: 'var(--hb-text-dim)',
              fontFamily: UI, fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase',
            }}
          >
            {firstRun ? 'Use local default' : 'Cancel'}
          </button>
          {unreachable ? (
            <button
              onClick={() => submit(true)}
              disabled={busy}
              style={{
                flex: 1, padding: '0.6rem 1.1rem', cursor: busy ? 'default' : 'pointer',
                background: 'linear-gradient(180deg, rgba(217,156,68,0.30), rgba(217,156,68,0.12))',
                border: '1px solid var(--hb-amber)', color: '#fff',
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 800, letterSpacing: '0.2em', textTransform: 'uppercase',
              }}
            >
              Save anyway
            </button>
          ) : (
            <button
              onClick={() => submit()}
              disabled={busy || !canSubmit}
              style={{
                flex: 1, padding: '0.6rem 1.1rem',
                cursor: busy || !canSubmit ? 'default' : 'pointer',
                opacity: !canSubmit ? 0.5 : 1,
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                background: 'linear-gradient(180deg, rgba(127,164,196,0.30), rgba(127,164,196,0.12))',
                border: `1px solid ${CYAN}`, color: '#fff',
                boxShadow: canSubmit && !busy ? '0 0 18px rgba(127,164,196,0.38)' : 'none',
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 800, letterSpacing: '0.2em', textTransform: 'uppercase',
                transition: 'opacity 0.2s, box-shadow 0.2s',
              }}
            >
              {busy && (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"
                  style={{ animation: 'csSpin 0.7s linear infinite' }}>
                  <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                </svg>
              )}
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
