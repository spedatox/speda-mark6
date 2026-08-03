import { useEffect, useMemo, useRef, useState } from 'react'
import VoiceOrb, { type OrbState } from './VoiceOrb'
import VoiceCanvas from './VoiceCanvas'
import { splitPanels, hasArtifacts } from '../lib/voicePanels'
import { TextSegment } from './Message'
import type { MicState } from '../lib/mic'

/**
 * Voice mode's surface: the orb, and the reply as it is being spoken.
 *
 * It replaces the transcript rather than sitting beside it. The whole point of
 * the mode is that the owner is listening, not reading a scrollback — so what
 * shows is only the current exchange, and it follows whichever session is
 * selected. The composer stays put underneath (ChatMain still owns it): the
 * owner types, SPEDA answers aloud.
 *
 * ── Two layouts, one rule ──────────────────────────────────────────────────
 * SPOKEN: the orb owns the screen and the words run along the bottom edge. The
 * orb NEVER shrinks to make room for prose — it lifts clear of it — because a
 * talking orb that shrinks reads as the agent backing away mid-sentence.
 *
 * THE CANVAS: the moment the answer carries something to SHOW — a chart, a map,
 * a worked equation, a widget — the mode becomes a workspace. The orb docks
 * into the corner and the answer is taken apart into windows (VoiceCanvas).
 * That is a different gesture from shrinking: the orb steps aside for something
 * it is presenting.
 */

const LOCALES: { id: string; label: string }[] = [
  { id: 'tr-TR', label: 'TR' },
  { id: 'en-US', label: 'EN' },
]

/** The band the canvas gets: below the top bar + the owner's prompt line, above
 *  the status line. Windows are packed inside it, so it has to be real pixels
 *  rather than padding — the packer cannot see padding. */
const CANVAS_TOP = 68
const CANVAS_BOTTOM = 34

/**
 * How much of `text` is safe to render right now.
 *
 * While a turn is streaming, an odd number of ``` fences means the last one is
 * still open — its body is half-written. Rendering that would mount a widget
 * iframe (or a chart parser) against a truncated document on every chunk. So
 * the reveal holds at the start of the open fence; everything before it, and
 * every CLOSED block, still streams. This mirrors `safeTarget` in Message.tsx.
 */
function renderable(text: string, streaming: boolean): string {
  if (!streaming) return text
  let idx = -1
  let count = 0
  for (let i = text.indexOf('```'); i !== -1; i = text.indexOf('```', i + 3)) {
    count++
    idx = i
  }
  return count % 2 === 1 ? text.slice(0, idx) : text
}

interface Props {
  state: OrbState
  amplitude: () => number
  spectrum?: (out: Float32Array) => void
  /** The owner's mic level — the orb reacts to both voices. */
  inputLevel?: () => number
  /** The reply so far — the LIVE assistant turn, or the last answer in whatever
   *  session is selected. Sourced from the chat store rather than kept locally,
   *  so switching agent or session changes what the orb screen is showing. */
  reply: string
  /** True while that reply is still being written (gates partial-fence render). */
  streaming: boolean
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
}

