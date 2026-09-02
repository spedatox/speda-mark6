// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef } from 'react'

/**
 * The lock screen's screensaver — a gyroscope on a black field.
 *
 * Three rings of a real 3D gyro, orthographically projected, rotating on their
 * own axes at rates that never come back into phase, around a core that
 * breathes. Depth is carried by alpha alone: the half of each ring travelling
 * away from the viewer dims, which is what reads as three-dimensional without
 * a single shaded surface.
 *
 * The whole composition drifts on two slow sines. That is not decoration —
 * this runs unattended for hours on a panel that may be OLED, and a static
 * bright figure at screen centre is how you burn one.
 *
 * Deliberately austere: hairlines, one accent hue taken from the live theme,
 * no glow stacks, no particles, no corner brackets (the deck's design contract
 * bans those). Everything drawn is either the gyro, the clock, or a readout
 * that states something true.
 */

/** Points per ring polyline — enough that the ellipse reads as a curve. */
const SEGMENTS = 200

interface Ring {
  /** Radius as a fraction of the composition's half-size. */
  r: number
  /** Tilt of the ring's plane, radians. */
  tilt: number
  /** Spin rate, radians/second — deliberately non-harmonic across rings. */
  spin: number
  /** Precession of the tilt, radians/second. */
  precess: number
  width: number
  /** Ticks drawn along the ring, or 0 for a bare ring. */
  ticks: number
}

const RINGS: Ring[] = [
  { r: 1.00, tilt: 0.62, spin: 0.115, precess: 0.041, width: 1.1, ticks: 60 },
  { r: 0.74, tilt: 1.18, spin: -0.167, precess: 0.029, width: 1.0, ticks: 0 },
  { r: 0.50, tilt: 0.34, spin: 0.233, precess: -0.053, width: 1.0, ticks: 24 },
]

/** Read the live theme accent as an "r, g, b" triplet. */
function accentRGB(): string {
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue('--hb-accent-rgb').trim()
  return v || '127, 164, 196'
}

