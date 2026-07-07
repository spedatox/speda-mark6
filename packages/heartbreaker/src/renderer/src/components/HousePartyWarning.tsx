import { ROSTER } from '../lib/agents'
import { Avatar } from './CommBubble'

const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"

/**
 * HousePartyWarning — the inline authorization card SPEDA renders in-chat when
 * the owner asks to engage the House Party Protocol.
 *
 * Triggered by a ```hpp-warning code block. The protocol is heavy, expensive
 * and still a prototype, so it never fires casually — this card states the
 * stakes (HEAVY · EXPENSIVE · PROTOTYPE), shows the roster it would summon, and
 * asks the owner to speak the authorization passphrase. It is deliberately NOT
 * interactive: the passphrase is validated server-side by the house_party tool,
 * so the owner replies with it in the composer and SPEDA relays it. The block
 * body is optional — a single line becomes the mission objective on the card.
 */
export default function HousePartyWarning({ children }: { children?: string }) {
  // Only an explicit `objective: …` line becomes the objective — the model
  // often dumps the whole warning text in the body, and the card supplies all
  // of its own wording, so free text in the block is ignored.
  const objMatch = /(?:^|\n)\s*objective\s*:\s*(.+)/i.exec(children ?? '')
  const objective = objMatch ? objMatch[1].trim().slice(0, 180) : ''

  return (
    <div
      className="hb-holo"
      style={{
        position: 'relative',
        margin: '0.4rem 0',
        padding: '0.9rem 1rem 1rem',
        maxWidth: 560,
        border: '1px solid rgba(242, 183, 92, 0.5)',
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.22), 0 0 26px rgba(242,183,92,0.14), 0 10px 34px rgba(0,0,0,0.4)',
        overflow: 'hidden',
        animation: 'widgetEntrance 0.35s ease both',
      }}
    >
      {/* Caution stripe down the left edge */}
      <span aria-hidden style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
        background: 'linear-gradient(180deg, var(--hb-amber-bright), rgba(242,183,92,0.25))',
        boxShadow: '0 0 10px rgba(242,183,92,0.5)',
      }} />

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.7rem' }}>
        <span style={{
          display: 'flex', color: 'var(--hb-amber-bright)',
          animation: 'hbBlink 1.8s ease-in-out infinite',
        }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          </svg>
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontFamily: UI, fontSize: '0.92rem', fontWeight: 800,
            letterSpacing: '0.16em', textTransform: 'uppercase', color: '#fff', lineHeight: 1.1,
          }}>
            House Party Protocol
          </div>
          <div style={{
            fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.22em',
            textTransform: 'uppercase', color: 'var(--hb-amber)', marginTop: 2,
          }}>
            Authorization Required
          </div>
        </div>
      </div>

      {/* Cost flags */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.7rem' }}>
        {['Heavy', 'Expensive', 'Prototype'].map(flag => (
          <span key={flag} style={{
            display: 'inline-flex', alignItems: 'center',
            padding: '0.18rem 0.6rem',
            border: '1px solid rgba(242,183,92,0.45)',
            background: 'rgba(242,183,92,0.08)',
            borderRadius: 999,
            fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.16em',
            textTransform: 'uppercase', color: 'var(--hb-amber-bright)',
          }}>
            {flag}
          </span>
        ))}
      </div>

      {/* Explanation */}
      <p style={{
        margin: 0, fontFamily: 'var(--font-read)', fontSize: '0.82rem',
        lineHeight: 1.55, color: 'var(--hb-text-dim)',
      }}>
        Engaging summons the <strong style={{ color: 'var(--hb-text)' }}>entire roster</strong> in
        parallel at full model grade with domain boundaries relaxed — costly, and still experimental.
        Reserve it for genuinely high-stakes, all-hands operations.
      </p>

      {objective && (
        <div style={{
          marginTop: '0.7rem', padding: '0.5rem 0.7rem',
          background: 'rgba(242,183,92,0.06)', border: '1px solid rgba(242,183,92,0.22)',
        }}>
          <div style={{
            fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.18em',
            textTransform: 'uppercase', color: 'var(--hb-amber)', marginBottom: 3,
          }}>
            Objective
          </div>
          <div style={{ fontFamily: 'var(--font-read)', fontSize: '0.82rem', color: 'var(--hb-text)' }}>
            {objective}
          </div>
        </div>
      )}

      {/* Roster to be summoned — standing by */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginTop: '0.85rem',
        paddingTop: '0.7rem', borderTop: '1px solid var(--hb-edge)',
      }}>
        <span style={{
          fontFamily: MONO, fontSize: '0.5rem', letterSpacing: '0.16em',
          textTransform: 'uppercase', color: 'var(--hb-icon)', flexShrink: 0,
        }}>
          Standing by
        </span>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {ROSTER.map((id, i) => (
            <span key={id} style={{ opacity: 0.55, animation: `hbHppBoot 0.4s ease ${0.15 + i * 0.06}s both` }}>
              <Avatar id={id} size={22} />
            </span>
          ))}
        </div>
      </div>

      {/* Passphrase prompt — informational; the owner replies in the composer */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '0.85rem',
        padding: '0.5rem 0.7rem',
        border: '1px dashed rgba(242,183,92,0.45)',
        background: 'rgba(8,16,24,0.4)',
      }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--hb-amber-bright)" strokeWidth="2" style={{ flexShrink: 0 }}>
          <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
        <span style={{
          flex: 1, fontFamily: UI, fontSize: '0.78rem', fontWeight: 600,
          letterSpacing: '0.04em', color: 'var(--hb-text)',
        }}>
          Reply with the authorization passphrase to engage.
        </span>
        <span style={{
          fontFamily: MONO, fontSize: '0.8rem', letterSpacing: '0.3em',
          color: 'var(--hb-amber)', flexShrink: 0,
        }}>
          ••••••
        </span>
      </div>
    </div>
  )
}
