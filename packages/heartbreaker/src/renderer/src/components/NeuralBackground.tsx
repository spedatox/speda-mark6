// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * AmbientBackground — living gradient atmosphere behind the glass.
 *
 * Multiple soft gradient blobs orbit on independent paths at different speeds,
 * crossing and separating so the void feels like a reactor chamber with plasma
 * drifting behind glass. Two slow-rotating light projections sweep across the
 * scene like volumetric light leaks from off-screen, adding a sense of depth
 * and directed energy. Everything uses the --hb-accent-rgb variable, so the
 * entire atmosphere shifts hue during an agent morph.
 */
export default function AmbientBackground() {
  return (
    <>
      <style>{ambientKeyframes}</style>
      <div style={containerStyle} aria-hidden>
        {/* Blob 1 — large, slow, dominant presence */}
        <div style={{ ...blobBase, width: '55vw', height: '55vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.24), transparent 68%)',
          animation: 'ambOrbit1 22s ease-in-out infinite',
        }} />
        {/* Blob 2 — medium, counter-path */}
        <div style={{ ...blobBase, width: '42vw', height: '42vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.16), transparent 65%)',
          animation: 'ambOrbit2 16s ease-in-out infinite',
        }} />
        {/* Blob 3 — small accent, fastest */}
        <div style={{ ...blobBase, width: '28vw', height: '28vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.12), transparent 60%)',
          animation: 'ambOrbit3 12s ease-in-out infinite',
        }} />
        {/* Light projection 1 — slow rotating sweep */}
        <div style={{ ...sweepBase,
          width: '120vw', height: '35vh',
          background: 'linear-gradient(90deg, transparent 10%, rgba(var(--hb-accent-rgb), 0.04) 40%, rgba(var(--hb-accent-rgb), 0.07) 50%, rgba(var(--hb-accent-rgb), 0.04) 60%, transparent 90%)',
          animation: 'ambSweep1 28s linear infinite',
        }} />
        {/* Light projection 2 — faster counter-sweep */}
        <div style={{ ...sweepBase,
          width: '100vw', height: '25vh',
          background: 'linear-gradient(90deg, transparent 15%, rgba(var(--hb-accent-rgb), 0.03) 45%, rgba(var(--hb-accent-rgb), 0.05) 50%, rgba(var(--hb-accent-rgb), 0.03) 55%, transparent 85%)',
          animation: 'ambSweep2 20s linear infinite reverse',
        }} />
      </div>
    </>
  )
}

const containerStyle: React.CSSProperties = {
  position: 'fixed', inset: 0,
  pointerEvents: 'none', zIndex: 0,
  overflow: 'hidden',
  contain: 'strict',
}

const blobBase: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  left: 0,
  borderRadius: '50%',
  filter: 'blur(80px)',
  willChange: 'transform, opacity',
  transform: 'translateZ(0)',
}

const sweepBase: React.CSSProperties = {
  position: 'absolute',
  top: '50%',
  left: '-10%',
  transformOrigin: '50% 50%',
  filter: 'blur(40px)',
  willChange: 'transform',
  transform: 'translateZ(0)',
  opacity: 0.7,
}

const ambientKeyframes = `
@keyframes ambOrbit1 {
  0%   { transform: translate3d(-10vw, -5vh, 0); opacity: 0.9; }
  20%  { transform: translate3d(55vw, 20vh, 0); opacity: 1; }
  40%  { transform: translate3d(60vw, 55vh, 0); opacity: 0.75; }
  60%  { transform: translate3d(8vw, 58vh, 0); opacity: 0.85; }
  80%  { transform: translate3d(-8vw, 15vh, 0); opacity: 1; }
  100% { transform: translate3d(-10vw, -5vh, 0); opacity: 0.9; }
}
@keyframes ambOrbit2 {
  0%   { transform: translate3d(62vw, 60vh, 0); opacity: 0.8; }
  25%  { transform: translate3d(35vw, 8vh, 0); opacity: 1; }
  50%  { transform: translate3d(-5vw, -8vh, 0); opacity: 0.7; }
  75%  { transform: translate3d(2vw, 42vh, 0); opacity: 0.9; }
  100% { transform: translate3d(62vw, 60vh, 0); opacity: 0.8; }
}
@keyframes ambOrbit3 {
  0%   { transform: translate3d(70vw, 30vh, 0); opacity: 0.7; }
  33%  { transform: translate3d(30vw, 65vh, 0); opacity: 1; }
  66%  { transform: translate3d(50vw, 5vh, 0); opacity: 0.8; }
  100% { transform: translate3d(70vw, 30vh, 0); opacity: 0.7; }
}
@keyframes ambSweep1 {
  0%   { transform: translateY(-50%) rotate(-15deg); }
  50%  { transform: translateY(-50%) rotate(15deg); }
  100% { transform: translateY(-50%) rotate(-15deg); }
}
@keyframes ambSweep2 {
  0%   { transform: translateY(-30%) rotate(20deg); }
  50%  { transform: translateY(-70%) rotate(-10deg); }
  100% { transform: translateY(-30%) rotate(20deg); }
}
`
