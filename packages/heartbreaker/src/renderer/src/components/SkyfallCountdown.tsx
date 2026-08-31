// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react'
import { fireSkyfall, abortSkyfall } from '../lib/api'
import type { AppConfig } from '../lib/types'
import type { SkyfallArm, SkyfallResult } from '../lib/api'
import { useT } from '../lib/i18n'

/**
 * SKYFALL — the countdown. This screen IS the protocol.
 *
 * Everything else in Skyfall exists so that a request cannot go out without the
 * owner having had a window to stop it, and this component is that window. Both
 * ways in — Speda arming it by voice, or a project picked in settings — land
 * here, and neither can skip it.
 *
 * THE ABORT IS THE ABSENCE OF AN ACTION, NOT AN ACTION.
 * Aborting does not cancel a request; it means this component never makes one.
 * There is nothing in flight to race and no cancel that can arrive too late,
 * and every failure mode — a crashed renderer, a closed window, a machine that
 * went to sleep — falls toward "did not fire". `POST /abort` afterwards only
 * writes it down; if that call fails, the abort still happened.
 *
 * WHY A DEADLINE AND NOT A DECREMENTING COUNTER.
 * The clock is `deadline - now`, re-read every frame. A counter that subtracts
 * on a timer drifts when the tab is throttled or the machine suspends, and the
 * direction it drifts is *longer* — the owner watches "3" sit there for eight
 * seconds while the real deadline slides away from them.
 *
 * IF THE CLOCK STOPPED BEING SHOWN, IT DOES NOT FIRE.
 * The wall clock alone is not enough, because `requestAnimationFrame` does not
 * run at all in a hidden window: minimise it, alt-tab, let the machine sleep,
 * and no frame happens until the window is looked at again — at which point
 * `now` is already past the deadline and the naive reading is "fire
 * immediately". That is the worst possible behaviour: the owner returns to a
 * request going out with no countdown and no chance to stop it.
 *
 * So a frame gap longer than STALL_MS means the countdown was not on screen
 * while it mattered, and the launch is ABORTED instead. It restates the rule
 * the whole protocol is built on — the screen IS the protocol, so a screen
 * nobody was shown did not run — and it fails toward "did not fire", which is
 * the only direction this may ever fail in.
 *
 * ESC ABORTS. There is no click-outside-to-dismiss and no close button that
 * quietly means "abort" — the two outcomes are named, in words, on two buttons.
 * A launch screen you can dismiss by missing is a launch screen that lies about
 * what dismissing did.
 */
/** A frame gap past this means the window was not being drawn. Comfortably
 *  above a dropped frame or a slow render, comfortably below the shortest
 *  countdown the server will accept (3s). */
const STALL_MS = 1500

