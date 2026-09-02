// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings } from '../store/settings'
import { hashPasscode, hashesMatch } from './lock'

/**
 * The screen lock's state machine, in one place so both desktop clients mount
 * it identically: what raises the lock (Ctrl+L, launch, going idle) and what
 * lowers it (the right passcode).
 *
 * The initial state is computed on the FIRST render, not in an effect — an
 * effect would paint one frame of the unlocked deck before covering it, which
 * for a lock is the whole failure.
 */
export function useScreenLock() {
  const { settings } = useSettings()
  const { lockOnLaunch, lockPasscodeHash, lockIdleMinutes } = settings

  const [locked, setLocked] = useState(() => lockOnLaunch)
  const lockedRef = useRef(locked)
  lockedRef.current = locked

  const lock = useCallback(() => setLocked(true), [])

  const unlock = useCallback(async (passcode: string) => {
    // No passcode set: the screen is a privacy veil, and anything lifts it.
    if (!lockPasscodeHash) { setLocked(false); return true }
    const ok = hashesMatch(await hashPasscode(passcode), lockPasscodeHash)
    if (ok) setLocked(false)
    return ok
  }, [lockPasscodeHash])

  // Ctrl+L, everywhere. Capture phase so a focused composer or modal cannot
  // eat it, and never while already locked (the lock screen swallows keys).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.metaKey) return
      if (e.key.toLowerCase() !== 'l') return
      if (lockedRef.current) return
      e.preventDefault()
      lock()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [lock])

  // Idle auto-lock. Off at 0 — the owner may well want Ctrl+L and nothing else.
  useEffect(() => {
    if (lockIdleMinutes <= 0) return
    let timer: number | undefined
    const arm = () => {
      window.clearTimeout(timer)
      if (lockedRef.current) return
      timer = window.setTimeout(lock, lockIdleMinutes * 60_000)
    }
    const events: (keyof WindowEventMap)[] = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart']
    events.forEach(ev => window.addEventListener(ev, arm, { passive: true }))
    arm()
    return () => {
      window.clearTimeout(timer)
      events.forEach(ev => window.removeEventListener(ev, arm))
    }
  }, [lockIdleMinutes, lock, locked])

  return { locked, lock, unlock, hasPasscode: !!lockPasscodeHash }
}
