import { useEffect, useRef, useState } from 'react'
import VoiceOrb, { type OrbState } from './VoiceOrb'
import type { MicState } from '../lib/mic'

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
  /** The owner's mic level — the orb reacts to both voices. */
  inputLevel?: () => number
  /** The reply text so far — shown under the orb, a couple of lines at a time. */
  reply: string
  /** What the owner last said, kept small above the orb for context. */
  prompt: string
  locale: string
  onLocale: (locale: string) => void
  onClose: () => void
  /** Cut playback without leaving the mode. */
  onStopSpeaking: () => void
  /** Mic state, owned by the composer's mic button. Reported here so the status
   *  line distinguishes an open mic from one that is actually hearing speech —
   *  identical-looking states are why people talk to a muted machine. */
  micState: MicState
  /** False when the backend has no Azure key — the mode is unusable, say so
   *  rather than silently never speaking. */
  configured: boolean
  agentName: string
  agentId?: string
}

export default function VoiceMode({
  state, amplitude, spectrum, inputLevel, reply, prompt, locale, onLocale, onClose,
  onStopSpeaking, micState, configured, agentName, agentId,
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

  /* ── Size ────────────────────────────────────────────────────────────────
   * The orb owns the screen when there is nothing to read, and yields only as
   * much as the reply actually needs. Driven off viewport height rather than a
   * fixed pixel count so it fills a large display instead of floating in it.
   * The transition lives on the canvas box (VoiceOrb), which re-reads its own
   * size every frame — so it scales smoothly rather than jumping. */
  const boxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    // Measured, not derived from innerHeight: this pane sits under the header
    // and above the composer, so the viewport is a good deal taller than the
    // room actually available, and sizing off it overflows.
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setBox({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  const speaking = reply.length > 0
  // Bounded by width as well as height — on a wide, short window the limit is
  // vertical, on a narrow one it is horizontal, and only the smaller fits.
  const room = Math.min(box.h || 520, box.w || 520)
  const orbSize = Math.round(
    speaking
      ? Math.max(240, Math.min(440, room * 0.62))
      : Math.max(300, Math.min(760, room * 0.98)),
  )

  // The mic outranks the agent's own state while it is hearing speech: during
  // barge-in both are true at once, and what the owner needs confirmed at that
  // instant is that they are being heard, not that the agent is still talking.
  const label =
    !configured ? 'VOICE OUTPUT NOT CONFIGURED'
    : micState === 'hearing' ? 'LISTENING'
    : micState === 'recognizing' ? 'TRANSCRIBING'
    : state === 'speaking' ? 'SPEAKING'
    : state === 'thinking' ? 'THINKING'
    : micState === 'listening' ? 'MIC OPEN'
    : 'STANDING BY'

  return (
    <div
      ref={boxRef}
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

      {/* The mic outranks the agent's own state, for the same reason the label
          does: during barge-in both are live at once, and the orb showing that
          it hears you is the more urgent of the two. */}
      <VoiceOrb
        state={micState === 'hearing' ? 'listening' : state}
        amplitude={amplitude}
        spectrum={spectrum}
        inputLevel={inputLevel}
        agentId={agentId}
        size={orbSize}
      />

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
            ? `Type below, or hit the mic and just talk — ${agentName} answers out loud.`
            : 'Set AZURE_SPEECH_KEY on the backend to enable voice.'}
        </div>
      )}
    </div>
  )
}