export default function VoiceMode({
  state, amplitude, spectrum, inputLevel, reply, streaming, prompt, locale, onLocale,
  onClose, onStopSpeaking, micState, configured, agentName,
}: Props) {
  const visible = useMemo(() => renderable(reply, streaming), [reply, streaming])
  const hasText = visible.trim().length > 0
  const panels = useMemo(() => splitPanels(visible), [visible])
  const hasCanvas = useMemo(() => hasArtifacts(panels), [panels])
  // Bumped by REFLOW — hands every window the owner dragged back to the layout.
  const [reflow, setReflow] = useState(0)

  // Show the tail of the reply, not the head: while it is being spoken the
  // interesting part is what is being said now.
  const tailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    tailRef.current?.scrollTo({ top: tailRef.current.scrollHeight, behavior: 'smooth' })
  }, [visible])

  /* ── Size ────────────────────────────────────────────────────────────────
   * The orb owns the screen. Driven off the pane's measured height rather than
   * a fixed pixel count so it fills a large display instead of floating in it,
   * and it keeps that size while speaking — the reply is an overlay along the
   * bottom edge and the orb lifts clear of it instead of scaling down.
   * The transition lives on the canvas box (VoiceOrb), which re-reads its own
   * size every frame — so a dock/undock scales smoothly rather than jumping. */
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

  // Bounded by width as well as height — on a wide, short window the limit is
  // vertical, on a narrow one it is horizontal, and only the smaller fits.
  const room = Math.min(box.h || 520, box.w || 520)
  const orbSize = Math.round(
    hasCanvas
      ? Math.max(300, Math.min(560, room * 0.62))
      : Math.max(280, Math.min(720, room * 0.92)),
  )

  /* ── The orb's box ────────────────────────────────────────────────────────
   * Both states are expressed the SAME way — left/top/width/height in pixels —
   * for one reason: that is what lets the browser interpolate between them. The
   * centred state used to be `inset:0` with flex centring and the docked state
   * `right/bottom`, and no transition can tween between two different
   * positioning schemes, so the orb teleported into the corner.
   *
   * Docked, it sits IN the corner rather than near it: a quarter of it hangs
   * off both edges so the glow bleeds out of frame instead of the whole
   * assembly floating inside a margin, which reads as a widget parked in the
   * corner rather than an object pushed aside. */
  // The only concession the centred orb makes to the text: rise by a fraction of
  // the pane so the bottom overlay is not sitting on its face. Small enough that
  // the ~8% of empty margin the assembly carries absorbs it.
  const lift = hasCanvas || !hasText ? 0 : -Math.round(Math.min(96, room * 0.07))
  /* Arm the transition only after the real geometry has been PAINTED once.
   *
   * The first render has no box (the ResizeObserver hasn't fired), so the orb's
   * first geometry is nonsense — a 478px orb at −239,−239. Arming on the render
   * that measures is not enough: React commits the new left/top and the new
   * transition property together, the browser never painted the un-transitioned
   * value, and it animates from the nonsense one. So the mode opens with the orb
   * swooping in from off-screen, which is not the dock — it is a glitch that
   * looks like one. A frame's delay is what makes the difference. */
  const [animate, setAnimate] = useState(false)
  useEffect(() => {
    if (box.w === 0 || animate) return
    const id = requestAnimationFrame(() => setAnimate(true))
    return () => cancelAnimationFrame(id)
  }, [box.w, animate])

  const bleed = Math.round(orbSize * 0.26)
  const orbLeft = hasCanvas
    ? Math.round(box.w - orbSize + bleed)
    : Math.round((box.w - orbSize) / 2)
  const orbTop = hasCanvas
    ? Math.round(box.h - orbSize + bleed)
    : Math.round((box.h - orbSize) / 2) + lift

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

  const chip = {
    height: 24, padding: '0 0.6rem',
    fontFamily: "'Rajdhani', sans-serif", fontSize: '0.64rem',
    fontWeight: 700, letterSpacing: '0.16em',
  } as const

  return (
    <div
      ref={boxRef}
      style={{
        flex: 1, minHeight: 0, position: 'relative',
        // NOT clipped: the docked orb deliberately hangs off the corner, and a
        // pane that clips would cut the bleed back to a flush edge. The canvas
        // does its own clipping, so windows still cannot escape.
        overflow: 'visible',
        animation: 'fadeIn 0.25s ease',
      }}
    >
      {/* Top bar — language + exit */}
      <div style={{
        position: 'absolute', top: 12, left: 0, right: 0, zIndex: 4,
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
              style={{ ...chip, ...(locale === l.id ? { color: 'var(--hb-cyan-bright)' } : {}) }}
            >
              {l.label}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 4 }}>
        {/* REFLOW — hands the board back to SPEDA. Only meaningful once there
            is a board, and only worth offering once the owner has moved
            something on it. */}
        {hasCanvas && (
          <button
            className="hb-btn"
            onClick={() => setReflow(n => n + 1)}
            title="Re-pack the windows"
            style={{ ...chip }}
          >
            REFLOW
          </button>
        )}

        <button
          className="hb-btn"
          onClick={onClose}
          title="Leave voice mode (Esc)"
          style={{ ...chip, gap: '0.35rem' }}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          EXIT
        </button>
        </div>
      </div>

      {/* What the owner asked — small, above everything */}
      {prompt && (
        <div style={{
          position: 'absolute', top: 46, left: 0, right: 0, zIndex: 3,
          padding: '0 4rem', textAlign: 'center',
          fontFamily: 'var(--font-mono)', fontSize: '0.7rem',
          letterSpacing: '0.04em', color: 'var(--hb-text-faint)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}>
          {prompt}
        </div>
      )}

      {/* THE CANVAS. Sits between the top bar and the status line; each window is
          placed clear of the orb's corner (see VoiceCanvas's packer). Sized off
          the measured box, so the layout is computed in the pixels it is drawn
          in rather than in percentages that lie during a resize. */}
      {hasCanvas && box.w > 0 && (
        <div style={{
          position: 'absolute', left: 0, right: 0,
          top: CANVAS_TOP, bottom: CANVAS_BOTTOM, zIndex: 2,
        }}>
          <VoiceCanvas
            panels={panels}
            width={box.w}
            height={Math.max(200, box.h - CANVAS_TOP - CANVAS_BOTTOM)}
            // The keep-out quadrant is the orb's own box, taken from the same
            // numbers that place it — derived twice, they drift.
            reserveX={orbLeft}
            reserveY={orbTop - CANVAS_TOP}
            reflow={reflow}
          />
        </div>
      )}

      {/* The orb. Centred and full-size by default; flies to the bottom-right
          corner, shrinking on the way, when the canvas takes the pane — one
          animation over one set of properties, so it MOVES rather than cuts.
          pointerEvents:none so a drag that passes over the dock still belongs to
          the window underneath it. */}
      <div
        style={{
          position: 'absolute', zIndex: 3, pointerEvents: 'none',
          left: orbLeft, top: orbTop, width: orbSize, height: orbSize,
          transition: !animate ? 'none' : `
            left 0.7s cubic-bezier(0.65, 0, 0.35, 1),
            top 0.7s cubic-bezier(0.65, 0, 0.35, 1),
            width 0.7s cubic-bezier(0.65, 0, 0.35, 1),
            height 0.7s cubic-bezier(0.65, 0, 0.35, 1)`,
        }}
      >
        {/* The mic outranks the agent's own state, for the same reason the label
            does: during barge-in both are live at once, and the orb showing that
            it hears you is the more urgent of the two. */}
        <VoiceOrb
          state={micState === 'hearing' ? 'listening' : state}
          amplitude={amplitude}
          spectrum={spectrum}
          inputLevel={inputLevel}
        />
      </div>

      {/* Bottom stack — the spoken reply, the status line, the stop control.
          Anchored to the bottom edge and grows upward, so nothing below it ever
          moves as text arrives. */}
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 4,
        display: 'flex', flexDirection: 'column',
        // With the orb in the corner the centre of the bottom edge is under it,
        // so the status line moves to the free side rather than under the glow.
        alignItems: hasCanvas ? 'flex-start' : 'center',
        gap: '0.75rem', pointerEvents: 'none',
        padding: hasCanvas
          ? `0.4rem ${orbSize}px 0.55rem 1.2rem`
          : '3rem 1.5rem 1.1rem',
        // A wash under the text, so prose stays legible over the orb's glow.
        background: hasText && !hasCanvas
          ? 'linear-gradient(to top, rgba(4,8,10,0.92) 42%, rgba(4,8,10,0.72) 68%, rgba(4,8,10,0))'
          : 'none',
      }}>
        {/* The reply, as it is spoken. Rendered through the transcript's own
            markdown pipeline — headings, lists, tables and code all read the
            way they do in chat. */}
        {!hasCanvas && hasText && (
          <div
            ref={tailRef}
            className="prose"
            style={{
              maxWidth: 640, maxHeight: '11rem', overflowY: 'auto',
              pointerEvents: 'auto', fontSize: '1.02rem',
              overflowWrap: 'anywhere', minWidth: 0,
            }}
          >
            <TextSegment text={visible} />
          </div>
        )}

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

        {state === 'speaking' && (
          <button
            className="hb-btn"
            onClick={onStopSpeaking}
            title="Stop speaking"
            style={{ ...chip, height: 26, padding: '0 0.75rem', gap: '0.4rem', pointerEvents: 'auto' }}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
              <rect x="5" y="5" width="14" height="14" rx="2" />
            </svg>
            STOP
          </button>
        )}

        {!hasText && (
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
            letterSpacing: '0.06em', color: 'var(--hb-icon-dim)', textAlign: 'center',
          }}>
            {configured
              ? `Type below, or hit the mic and just talk — ${agentName} answers out loud.`
              : 'Set AZURE_SPEECH_KEY on the backend to enable voice.'}
          </div>
        )}
      </div>
    </div>
  )
}
