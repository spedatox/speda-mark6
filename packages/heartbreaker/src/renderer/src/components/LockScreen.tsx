// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react'
import LockScreensaver, { AgentCard, CARD_KEYFRAMES } from './LockScreensaver'
import type { SaverAgent } from './LockScreensaver'
import { useT } from '../lib/i18n'

/**
 * SCREEN LOCK — the deck sealed behind a passcode.
 *
 * Covers everything, takes every key, and is not closable: the only ways out
 * are the right passcode or quitting the app. Ctrl+L raises it, and (when the
 * owner asks for it) so does opening the app and going idle.
 *
 * The time is deliberately NOT here — the screensaver carries it, and a lock
 * screen you are looking at is one you are about to answer, not consult.
 *
 * After `screensaverSeconds` of nothing, the keypad fades and the screensaver
 * takes the screen. Any input brings the keypad straight back — the screensaver
 * is a state of this screen, not a second screen stacked on top of it, so there
 * is never a moment where a keystroke goes somewhere the owner cannot see.
 *
 * Sibling in spirit to LockdownActivation: same vocabulary (hairlines, mono
 * labels in caps, one accent), opposite job — that one is a cinematic that
 * plays and leaves, this one stays until it is answered.
 */

const MONO = 'var(--font-mono)'

const KEYFRAMES = `
@keyframes lsIn     { from { opacity: 0; } to { opacity: 1; } }
@keyframes lsRise   { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@keyframes lsShake  {
  0%,100% { transform: translateX(0); }
  15%     { transform: translateX(-9px); }
  30%     { transform: translateX(8px); }
  45%     { transform: translateX(-6px); }
  60%     { transform: translateX(4px); }
  80%     { transform: translateX(-2px); }
}
@keyframes lsSweep  { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
@keyframes lsFadeIn { from { opacity: 0; } to { opacity: 1; } }
`

