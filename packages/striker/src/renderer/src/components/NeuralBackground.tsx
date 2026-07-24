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
        {/* Striker: the ambient stays, but dialed WAY down — a faint cyan breath
            over the neutral-dark void, not a wash. */}
        {/* Blob 1 — large, slow, dominant presence */}
        <div style={{ ...blobBase, width: '55vw', height: '55vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.10), transparent 68%)',
          animation: 'ambOrbit1 22s ease-in-out infinite',
        }} />
        {/* Blob 2 — medium, counter-path */}
        <div style={{ ...blobBase, width: '42vw', height: '42vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.07), transparent 65%)',
          animation: 'ambOrbit2 16s ease-in-out infinite',
        }} />
        {/* Blob 3 — small accent, fastest */}
        <div style={{ ...blobBase, width: '28vw', height: '28vh',
          background: 'radial-gradient(circle, rgba(var(--hb-accent-rgb), 0.05), transparent 60%)',
          animation: 'ambOrbit3 12s ease-in-out infinite',
        }} />
        {/* Light projection 1 — slow rotating sweep */}
        <div style={{ ...sweepBase,
          width: '120vw', height: '35vh',
          background: 'linear-gradient(90deg, transparent 10%, rgba(var(--hb-accent-rgb), 0.02) 40%, rgba(var(--hb-accent-rgb), 0.035) 50%, rgba(var(--hb-accent-rgb), 0.02) 60%, transparent 90%)',
          animation: 'ambSweep1 28s linear infinite',
        }} />
        {/* Light projection 2 — faster counter-sweep */}
        <div style={{ ...sweepBase,
          width: '100vw', height: '25vh',
          background: 'linear-gradient(90deg, transparent 15%, rgba(var(--hb-accent-rgb), 0.015) 45%, rgba(var(--hb-accent-rgb), 0.025) 50%, rgba(var(--hb-accent-rgb), 0.015) 55%, transparent 85%)',
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
}

const blobBase: React.CSSProperties = {
  position: 'absolute',
  borderRadius: '50%',
  filter: 'blur(80px)',
  willChange: 'top, left, opacity',
}

const sweepBase: React.CSSProperties = {
  position: 'absolute',
  top: '50%',
  left: '-10%',
  transformOrigin: '50% 50%',
  filter: 'blur(40px)',
  willChange: 'transform',
  opacity: 0.7,
}

const ambientKeyframes = `
@keyframes ambOrbit1 {
  0%   { top: -5%; left: -10%; opacity: 0.9; }
  20%  { top: 20%; left: 55%; opacity: 1; }
  40%  { top: 55%; left: 60%; opacity: 0.75; }
  60%  { top: 58%; left: 8%; opacity: 0.85; }
  80%  { top: 15%; left: -8%; opacity: 1; }
  100% { top: -5%; left: -10%; opacity: 0.9; }
}
@keyframes ambOrbit2 {
  0%   { top: 60%; left: 62%; opacity: 0.8; }
  25%  { top: 8%;  left: 35%; opacity: 1; }
  50%  { top: -8%; left: -5%; opacity: 0.7; }
  75%  { top: 42%; left: 2%;  opacity: 0.9; }
  100% { top: 60%; left: 62%; opacity: 0.8; }
}
@keyframes ambOrbit3 {
  0%   { top: 30%; left: 70%; opacity: 0.7; }
  33%  { top: 65%; left: 30%; opacity: 1; }
  66%  { top: 5%;  left: 50%; opacity: 0.8; }
  100% { top: 30%; left: 70%; opacity: 0.7; }
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
