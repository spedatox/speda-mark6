import { useEffect, useRef, useState } from 'react'
import { engageLockdown } from '../lib/api'
import type { AppConfig } from '../lib/types'

const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

const KEYFRAMES = `
@keyframes lkdBg { from { opacity: 0; } to { opacity: 1; } }
@keyframes lkdCard {
  0%   { opacity: 0; transform: translateY(26px) scale(0.94); filter: blur(10px); }
  100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes lkdScan { from { transform: translateX(-120%); } to { transform: translateX(320%); } }
@keyframes lkdShake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-6px)} 40%,80%{transform:translateX(6px)} }
@keyframes lkdPulse { 0%,100%{opacity:1} 50%{opacity:0.45} }
`

/** What containment actually does, stated plainly. The owner reaches for this
 *  during an incident, when the last thing they should have to do is remember
 *  which ports survive — so the modal tells them before they authorize. */
const SEALED = ['Host SSH (22)', 'App raw port (8000)']
const OPEN = ['HTTPS 443/80 — this app', 'Outbound — Speda keeps working']

/**
 * LockdownModal — the authorization pop-up for engaging the Lockdown Protocol.
 *
 * Mirrors HousePartyModal's flow (masked field, server-side validation, the
 * passphrase never enters the chat transcript) but reads as containment rather
 * than mobilization: red, not amber, and it names what stays reachable so
 * engaging never feels like stepping off a cliff.
 *
 * Opened directly by the Protocols tab, or by the `speda:lockdown-authorize`
 * event when an agent requests authorization mid-chat.
 */