export default function LockScreensaver({ agentName, modelNumber, lockedLabel }: {
  agentName: string
  modelNumber: string
  /** The one word under the clock — localised by the caller. */
  lockedLabel: string
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const rgb = accentRGB()
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const rate = calm ? 0.25 : 1

    let w = 0, h = 0, dpr = 1
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = canvas.clientWidth
      h = canvas.clientHeight
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    window.addEventListener('resize', resize)

    const t0 = performance.now()
    let raf = 0

    /** One ring, as a depth-shaded polyline plus its ticks. */
    const drawRing = (ring: Ring, size: number, t: number) => {
      const spin = t * ring.spin * rate
      const tilt = ring.tilt + Math.sin(t * ring.precess * rate) * 0.42
      const cosT = Math.cos(tilt), sinT = Math.sin(tilt)
      const cosS = Math.cos(spin), sinS = Math.sin(spin)
      const R = ring.r * size

      // A circle in its own plane, tilted about x and then spun about y — the
      // two rotations a gimbal actually has.
      const pt = (a: number) => {
        const x0 = Math.cos(a) * R, y0 = Math.sin(a) * R
        const y1 = y0 * cosT, z1 = y0 * sinT
        return { x: x0 * cosS + z1 * sinS, y: y1, z: -x0 * sinS + z1 * cosS }
      }

      let prev = pt(0)
      for (let i = 1; i <= SEGMENTS; i++) {
        const p = pt((i / SEGMENTS) * Math.PI * 2)
        // Depth to alpha: the front of the ring is legible, the back a whisper.
        const depth = (p.z / R + 1) / 2
        ctx.strokeStyle = `rgba(${rgb}, ${(0.14 + depth * 0.80).toFixed(3)})`
        ctx.lineWidth = ring.width * (0.6 + depth * 0.6)
        ctx.beginPath()
        ctx.moveTo(prev.x, prev.y)
        ctx.lineTo(p.x, p.y)
        ctx.stroke()
        prev = p
      }

      if (!ring.ticks) return
      ctx.lineWidth = 1
      for (let i = 0; i < ring.ticks; i++) {
        const p = pt((i / ring.ticks) * Math.PI * 2)
        const depth = (p.z / R + 1) / 2
        // Ticks stand outward along the ring's own radius, every fifth longer.
        const k = 1 + (i % 5 === 0 ? 10 : 5) / R
        ctx.strokeStyle = `rgba(${rgb}, ${(0.08 + depth * 0.62).toFixed(3)})`
        ctx.beginPath()
        ctx.moveTo(p.x, p.y)
        ctx.lineTo(p.x * k, p.y * k)
        ctx.stroke()
      }
    }

    /** The core: a breathing disc under three counter-rotating arcs. */
    const drawCore = (size: number, t: number) => {
      const breath = 0.5 + 0.5 * Math.sin(t * 0.55 * rate)
      const r = size * (0.085 + breath * 0.012)

      const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 3.4)
      glow.addColorStop(0, `rgba(${rgb}, ${0.34 + breath * 0.2})`)
      glow.addColorStop(0.45, `rgba(${rgb}, 0.05)`)
      glow.addColorStop(1, `rgba(${rgb}, 0)`)
      ctx.fillStyle = glow
      ctx.beginPath()
      ctx.arc(0, 0, r * 3.4, 0, Math.PI * 2)
      ctx.fill()

      ctx.fillStyle = `rgba(${rgb}, ${0.22 + breath * 0.16})`
      ctx.beginPath()
      ctx.arc(0, 0, r, 0, Math.PI * 2)
      ctx.fill()

      for (let i = 0; i < 3; i++) {
        const a = t * (i % 2 ? -0.9 : 0.62) * rate + i * 2.1
        ctx.strokeStyle = `rgba(${rgb}, ${0.7 - i * 0.14})`
        ctx.lineWidth = 1.2
        ctx.beginPath()
        ctx.arc(0, 0, r * (1.55 + i * 0.42), a, a + 1.05)
        ctx.stroke()
      }
    }

    /** A low, sparse activity trace under the gyro — never loud. */
    const drawTrace = (size: number, t: number) => {
      const bars = 64
      const span = size * 1.55
      const y = size * 1.16
      ctx.lineWidth = 1
      for (let i = 0; i < bars; i++) {
        const x = -span / 2 + (i / (bars - 1)) * span
        // Two out-of-phase sines: a trace that never visibly repeats and needs
        // no random source (which would flicker between frames).
        const a = Math.sin(t * 0.7 * rate + i * 0.55) * Math.sin(t * 0.23 * rate + i * 0.17)
        const bar = Math.abs(a) * size * 0.055 + 1
        ctx.strokeStyle = `rgba(${rgb}, ${(0.14 + Math.abs(a) * 0.42).toFixed(3)})`
        ctx.beginPath()
        ctx.moveTo(x, y - bar)
        ctx.lineTo(x, y + bar)
        ctx.stroke()
      }
    }

    const two = (n: number) => String(n).padStart(2, '0')

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      const t = (now - t0) / 1000

      ctx.clearRect(0, 0, w, h)

      // Vignette — pulls the eye to the centre without lighting anything.
      const vg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.72)
      vg.addColorStop(0, 'rgba(12, 17, 21, 0.6)')
      vg.addColorStop(1, 'rgba(0, 0, 0, 0.92)')
      ctx.fillStyle = vg
      ctx.fillRect(0, 0, w, h)

      // Burn-in drift, on two periods that do not resolve into a short cycle.
      const dx = Math.sin(t / 37) * w * 0.055
      const dy = Math.cos(t / 53) * h * 0.05
      // The gyro plus its readouts stand about 3.2 sizes tall, so the scale
      // comes off the SHORT edge with that stack budgeted in — otherwise the
      // clock walks off the bottom of a wide, short window.
      const size = Math.min(w * 0.28, h * 0.24)

      ctx.save()
      // Hung high: the stack below the gyro is what balances the composition.
      ctx.translate(w / 2 + dx, h / 2 - size * 0.62 + dy)
      for (const ring of RINGS) drawRing(ring, size, t)
      drawCore(size, t)
      drawTrace(size, t)

      // The clock, hung below the gyro, drifting with it.
      const d = new Date()
      ctx.textAlign = 'center'
      ctx.textBaseline = 'alphabetic'
      ctx.fillStyle = 'rgba(219, 230, 236, 0.88)'
      ctx.font = `300 ${Math.round(size * 0.62)}px 'Rajdhani', sans-serif`
      ctx.letterSpacing = `${Math.round(size * 0.06)}px`
      ctx.fillText(`${two(d.getHours())}:${two(d.getMinutes())}`, 0, size * 1.78)

      ctx.font = `500 ${Math.round(size * 0.088)}px 'Inter', sans-serif`
      ctx.letterSpacing = `${Math.round(size * 0.036)}px`
      ctx.fillStyle = `rgba(${rgb}, 0.62)`
      ctx.fillText(`${agentName} ${modelNumber} — ${lockedLabel}`, 0, size * 2.0)

      ctx.fillStyle = 'rgba(93, 111, 122, 0.75)'
      ctx.font = `400 ${Math.round(size * 0.078)}px 'Inter', sans-serif`
      ctx.fillText(
        d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }),
        0, size * 2.19,
      )
      ctx.letterSpacing = '0px'
      ctx.restore()
    }

    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
    }
  }, [agentName, modelNumber, lockedLabel])

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }}
    />
  )
}