export default function LockScreen({ agent, agents, dwellMs, hasPasscode, screensaverSeconds, onUnlock }: {
  /** Whoever the deck was on when it locked — the keypad states that agent.
   *  Not the house brand: locking mid-conversation with Centurion and coming
   *  back to a Speda screen loses your place. */
  agent: SaverAgent
  /** The roster the screensaver parades. Striker passes its single agent. */
  agents: SaverAgent[]
  /** How long each agent holds before dissolving into the next. */
  dwellMs: number
  /** False when no passcode was ever set — the screen is then a privacy veil
   *  that any keypress lifts, and it says so rather than pretending. */
  hasPasscode: boolean
  /** Idle seconds before the screensaver takes over. 0 disables it. */
  screensaverSeconds: number
  /** Resolves true when the passcode is right. */
  onUnlock: (passcode: string) => Promise<boolean>
}) {
  const t = useT()
  const [entry, setEntry] = useState('')
  const [wrong, setWrong] = useState(false)
  const [saver, setSaver] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const idleRef = useRef<number | undefined>(undefined)

  /** Restart the idle countdown; leave the screensaver if it had started. */
  const stir = useCallback(() => {
    setSaver(false)
    window.clearTimeout(idleRef.current)
    if (screensaverSeconds > 0) {
      idleRef.current = window.setTimeout(() => setSaver(true), screensaverSeconds * 1000)
    }
  }, [screensaverSeconds])

  useEffect(() => {
    stir()
    return () => window.clearTimeout(idleRef.current)
  }, [stir])

  const submit = async () => {
    if (!hasPasscode) { await onUnlock(''); return }
    if (!entry) return
    const ok = await onUnlock(entry)
    if (ok) return
    setWrong(true)
    setEntry('')
    window.setTimeout(() => setWrong(false), 520)
  }
  // The window listener below is armed once, so it would otherwise close over
  // the first render's `submit` and forever compare an empty passcode.
  const submitRef = useRef(submit)
  submitRef.current = submit

  // The lock owns the keyboard while it is up. Everything underneath — the
  // composer, the switcher's arrow keys, Ctrl+L itself — must not see a thing.
  //
  // Enter is answered HERE rather than on the input's own onKeyDown. The field
  // is read-only when no passcode is set, and a screen whose whole instruction
  // is "press Enter" cannot afford for Enter to depend on which element the
  // focus happens to be sitting on.
  useEffect(() => {
    const swallow = (e: KeyboardEvent) => {
      stir()
      if (e.key === 'Escape' || (e.ctrlKey && e.key.toLowerCase() === 'l')) {
        e.preventDefault()
        e.stopPropagation()
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        e.stopPropagation()
        void submitRef.current()
        return
      }
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', swallow, true)
    return () => window.removeEventListener('keydown', swallow, true)
  }, [stir])

  useEffect(() => { if (!saver) inputRef.current?.focus() }, [saver])




  return (
    <div
      onMouseMove={stir}
      onMouseDown={() => { stir(); inputRef.current?.focus() }}
      onWheel={stir}
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        background: '#05080a',
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden', userSelect: 'none',
        animation: 'lsIn 0.22s ease both',
      }}
    >
      <style>{KEYFRAMES}</style>
      <style>{CARD_KEYFRAMES}</style>

      {/* Lit by the agent it is holding, the way the screensaver's beats are. */}
      <div aria-hidden style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: `radial-gradient(ellipse 70% 55% at 50% 42%, ${agent.accent}1c, transparent 70%)`,
      }} />

      {saver && (
        <div style={{ position: 'absolute', inset: 0, animation: 'lsFadeIn 0.9s ease both' }}>
          <LockScreensaver
            agents={agents}
            dwellMs={dwellMs}
            lockedLabel={t.lockScreen.locked}
          />
        </div>
      )}

      {!saver && (
        <>
          {/* A single hairline crossing the top — the only chrome on the screen. */}
          <div aria-hidden style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: 1,
            background: 'rgba(var(--hb-accent-rgb), 0.18)', overflow: 'hidden',
          }}>
            <div style={{
              width: '38%', height: '100%',
              background: 'linear-gradient(90deg, transparent, var(--hb-cyan-bright), transparent)',
              animation: 'lsSweep 5.5s linear infinite',
            }} />
          </div>

          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            gap: '1.6rem', animation: 'lsRise 0.4s ease 0.05s both',
          }}>
            {/* The same card the screensaver parades, at rest — the deck says
                which agent it is holding before it asks who you are. */}
            <AgentCard agent={agent} animate={false} />

            <span style={{
              fontFamily: MONO, fontSize: '0.62rem', letterSpacing: '0.34em',
              textTransform: 'uppercase', color: 'var(--hb-text-faint)',
            }}>
              {t.lockScreen.locked}
            </span>

            {/* The passcode line. A hairline, dots, nothing else. */}
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.9rem',
              animation: wrong ? 'lsShake 0.5s cubic-bezier(0.36,0.07,0.19,0.97) both' : undefined,
            }}>
              <input
                ref={inputRef}
                type="password"
                autoFocus
                value={entry}
                readOnly={!hasPasscode}
                onChange={e => { setEntry(e.target.value); stir() }}
                placeholder={hasPasscode ? t.lockScreen.enterPasscode : t.lockScreen.pressEnter}
                aria-label={t.lockScreen.enterPasscode}
                style={{
                  width: 'min(340px, 76vw)', height: 46,
                  background: 'transparent', outline: 'none',
                  border: 'none',
                  borderBottom: `1px solid ${wrong ? 'rgba(232,86,74,0.85)' : 'rgba(var(--hb-accent-rgb), 0.4)'}`,
                  textAlign: 'center',
                  fontFamily: MONO, fontSize: '1.05rem', letterSpacing: '0.5em',
                  paddingLeft: '0.5em',
                  color: wrong ? '#e8564a' : 'var(--hb-text)',
                  transition: 'border-color 0.2s, color 0.2s',
                  userSelect: 'text',
                }}
              />
              <span style={{
                fontFamily: MONO, fontSize: '0.58rem', letterSpacing: '0.26em',
                textTransform: 'uppercase',
                color: wrong ? '#e8564a' : 'var(--hb-text-faint)',
                minHeight: '0.9rem',
              }}>
                {wrong
                  ? t.lockScreen.wrong
                  : hasPasscode ? t.lockScreen.hint : t.lockScreen.noPasscode}
              </span>
            </div>

            <button
              onClick={() => void submit()}
              className="glass-round"
              style={{
                height: 34, padding: '0 22px',
                fontFamily: MONO, fontSize: '0.6rem',
                letterSpacing: '0.28em', textTransform: 'uppercase',
                border: '1px solid rgba(var(--hb-accent-rgb), 0.32)',
                background: 'rgba(var(--hb-accent-rgb), 0.1)',
                color: 'var(--hb-cyan-bright)', cursor: 'pointer',
              }}
            >
              {t.lockScreen.unlock}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