export default function SkyfallCountdown({ config, arm, onClose }: {
  config: AppConfig
  arm: SkyfallArm
  onClose: () => void
}) {
  const t = useT()
  const total = Math.max(1, arm.countdown_seconds || 10)
  const deadlineRef = useRef(Date.now() + total * 1000)
  // Last frame's wall time. Frames are ~16ms apart while the window is visible;
  // anything past this gap means it was not.
  const lastTickRef = useRef(Date.now())
  const [remaining, setRemaining] = useState(total)
  // 'armed' → 'firing' → 'done' | 'aborted' | 'stalled'. One-way; nothing
  // returns to armed. 'stalled' is an abort with a different reason to show.
  const [phase, setPhase] = useState<'armed' | 'firing' | 'done' | 'aborted' | 'stalled'>('armed')
  const [result, setResult] = useState<SkyfallResult | null>(null)
  // Guards the one thing that must happen at most once, ever.
  const firedRef = useRef(false)

  const fire = useCallback(async () => {
    if (firedRef.current) return
    firedRef.current = true
    setPhase('firing')
    setResult(await fireSkyfall(config, arm.project_id))
    setPhase('done')
  }, [config, arm.project_id])

  const abort = useCallback((secondsLeft: number, reason: 'aborted' | 'stalled' = 'aborted') => {
    if (firedRef.current) return
    // Claim the trigger so a tick racing this click cannot fire behind it.
    firedRef.current = true
    setPhase(reason)
    void abortSkyfall(config, arm.project_id, secondsLeft)
  }, [config, arm.project_id])

  useEffect(() => {
    if (phase !== 'armed') return
    let raf = 0
    lastTickRef.current = Date.now()
    const tick = () => {
      const now = Date.now()
      const gap = now - lastTickRef.current
      lastTickRef.current = now
      if (gap > STALL_MS) {
        // The window was hidden, minimised or asleep. Whatever the wall clock
        // says, this countdown was not shown — so it does not fire.
        abort(Math.max(0, (deadlineRef.current - now) / 1000), 'stalled')
        return
      }
      const left = (deadlineRef.current - now) / 1000
      setRemaining(left > 0 ? left : 0)
      if (left <= 0) { void fire(); return }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [phase, fire, abort])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      if (phase === 'armed') abort(remaining)
      else if (phase !== 'firing') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phase, remaining, abort, onClose])

  const whole = Math.ceil(remaining)
  // Under five seconds the whole screen starts breathing faster. The pulse is
  // driven off the fractional part, so it lands ON each second rather than
  // free-running against it.
  const urgent = phase === 'armed' && remaining <= 5
  const beat = phase === 'armed' ? 1 - (remaining - Math.floor(remaining)) : 0

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t.skyfall.armedTitle}
      style={{
        position: 'fixed', inset: 0, zIndex: 4000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: `radial-gradient(circle at 50% 45%, rgba(216,72,60,${
          phase === 'armed' ? 0.1 + (urgent ? beat * 0.14 : 0.02) : 0.06
        }) 0%, rgba(6,7,9,0.97) 62%, rgba(4,5,6,0.99) 100%)`,
        backdropFilter: 'blur(3px)',
        transition: 'background 120ms linear',
      }}
    >
      {/* The sweep: a bar draining left-to-right, so the time left is legible
          from across a room without reading the number. */}
      <div style={{
        position: 'absolute', top: 0, left: 0, height: 2,
        width: `${phase === 'armed' ? (remaining / total) * 100 : 0}%`,
        background: '#d8483c',
        boxShadow: '0 0 18px rgba(216,72,60,0.9)',
        transition: 'width 80ms linear',
      }} />

      <div style={{
        width: 'min(560px, 88vw)', display: 'flex', flexDirection: 'column',
        alignItems: 'center', gap: 22, textAlign: 'center', padding: 24,
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.62rem', letterSpacing: '0.42em',
          textTransform: 'uppercase',
          color: phase === 'aborted' || phase === 'stalled' ? 'var(--hb-text-faint)' : '#e5897c',
        }}>
          {phase === 'armed' ? t.skyfall.armed
            : phase === 'firing' ? t.skyfall.firing
              : phase === 'stalled' ? t.skyfall.stalled
                : phase === 'aborted' ? t.skyfall.aborted : t.skyfall.complete}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{
            fontFamily: 'var(--font-ui)', fontSize: '1.6rem', fontWeight: 700,
            letterSpacing: '0.02em', color: 'var(--hb-text)',
          }}>
            {arm.name}
          </div>
          {arm.description && (
            <div style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', lineHeight: 1.6 }}>
              {arm.description}
            </div>
          )}
          {/* What is about to be sent, stated plainly. A launch screen that does
              not say where it is aiming is theatre with no information in it. */}
          <div style={{
            marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: '0.68rem',
            color: 'var(--hb-text-dim)', wordBreak: 'break-all', userSelect: 'text',
          }}>
            {arm.method} {arm.url}
          </div>
        </div>

        {phase === 'armed' && (
          <>
            <div style={{
              fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums',
              fontSize: urgent ? '7.5rem' : '6.5rem', lineHeight: 1, fontWeight: 700,
              color: urgent ? '#ff6a58' : '#e5897c',
              textShadow: `0 0 ${urgent ? 46 + beat * 26 : 28}px rgba(216,72,60,${urgent ? 0.75 : 0.45})`,
              transform: `scale(${urgent ? 1 + beat * 0.045 : 1})`,
              transition: 'font-size 160ms ease, transform 90ms linear',
            }}>
              {whole}
            </div>
            <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
              {t.skyfall.willFire}
            </div>
          </>
        )}

        {phase === 'firing' && (
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '1.1rem', color: '#ff6a58',
            letterSpacing: '0.2em',
          }}>
            {t.skyfall.sending}
          </div>
        )}

        {(phase === 'aborted' || phase === 'stalled') && (
          <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text-dim)', lineHeight: 1.7 }}>
            {phase === 'stalled' ? t.skyfall.stalledBody : t.skyfall.abortedBody}
          </div>
        )}

        {phase === 'done' && result && <Outcome result={result} />}

        <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
          {phase === 'armed' ? (
            <>
              {/* Abort is the wide, bright one. The screen should be easier to
                  stop than to hurry along. */}
              <button
                onClick={() => abort(remaining)}
                className="glass-round"
                style={{
                  minWidth: 190, height: 46, padding: '0 26px',
                  border: '1px solid rgba(255,255,255,0.22)',
                  background: 'rgba(255,255,255,0.1)', color: 'var(--hb-text)',
                  fontFamily: 'var(--font-ui)', fontSize: '0.95rem', fontWeight: 600,
                  letterSpacing: '0.06em', cursor: 'pointer',
                }}
              >
                {t.skyfall.abort}
              </button>
              {/* Calls fire() outright rather than setting the deadline to now:
                  moving the deadline only fires if a FRAME then runs, which is
                  precisely what a hidden window does not do. An explicit press
                  should not depend on the animation loop. */}
              <button
                onClick={() => { void fire() }}
                className="glass-round"
                style={{
                  height: 46, padding: '0 20px',
                  border: '1px solid rgba(216,72,60,0.4)',
                  background: 'rgba(216,72,60,0.12)', color: '#e5897c',
                  fontFamily: 'var(--font-ui)', fontSize: '0.875rem',
                  cursor: 'pointer',
                }}
                title={t.skyfall.fireNowTitle}
              >
                {t.skyfall.fireNow}
              </button>
            </>
          ) : phase !== 'firing' ? (
            <button
              onClick={onClose}
              className="glass-round"
              style={{
                minWidth: 150, height: 44, padding: '0 24px',
                border: '1px solid rgba(255,255,255,0.14)',
                background: 'var(--glass-sheen)', color: 'var(--hb-text)',
                fontFamily: 'var(--font-ui)', fontSize: '0.9rem', cursor: 'pointer',
              }}
            >
              {t.skyfall.close}
            </button>
          ) : null}
        </div>

        {phase === 'armed' && (
          <div style={{ fontSize: '0.72rem', color: 'var(--hb-text-faint)' }}>
            {t.skyfall.escHint}
          </div>
        )}
      </div>
    </div>
  )
}

