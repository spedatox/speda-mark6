import { useEffect, useRef, useState } from 'react'
import VoiceOrb, { type OrbState } from './VoiceOrb'

/**
 * Voice mode's surface: the orb, and the reply as it is being spoken.
 *
 * It replaces the transcript rather than sitting beside it. The whole point of
 * the mode is that the owner is listening, not reading a scrollback — so what
 * shows is only the current exchange, and it clears when the next one starts.
 * The composer stays put underneath (ChatMain still owns it): the owner types,
 * SPEDA answers aloud.
 */

const LOCALES: { id: string; label: string }[] = [
  { id: 'tr-TR', label: 'TR' },
  { id: 'en-US', label: 'EN' },
]

interface Props {
  state: OrbState
  amplitude: () => number
  spectrum?: (out: Float32Array) => void
  /** The reply text so far — shown under the orb, a couple of lines at a time. */
  reply: string
  /** What the owner last said, kept small above the orb for context. */
  prompt: string
  locale: string
  onLocale: (locale: string) => void
  onClose: () => void
  /** Cut playback without leaving the mode. */
  onStopSpeaking: () => void
  /** False when the backend has no Azure key — the mode is unusable, say so
   *  rather than silently never speaking. */
  configured: boolean
  agentName: string
  agentId?: string
}

export default function VoiceMode({
  state, amplitude, spectrum, reply, prompt, locale, onLocale, onClose, onStopSpeaking,
  configured, agentName, agentId,
}: Props) {
  // Show the tail of the reply, not the head: while it is being spoken the
  // interesting part is what is being said now.
  const tailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    tailRef.current?.scrollTo({ top: tailRef.current.scrollHeight, behavior: 'smooth' })
  }, [reply])

  const [hint, setHint] = useState(true)
  useEffect(() => {
    if (!reply) return
    setHint(false)
  }, [reply])

  const label =
    !configured ? 'VOICE OUTPUT NOT CONFIGURED'
    : state === 'speaking' ? 'SPEAKING'
    : state === 'thinking' ? 'THINKING'
    : 'STANDING BY'

  return (
    <div
      style={{
        flex: 1, minHeight: 0, position: 'relative',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', gap: '1.1rem', padding: '1.5rem',
        animation: 'fadeIn 0.25s ease',
      }}
    >
      {/* Top bar — language + exit */}
      <div style={{
        position: 'absolute', top: 12, left: 0, right: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 1rem',
      }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {LOCALES.map(l => (
            <button
              key={l.id}
              className={locale === l.id ? 'hb-btn hb-btn-tint' : 'hb-btn'}
              onClick={() => onLocale(l.id)}
              title={`Speak replies in ${l.id}`}
              style={{
                height: 24, padding: '0 0.6rem',
                fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
                fontWeight: 700, letterSpacing: '0.16em',
                ...(locale === l.id ? { color: 'var(--hb-cyan-bright)' } : {}),
              }}
            >
              {l.label}
            </button>
          ))}
        </div>

        <button
          className="hb-btn"
          onClick={onClose}
          title="Leave voice mode (Esc)"
          style={{
            height: 24, padding: '0 0.6rem', gap: '0.35rem',
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
            fontWeight: 700, letterSpacing: '0.16em',
          }}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          EXIT
        </button>
      </div>

      {/* What the owner asked — small, above the orb */}
      {prompt && (
        <div style={{
          maxWidth: 560, textAlign: 'center',
          fontFamily: "var(--font-mono)", fontSize: '0.7rem',
          letterSpacing: '0.04em', color: 'var(--hb-text-faint)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {prompt}
        </div>
      )}

      <VoiceOrb state={state} amplitude={amplitude} spectrum={spectrum} agentId={agentId} size={400} />

      {/* Status line */}
      <div
        className={state === 'thinking' ? 'thinking-shimmer' : undefined}
        style={{
          fontFamily: "'Rajdhani', sans-serif", fontSize: '0.7rem',
          fontWeight: 700, letterSpacing: '0.22em',
          color: configured ? 'var(--hb-cyan-dim)' : '#f87171',
        }}
      >
        {label}
      </div>

      {/* The reply, as it is spoken */}
      <div
        ref={tailRef}
        style={{
          maxWidth: 620, maxHeight: '9rem', overflowY: 'auto',
          textAlign: 'center', fontFamily: 'var(--font-read)',
          fontSize: '1.02rem', lineHeight: 1.7, color: 'var(--hb-text)',
          userSelect: 'text', whiteSpace: 'pre-wrap',
        }}
      >
        {reply}
      </div>

      {state === 'speaking' && (
        <button
          className="hb-btn"
          onClick={onStopSpeaking}
          title="Stop speaking"
          style={{
            height: 26, padding: '0 0.75rem', gap: '0.4rem',
            fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
            fontWeight: 700, letterSpacing: '0.16em',
          }}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
            <rect x="5" y="5" width="14" height="14" rx="2" />
          </svg>
          STOP
        </button>
      )}

      {hint && !reply && (
        <div style={{
          position: 'absolute', bottom: 10,
          fontFamily: "var(--font-mono)", fontSize: '0.62rem',
          letterSpacing: '0.06em', color: 'var(--hb-icon-dim)', textAlign: 'center',
        }}>
          {configured
            ? `Type below — ${agentName} answers out loud.`
            : 'Set AZURE_SPEECH_KEY on the backend to enable spoken replies.'}
        </div>
      )}
    </div>
  )
}
