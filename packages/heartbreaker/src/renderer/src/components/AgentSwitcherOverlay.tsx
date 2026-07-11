import { useEffect, useState, useRef } from 'react'
import { ROSTER, agentColor } from '../lib/agents'
import { BRANDS } from '../profile/brands'
import { Avatar } from './CommBubble'

/**
 * AGENT SWITCHER — the "armoury" selector (Alt+A).
 *
 * A cinematic, Stark-style agent bay: the whole room glows in the focused
 * agent's colour, the selected avatar sits inside dual counter-rotating HUD
 * rings, the roster materialises in a staggered boot sequence, a live
 * designation panel reads out the agent's spec, and confirming flares the pod
 * before handing off to the theme morph. Fluid-glass language throughout — no
 * grids, brackets, ticks or scanlines.
 */

const SMOOTH = 'cubic-bezier(0.16, 1, 0.3, 1)'

const KEYFRAMES = `
@keyframes swBg { from { opacity: 0; backdrop-filter: blur(0); } to { opacity: 1; } }
@keyframes swTitle {
  0%   { opacity: 0; transform: translateY(-14px); clip-path: inset(0 100% 0 0); filter: blur(6px); }
  100% { opacity: 1; transform: translateY(0);     clip-path: inset(0 0 0 0);   filter: blur(0); }
}
@keyframes swUnderline { from { transform: scaleX(0); opacity: 0; } to { transform: scaleX(1); opacity: 1; } }
@keyframes swSub { from { opacity: 0; letter-spacing: 0.6em; } to { opacity: 0.9; letter-spacing: 0.34em; } }
@keyframes podRise {
  0%   { opacity: 0; transform: translateY(46px) scale(0.86); filter: blur(9px); }
  60%  { opacity: 1; }
  100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes ringSpin    { to { transform: rotate(360deg); } }
@keyframes ringSpinRev { to { transform: rotate(-360deg); } }
@keyframes heroGlow {
  0%, 100% { opacity: 0.55; transform: scale(1); }
  50%      { opacity: 0.9;  transform: scale(1.06); }
}
@keyframes desigIn {
  from { opacity: 0; transform: translateY(10px); filter: blur(5px); }
  to   { opacity: 1; transform: translateY(0);    filter: blur(0); }
}
@keyframes sweep { from { transform: translateX(-120%); opacity: 0; } 30% { opacity: 0.7; } to { transform: translateX(120%); opacity: 0; } }
@keyframes hintIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 0.85; transform: translateY(0); } }
@keyframes podFlare {
  0%   { transform: scale(1); }
  35%  { transform: scale(1.14); filter: brightness(1.9); }
  100% { transform: scale(1.6); filter: brightness(2.6); opacity: 0; }
}
@keyframes floatUp {
  from { transform: translateY(20px); opacity: 0; }
  20%  { opacity: 0.5; }
  to   { transform: translateY(-120px); opacity: 0; }
}
`

/** Dual counter-rotating HUD rings + reactor glow behind the selected avatar. */
function Rings({ color }: { color: string }) {
  const S = 150
  const c = S / 2
  return (
    <div style={{
      position: 'absolute', top: '50%', left: '50%',
      width: S, height: S, transform: 'translate(-50%,-50%)',
      pointerEvents: 'none',
    }}>
      {/* reactor glow */}
      <div style={{
        position: 'absolute', inset: '18%', borderRadius: '50%',
        background: `radial-gradient(circle, ${color}66 0%, ${color}22 45%, transparent 70%)`,
        filter: 'blur(6px)', animation: 'heroGlow 3s ease-in-out infinite',
      }} />
      {/* outer dashed ring — slow */}
      <svg width={S} height={S} style={{ position: 'absolute', inset: 0, animation: 'ringSpin 14s linear infinite' }}>
        <circle cx={c} cy={c} r={c - 3} fill="none" stroke={color} strokeWidth={1.4}
          strokeDasharray="2 9" opacity={0.7} />
      </svg>
      {/* inner arc — faster, opposite direction */}
      <svg width={S} height={S} style={{ position: 'absolute', inset: 0, animation: 'ringSpinRev 6s linear infinite' }}>
        <circle cx={c} cy={c} r={c - 16} fill="none" stroke={color} strokeWidth={2.4}
          strokeDasharray={`${Math.PI * (c - 16) * 0.28} ${Math.PI * (c - 16) * 2}`}
          strokeLinecap="round"
          style={{ filter: `drop-shadow(0 0 5px ${color})` }} />
      </svg>
      {/* faint counter arc */}
      <svg width={S} height={S} style={{ position: 'absolute', inset: 0, animation: 'ringSpin 9s linear infinite' }}>
        <circle cx={c} cy={c} r={c - 9} fill="none" stroke={color} strokeWidth={1}
          strokeDasharray={`${Math.PI * (c - 9) * 0.12} ${Math.PI * (c - 9) * 2}`}
          strokeLinecap="round" opacity={0.6} />
      </svg>
    </div>
  )
}