/** What came back. `fired` and `ok` are read separately: "it went out and the
 *  target said 500" is a different sentence from "it never left", and the one
 *  case that must never be rendered as either is "we do not know". */
function Outcome({ result }: { result: SkyfallResult }) {
  const t = useT()
  const good = result.fired && result.ok
  const tint = !result.fired ? 'var(--hb-text-faint)' : good ? '#7fd6a2' : '#e5897c'

  return (
    <div style={{
      width: '100%', display: 'flex', flexDirection: 'column', gap: 8,
      padding: '14px 16px', textAlign: 'left',
      background: 'rgba(255,255,255,0.03)',
      border: `1px solid ${good ? 'rgba(127,214,162,0.3)' : 'rgba(216,72,60,0.3)'}`,
      fontFamily: 'var(--font-mono)', fontSize: '0.7rem',
    }}>
      <div style={{ color: tint, letterSpacing: '0.14em', textTransform: 'uppercase', fontSize: '0.6rem' }}>
        {!result.fired ? t.skyfall.notSent
          : good ? t.skyfall.delivered(result.status)
            : result.status ? t.skyfall.rejected(result.status) : t.skyfall.failed}
      </div>
      {result.error && (
        <div style={{ color: 'var(--hb-text-dim)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
          {result.error}
        </div>
      )}
      {result.body && (
        <div style={{
          color: 'var(--hb-text-dim)', lineHeight: 1.6, maxHeight: 160,
          overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          userSelect: 'text',
        }}>
          {result.body}
          {result.truncated && `\n… ${t.skyfall.truncated}`}
        </div>
      )}
    </div>
  )
}
