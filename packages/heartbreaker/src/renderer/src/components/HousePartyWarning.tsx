const MONO = 'var(--font-mono)'
const UI = "'Rajdhani', sans-serif"

/**
 * HousePartyWarning — SALVAGE rendering for a ```hpp-warning fence.
 *
 * The live authorization flow no longer goes through the transcript at all: the
 * backend's house_party tool raises a `house_party_auth` SSE event and ChatMain
 * opens the passphrase window off that. This component only exists so an old
 * transcript — or a model that emits the fence out of habit — shows a banner the
 * owner can click instead of a raw code block. It does NOT auto-open: scrolling
 * back through history must never pop an authorization window.
 */

function openModal(objective: string) {
  window.dispatchEvent(new CustomEvent('speda:hpp-authorize', { detail: { objective } }))
}

export default function HousePartyWarning({ children }: { children?: string }) {
  const objMatch = /(?:^|\n)\s*objective\s*:\s*(.+)/i.exec(children ?? '')
  const objective = objMatch ? objMatch[1].trim().slice(0, 180) : ''

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
