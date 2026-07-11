import { useEffect, useRef, useState } from 'react'
import { ROSTER } from '../lib/agents'
import { engageHouseParty } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { Avatar } from './CommBubble'

const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

const KEYFRAMES = `
@keyframes hppBg { from { opacity: 0; } to { opacity: 1; } }
@keyframes hppCard {
  0%   { opacity: 0; transform: translateY(26px) scale(0.94); filter: blur(10px); }
  100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes hppBoot { from { opacity: 0; transform: translateY(6px); } to { opacity: 0.85; transform: translateY(0); } }
@keyframes hppScan { from { transform: translateX(-120%); } to { transform: translateX(320%); } }
@keyframes hppShake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-6px)} 40%,80%{transform:translateX(6px)} }
`

/**
 * HousePartyModal — the authorization pop-up for engaging the House Party
 * Protocol. Replaces the old inline card + "type the passphrase in the
 * composer" flow: a proper modal with a MASKED passphrase field that is
 * validated server-side (the passphrase never enters the chat transcript).
 *
 * Opened by Layout when SPEDA emits the ```hpp-warning marker (or the owner
 * clicks the inline AUTHORIZE trigger). On success the protocol engages and the
 * app transitions to the war room.
 */
export default function HousePartyModal({
  config, objective, onClose, onEngaged,
}: {
  config: AppConfig
  objective?: string
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
    const res = await engageHouseParty(config, pass.trim())
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

  const AMBER = 'var(--hb-amber-bright)'

  return (
    <div
      onClick={() => !busy && onClose()}
      style={{
        position: 'fixed', inset: 0, zIndex: 10050,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1.5rem',
        background: 'radial-gradient(ellipse 70% 60% at 50% 45%, rgba(242,183,92,0.10), rgba(3,6,9,0.8) 55%, rgba(2,4,6,0.92))',
        backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
        animation: 'hppBg 0.35s ease both',
      }}
    >
      <style>{KEYFRAMES}</style>
      <div
        onClick={e => e.stopPropagation()}
        className="hb-holo"
        style={{
          position: 'relative', width: 'min(460px, 92vw)', overflow: 'hidden',
          padding: '1.4rem 1.5rem 1.5rem',
          border: `1px solid ${AMBER}66`,
          boxShadow: `inset 0 1px 0 0 rgba(255,255,255,0.2), 0 0 40px rgba(242,183,92,0.18), 0 20px 60px rgba(0,0,0,0.55)`,
          animation: `hppCard 0.55s cubic-bezier(0.16,1,0.3,1) both`,
        }}
      >
        {/* scanning light bar along the top edge */}
        <span aria-hidden style={{
          position: 'absolute', top: 0, left: 0, height: 2, width: '30%',
          background: `linear-gradient(90deg, transparent, ${AMBER}, transparent)`,
          animation: 'hppScan 2.4s linear infinite',
        }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.7rem', marginBottom: '1rem' }}>
          <span style={{ display: 'flex', color: AMBER, animation: 'hbBlink 1.8s ease-in-out infinite' }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
            </svg>
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: UI, fontSize: '1.15rem', fontWeight: 800, letterSpacing: '0.14em',
              textTransform: 'uppercase', color: '#fff', lineHeight: 1.05,
            }}>
              House Party Protocol
            </div>
            <div style={{
              fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.24em',
              textTransform: 'uppercase', color: AMBER, marginTop: 3,
            }}>
              Authorization Required
            </div>
          </div>
        </div>

        {/* Cost flags */}
        <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.85rem' }}>
          {['Heavy', 'Expensive', 'Prototype'].map(f => (
            <span key={f} style={{
              padding: '0.2rem 0.6rem', borderRadius: 999,
              border: `1px solid ${AMBER}55`, background: 'rgba(242,183,92,0.08)',
              fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.16em',
              textTransform: 'uppercase', color: AMBER,
            }}>{f}</span>
          ))}
        </div>

        <p style={{
          margin: 0, fontFamily: 'var(--font-read)', fontSize: '0.84rem', lineHeight: 1.55,
          color: 'var(--hb-text-dim)',
        }}>
          Engaging summons the <strong style={{ color: 'var(--hb-text)' }}>entire roster</strong> in
          parallel at full model grade with domain boundaries relaxed — costly, and still
          experimental. Reserve it for genuinely high-stakes, all-hands operations.
        </p>

        {objective && (
          <div style={{
            marginTop: '0.8rem', padding: '0.5rem 0.7rem',
            background: 'rgba(242,183,92,0.06)', border: `1px solid ${AMBER}33`,
          }}>
            <div style={{ fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.18em', textTransform: 'uppercase', color: AMBER, marginBottom: 3 }}>
              Objective
            </div>
            <div style={{ fontFamily: 'var(--font-read)', fontSize: '0.82rem', color: 'var(--hb-text)' }}>{objective}</div>
          </div>
        )}

        {/* Roster standing by */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          marginTop: '0.95rem', paddingTop: '0.8rem', borderTop: '1px solid var(--hb-edge)',
        }}>
          <span style={{ fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--hb-icon)', flexShrink: 0 }}>
            Standing by
          </span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {ROSTER.map((id, i) => (
              <span key={id} style={{ opacity: 0.6, animation: `hppBoot 0.4s ease ${0.2 + i * 0.05}s both` }}>
                <Avatar id={id} size={22} />
              </span>
            ))}
          </div>
        </div>

        {/* Masked passphrase field */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '1.1rem',
          padding: '0.15rem 0.15rem 0.15rem 0.7rem',
          border: `1px solid ${error ? 'var(--hb-red)' : `${AMBER}66`}`,
          background: 'rgba(6,12,18,0.55)',
          animation: error ? 'hppShake 0.4s ease' : 'none',
          transition: 'border-color 0.2s',
        }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={AMBER} strokeWidth="2" style={{ flexShrink: 0 }}>
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
          <div style={{ marginTop: '0.45rem', fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.08em', color: 'var(--hb-red)' }}>
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
              background: `linear-gradient(180deg, rgba(242,183,92,0.28), rgba(242,183,92,0.12))`,
              border: `1px solid ${AMBER}`, color: '#fff',
              boxShadow: pass.trim() && !busy ? `0 0 18px rgba(242,183,92,0.35)` : 'none',
              fontFamily: UI, fontSize: '0.78rem', fontWeight: 800, letterSpacing: '0.2em', textTransform: 'uppercase',
              transition: 'opacity 0.2s, box-shadow 0.2s',
            }}
          >
            {busy ? 'Engaging…' : 'Engage'}
          </button>
        </div>
      </div>
    </div>
  )
}