export default function AgentSwitcherOverlay({
  onSelect,
  onClose,
  currentAgentId,
}: {
  onSelect: (agentId: string) => void
  onClose: () => void
  currentAgentId: string
}) {
  const [selectedIndex, setSelectedIndex] = useState(() => Math.max(0, ROSTER.indexOf(currentAgentId)))
  const [confirming, setConfirming] = useState<number | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const podRefs = useRef<(HTMLButtonElement | null)[]>([])

  const selColor = agentColor(ROSTER[selectedIndex])
  const selBrand = BRANDS[ROSTER[selectedIndex]]

  useEffect(() => { containerRef.current?.focus() }, [])

  // Keep the focused pod in view as it moves.
  useEffect(() => {
    podRefs.current[selectedIndex]?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
  }, [selectedIndex])

  const choose = (index: number) => {
    if (confirming !== null) return
    setConfirming(index)
    // Let the lock-in flare play, then hand off to the theme morph.
    setTimeout(() => onSelect(ROSTER[index]), 400)
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (confirming !== null) return
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
      else if (e.key === 'ArrowRight') { e.preventDefault(); setSelectedIndex(i => (i + 1) % ROSTER.length) }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); setSelectedIndex(i => (i - 1 + ROSTER.length) % ROSTER.length) }
      else if (e.key === 'Enter') { e.preventDefault(); choose(selectedIndex) }
      else if (/^[1-9]$/.test(e.key)) {
        const n = parseInt(e.key, 10) - 1
        if (n < ROSTER.length) setSelectedIndex(n)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [selectedIndex, confirming, onClose]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        // The room takes the focused agent's hue — tweens as you move.
        background: `radial-gradient(ellipse 80% 60% at 50% 46%, ${selColor}1f 0%, rgba(3,7,10,0.86) 55%, rgba(2,5,7,0.94) 100%)`,
        backdropFilter: 'var(--hb-holo-blur)', WebkitBackdropFilter: 'var(--hb-holo-blur)',
        transition: 'background 0.6s ease',
        animation: `swBg 0.5s ${SMOOTH} both`,
      }}
    >
      <style>{KEYFRAMES}</style>

      {/* drifting light motes for atmosphere */}
      {[...Array(7)].map((_, i) => (
        <span key={i} aria-hidden style={{
          position: 'absolute', bottom: '28%', left: `${12 + i * 12}%`,
          width: 3, height: 3, borderRadius: '50%', background: selColor,
          boxShadow: `0 0 8px ${selColor}`, opacity: 0,
          animation: `floatUp ${5 + (i % 4)}s linear ${i * 0.7}s infinite`,
          transition: 'background 0.6s ease',
        }} />
      ))}

      {/* ── Title ─────────────────────────────────────────────────────────── */}
      <div style={{ textAlign: 'center', marginBottom: '2.6rem', pointerEvents: 'none' }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.6rem', letterSpacing: '0.34em',
          color: selColor, marginBottom: '0.7rem', textTransform: 'uppercase',
          animation: `swSub 0.8s ${SMOOTH} 0.15s both`, transition: 'color 0.6s ease',
        }}>
          Armoury // SPEDA Mark VI
        </div>
        <h1 style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: 700,
          fontSize: 'clamp(1.7rem, 5vw, 3rem)', letterSpacing: '0.34em',
          textTransform: 'uppercase', color: '#eef7fa', margin: 0, lineHeight: 1,
          textShadow: `0 0 28px ${selColor}88`, transition: 'text-shadow 0.6s ease',
          animation: `swTitle 0.9s ${SMOOTH} both`,
        }}>
          Select Your Agent
        </h1>
        <div style={{
          height: 2, marginTop: '0.9rem',
          background: `linear-gradient(90deg, transparent, ${selColor}, transparent)`,
          transformOrigin: 'center', transition: 'background 0.6s ease',
          animation: `swUnderline 0.8s ${SMOOTH} 0.4s both`,
          boxShadow: `0 0 12px ${selColor}`,
        }} />
      </div>

      {/* ── The bay ───────────────────────────────────────────────────────── */}
      <div
        ref={containerRef}
        tabIndex={-1}
        onClick={e => e.stopPropagation()}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'flex-start',
          gap: '0.6rem', maxWidth: '94vw', overflowX: 'auto', overflowY: 'hidden',
          padding: '3rem 2rem', outline: 'none', perspective: 900,
          scrollbarWidth: 'none',
        }}
      >
        {ROSTER.map((id, index) => {
          const brand = BRANDS[id]
          if (!brand) return null
          const color = agentColor(id)
          const isSel = index === selectedIndex
          const isConfirming = confirming === index

          return (
            <button
              key={id}
              ref={el => { podRefs.current[index] = el }}
              onClick={() => (isSel ? choose(index) : setSelectedIndex(index))}
              onMouseEnter={() => confirming === null && setSelectedIndex(index)}
              style={{
                position: 'relative', flexShrink: 0,
                width: 132, height: 220,
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                gap: '1.1rem', border: 'none', background: 'transparent', cursor: 'pointer',
                color, // glass-tint reads currentColor
                opacity: confirming !== null && !isConfirming ? 0.15 : isSel ? 1 : 0.4,
                filter: isSel ? 'none' : 'grayscale(0.5)',
                transform: isSel ? 'translateY(-10px) scale(1.12)' : 'scale(0.9)',
                transition: `transform 0.55s ${SMOOTH}, opacity 0.45s ease, filter 0.45s ease`,
                animation: `podRise 0.7s ${SMOOTH} ${0.25 + index * 0.07}s both`,
              }}
            >
              {/* avatar bay */}
              <div style={{ position: 'relative', width: 96, height: 96, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {isSel && <Rings color={color} />}
                {isConfirming && (
                  <div style={{
                    position: 'absolute', inset: '10%', borderRadius: '50%',
                    border: `2px solid ${color}`, boxShadow: `0 0 24px ${color}`,
                    animation: 'podFlare 0.4s ease-out forwards',
                  }} />
                )}
                <div style={{
                  transform: isSel ? 'scale(1.18)' : 'scale(1)',
                  transition: `transform 0.55s ${SMOOTH}`,
                  filter: isSel ? `drop-shadow(0 0 14px ${color}aa)` : 'none',
                }}>
                  <Avatar id={id} size={60} />
                </div>
              </div>

              {/* name + mark */}
              <div style={{ textAlign: 'center' }}>
                <div style={{
                  fontFamily: "'Rajdhani', sans-serif", fontWeight: 700,
                  fontSize: isSel ? '1.05rem' : '0.9rem', letterSpacing: '0.14em',
                  textTransform: 'uppercase', whiteSpace: 'nowrap',
                  color: isSel ? '#ffffff' : 'var(--hb-text-dim)',
                  textShadow: isSel ? `0 0 14px ${color}` : 'none',
                  transition: 'all 0.45s ease',
                }}>
                  {brand.name}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: '0.58rem', letterSpacing: '0.16em',
                  color: isSel ? color : 'var(--hb-text-faint)', marginTop: '0.25rem',
                  whiteSpace: 'nowrap', transition: 'color 0.45s ease',
                }}>
                  {brand.modelNumber}
                </div>
              </div>
            </button>
          )
        })}
      </div>

      {/* ── Designation spec panel — swaps as you move ────────────────────── */}
      <div
        onClick={e => e.stopPropagation()}
        className="hb-holo"
        style={{
          position: 'relative', overflow: 'hidden',
          minWidth: 340, maxWidth: '90vw', marginTop: '1.6rem',
          padding: '0.85rem 1.6rem', textAlign: 'center',
          borderTop: `1px solid ${selColor}44`, transition: 'border-color 0.6s ease',
          animation: `hintIn 0.7s ${SMOOTH} 0.6s both`,
        }}
      >
        {/* light sweep on every selection change */}
        <span key={`sweep-${selectedIndex}`} aria-hidden style={{
          position: 'absolute', top: 0, bottom: 0, width: '40%',
          background: `linear-gradient(90deg, transparent, ${selColor}33, transparent)`,
          animation: `sweep 0.7s ${SMOOTH}`,
        }} />
        <div key={`desig-${selectedIndex}`} style={{ animation: `desigIn 0.4s ${SMOOTH}` }}>
          <div style={{
            fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: '1.15rem',
            letterSpacing: '0.22em', textTransform: 'uppercase', color: '#eef7fa',
          }}>
            {selBrand?.name} <span style={{ color: selColor }}>· {selBrand?.modelNumber}</span>
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.62rem', letterSpacing: '0.14em',
            color: 'var(--hb-text-dim)', marginTop: '0.35rem', textTransform: 'uppercase',
          }}>
            {selBrand?.tagline}
          </div>
        </div>
      </div>

      {/* ── Hint bar ──────────────────────────────────────────────────────── */}
      <div className="hb-chip-amber" style={{
        marginTop: '1.4rem', padding: '0.55rem 1.2rem',
        animation: `hintIn 0.7s ${SMOOTH} 0.8s both`,
      }}>
        <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>&larr; &rarr;</span> NAVIGATE
        &middot; <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>ENTER</span> ENGAGE
        &middot; <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>ESC</span> CANCEL
      </div>
    </div>
  )
}
