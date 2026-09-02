// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Screen lock — passcode hashing.
 *
 * The passcode is never stored, only a SHA-256 of it with a fixed application
 * salt, so reading localStorage does not hand anyone the passcode. This is a
 * PRIVACY lock, not a vault: the deck is an Electron app on the owner's own
 * machine, and anyone with the machine can read the transcript cache directly.
 * It exists so the screen cannot be read over a shoulder or by whoever walks
 * past the desk — sized to that threat, and not pretending to be more.
 */

const SALT = 'speda-mark6-screenlock-v1'

export async function hashPasscode(passcode: string): Promise<string> {
  const data = new TextEncoder().encode(`${SALT}:${passcode}`)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
}

/** Constant-time-ish compare. Both sides are fixed-length hex digests. */
export function hashesMatch(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return diff === 0
}