export default function LockdownModal({
  config, reason, onClose, onEngaged,
}: {
  config: AppConfig
  reason?: string
  onClose: () => void
  onEngaged: () => void
}) {
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { const t = setTimeout(() => inputRef.current?.focus(), 350); return () => clearTimeout(t) }, [])

  const submit = async () => {
    if (busy || !pass.trim()) return
    setBusy(true); setError(null)
    const res = await engageLockdown(config, pass.trim())
    if (res.ok) {
      onEngaged()
      onClose()
    } else {
      setError(res.error || 'Authorization failed.')
      setPass('')
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) { e.preventDefault(); onClose() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [busy, onClose])

  const RED = 'var(--hb-red, #e8564a)'

  return (
    <div
      onClick={() => !busy && onClose()}
      style={{
        position: 'fixed', inset: 0, zIndex: 10050,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1.5rem',
        background: 'radial-gradient(ellipse 70% 60% at 50% 45%, rgba(232,86,74,0.12), rgba(9,3,4,0.82) 55%, rgba(4,2,2,0.94))',
        backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
        animation: 'lkdBg 0.35s ease both',
      }}
    >
      <style>{KEYFRAMES}</style>
      <div
        onClick={e => e.stopPropagation()}
        className="hb-holo"
        style={{
          position: 'relative', width: 'min(480px, 92vw)', overflow: 'hidden',
          padding: '1.4rem 1.5rem 1.5rem',
          border: `1px solid ${RED}66`,
          boxShadow: `inset 0 1px 0 0 rgba(255,255,255,0.16), 0 0 40px rgba(232,86,74,0.20), 0 20px 60px rgba(0,0,0,0.6)`,
          animation: `lkdCard 0.55s cubic-bezier(0.16,1,0.3,1) both`,
        }}
      >
        <span aria-hidden style={{
          position: 'absolute', top: 0, left: 0, height: 2, width: '30%',
          background: `linear-gradient(90deg, transparent, ${RED}, transparent)`,
          animation: 'lkdScan 2.4s linear infinite',
        }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.7rem', marginBottom: '1rem' }}>
          <span style={{ display: 'flex', color: RED, animation: 'lkdPulse 1.6s ease-in-out infinite' }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: UI, fontSize: '1.15rem', fontWeight: 800, letterSpacing: '0.14em',
              textTransform: 'uppercase', color: '#fff', lineHeight: 1.05,
            }}>
              Lockdown Protocol
            </div>
            <div style={{
              fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.24em',
              textTransform: 'uppercase', color: RED, marginTop: 3,
            }}>
              Authorization Required
            </div>
          </div>
        </div>

        <p style={{
          margin: 0, fontFamily: 'var(--font-read)', fontSize: '0.84rem', lineHeight: 1.55,
          color: 'var(--hb-text-dim)',
        }}>
          Engaging seals the server's <strong style={{ color: 'var(--hb-text)' }}>exposed inbound ports</strong> behind
          firewall rules, immediately. Reserve it for a suspected compromise of this host or a machine
          that can reach it.
        </p>

        {reason && (
          <div style={{
            marginTop: '0.8rem', padding: '0.5rem 0.7rem',
            background: 'rgba(232,86,74,0.07)', border: `1px solid ${RED}33`,
          }}>
            <div style={{ fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.18em', textTransform: 'uppercase', color: RED, marginBottom: 3 }}>
              Threat
            </div>
            <div style={{ fontFamily: 'var(--font-read)', fontSize: '0.82rem', color: 'var(--hb-text)' }}>{reason}</div>
          </div>
        )}

        {/* What closes, what stays — the reassurance that makes this safe to press */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.7rem',
          marginTop: '0.95rem', paddingTop: '0.85rem', borderTop: '1px solid var(--hb-edge)',
        }}>
          {[
            { title: 'Sealed', items: SEALED, color: RED },
            { title: 'Stays up', items: OPEN, color: 'var(--hb-cyan-bright)' },
          ].map(col => (
            <div key={col.title}>
              <div style={{
                fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.18em',
                textTransform: 'uppercase', color: col.color, marginBottom: 5,
              }}>
                {col.title}
              </div>
              {col.items.map(it => (
                <div key={it} style={{
                  fontFamily: MONO, fontSize: '0.6rem', lineHeight: 1.7,
                  color: 'var(--hb-text-dim)',
                }}>
                  {it}
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* Masked passphrase field */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '1.1rem',
          padding: '0.15rem 0.15rem 0.15rem 0.7rem',
          border: `1px solid ${error ? RED : `${RED}66`}`,
          background: 'rgba(14,6,7,0.55)',
          animation: error ? 'lkdShake 0.4s ease' : 'none',
          transition: 'border-color 0.2s',
        }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={RED} strokeWidth="2" style={{ flexShrink: 0 }}>
            <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          <input
            ref={inputRef}
            type="password"
            value={pass}
            onChange={e => { setPass(e.target.value); if (error) setError(null) }}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
            placeholder="Authorization passphrase"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            style={{
              flex: 1, minWidth: 0, height: 40,
              background: 'transparent', border: 'none', outline: 'none',
              color: 'var(--hb-text)', fontFamily: MONO, fontSize: '0.9rem', letterSpacing: '0.25em',
            }}
          />
        </div>
        {error && (
          <div style={{ marginTop: '0.45rem', fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.08em', color: RED }}>
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
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || !pass.trim()}
            style={{
              flex: 1, padding: '0.6rem 1.1rem',
              cursor: busy || !pass.trim() ? 'default' : 'pointer',
              opacity: !pass.trim() ? 0.5 : 1,
              background: `linear-gradient(180deg, rgba(232,86,74,0.30), rgba(232,86,74,0.12))`,
              border: `1px solid ${RED}`, color: '#fff',
              boxShadow: pass.trim() && !busy ? `0 0 18px rgba(232,86,74,0.38)` : 'none',
              fontFamily: UI, fontSize: '0.78rem', fontWeight: 800, letterSpacing: '0.2em', textTransform: 'uppercase',
              transition: 'opacity 0.2s, box-shadow 0.2s',
            }}
          >
            {busy ? 'Sealing…' : 'Engage Lockdown'}
          </button>
        </div>
      </div>
    </div>
  )
}
