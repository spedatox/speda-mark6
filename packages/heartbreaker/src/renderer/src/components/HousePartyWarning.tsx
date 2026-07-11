import { useEffect } from 'react'

const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

/**
 * HousePartyWarning — the in-chat trigger for the House Party authorization
 * pop-up. SPEDA emits a ```hpp-warning block; instead of rendering the full
 * card (and asking the owner to type the passphrase into the composer, which
 * was lame and insecure), this renders a slim glowing banner and raises a
 * `speda:hpp-authorize` event so Layout opens the real modal — a proper window
 * with a MASKED passphrase field, validated server-side.
 *
 * It auto-opens once when it first appears (debounced so streaming re-mounts
 * don't reopen it), and the banner stays as a click-to-reopen affordance.
 */

// Debounce auto-open across the many re-mounts ReactMarkdown does while the
// block is still streaming in.
let _lastAutoOpen = 0

function openModal(objective: string) {
  window.dispatchEvent(new CustomEvent('speda:hpp-authorize', { detail: { objective } }))
}

export default function HousePartyWarning({ children }: { children?: string }) {
  const objMatch = /(?:^|\n)\s*objective\s*:\s*(.+)/i.exec(children ?? '')
  const objective = objMatch ? objMatch[1].trim().slice(0, 180) : ''

  useEffect(() => {
    const now = Date.now()
    if (now - _lastAutoOpen > 2500) {
      _lastAutoOpen = now
      openModal(objective)
    }
  }, [objective])

  const AMBER = 'var(--hb-amber-bright)'
  return (
    <button
      onClick={() => openModal(objective)}
      className="hb-holo"
      style={{
        display: 'flex', alignItems: 'center', gap: '0.7rem', width: '100%', maxWidth: 460,
        margin: '0.4rem 0', padding: '0.6rem 0.85rem', cursor: 'pointer', textAlign: 'left',
        border: `1px solid ${AMBER}55`,
        boxShadow: `inset 0 1px 0 0 rgba(255,255,255,0.18), 0 0 20px rgba(242,183,92,0.12)`,
        animation: 'widgetEntrance 0.35s ease both',
      }}
    >
      <span style={{ display: 'flex', color: AMBER, animation: 'hbBlink 1.8s ease-in-out infinite', flexShrink: 0 }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
        </svg>
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{
          display: 'block', fontFamily: UI, fontSize: '0.86rem', fontWeight: 800,
          letterSpacing: '0.12em', textTransform: 'uppercase', color: '#fff', lineHeight: 1.15,
        }}>
          House Party Protocol
        </span>
        <span style={{
          display: 'block', fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.2em',
          textTransform: 'uppercase', color: AMBER, marginTop: 2,
        }}>
          Authorization required — click to open
        </span>
      </span>
      <span style={{
        flexShrink: 0, fontFamily: UI, fontSize: '0.72rem', fontWeight: 800,
        letterSpacing: '0.16em', textTransform: 'uppercase', color: AMBER,
        display: 'flex', alignItems: 'center', gap: 4,
      }}>
        Engage
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      </span>
    </button>
  )
}
