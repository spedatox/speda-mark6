import { useEffect, useState, useRef } from 'react'
import { ROSTER, agentColor } from '../lib/agents'
import { BRANDS } from '../profile/brands'
import { Avatar } from './CommBubble'

const KEYFRAMES = `
@keyframes switcherBg {
  from { opacity: 0; filter: blur(14px); }
  to { opacity: 1; filter: blur(0); }
}
@keyframes switcherContainer {
  0%   { opacity: 0; transform: scale(1.03) translateY(8px); filter: blur(10px); }
  100% { opacity: 1; transform: scale(1); filter: blur(0); }
}
@keyframes switcherCard {
  0%   { opacity: 0; transform: scale(0.85) translateY(16px); filter: blur(6px); }
  100% { opacity: 1; transform: scale(1); filter: blur(0); }
}
`

export default function AgentSwitcherOverlay({
  onSelect,
  onClose,
  currentAgentId
}: {
  onSelect: (agentId: string) => void
  onClose: () => void
  currentAgentId: string
}) {
  const [selectedIndex, setSelectedIndex] = useState(() => {
    const idx = ROSTER.indexOf(currentAgentId)
    return Math.max(0, idx)
  })

  // Capture focus so keyboard events go here
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    containerRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        setSelectedIndex(i => (i + 1) % ROSTER.length)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        setSelectedIndex(i => (i - 1 + ROSTER.length) % ROSTER.length)
      } else if (e.key === 'Enter') {
        e.preventDefault()
        onSelect(ROSTER[selectedIndex])
      }
    }
    // Use capture phase to intercept before anything else handles it
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [selectedIndex, onClose, onSelect])

  // A very smooth easing curve for a premium feel
  const SMOOTH_EASE = 'cubic-bezier(0.16, 1, 0.3, 1)'

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(3, 7, 10, 0.55)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        animation: `switcherBg 0.9s ${SMOOTH_EASE} both`,
      }}
      onClick={onClose}
    >
      <style>{KEYFRAMES}</style>
      <div
        ref={containerRef}
        className="hb-glass hb-seam-b"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{
          display: 'flex',
          gap: '1.2rem',
          padding: '2rem 2.5rem',
          outline: 'none',
          maxWidth: '90vw',
          overflowX: 'auto',
          alignItems: 'center',
          animation: `switcherContainer 1.2s ${SMOOTH_EASE} both`,
        }}
      >
        {ROSTER.map((id, index) => {
          const brand = BRANDS[id]
          if (!brand) return null
          
          const isSelected = index === selectedIndex
          const color = agentColor(id)

          return (
            <div
              key={id}
              className={`hb-glass glass-interactive ${isSelected ? 'glass-tint glass-active' : ''}`}
              onClick={() => onSelect(id)}
              style={{
                color: color, // The glass-tint class reads currentColor
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '1.2rem',
                width: '130px',
                height: '160px',
                padding: '1rem',
                cursor: 'pointer',
                transition: 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
                transform: isSelected ? 'scale(1.06) translateY(-4px)' : 'scale(1)',
                animation: `switcherCard 1.2s ${SMOOTH_EASE} ${0.2 + index * 0.1}s both`,
              }}
              onMouseEnter={() => setSelectedIndex(index)}
            >
              <div
                className="hb-round"
                style={{
                  color,
                  display: 'flex',
                  opacity: isSelected ? 1 : 0.5,
                  transition: 'opacity 0.5s ease',
                  transform: isSelected ? 'scale(1.1)' : 'scale(1)',
                }}
              >
                <Avatar id={id} size={64} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{
                  fontFamily: "'Rajdhani', sans-serif",
                  fontWeight: 700,
                  fontSize: '1rem',
                  letterSpacing: '0.12em',
                  textTransform: 'uppercase',
                  color: isSelected ? '#ffffff' : 'var(--hb-text-dim)',
                  textShadow: isSelected ? `0 0 12px ${color}` : 'none',
                  transition: 'all 0.5s ease',
                  whiteSpace: 'nowrap',
                }}>
                  {brand.name}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.6rem',
                  letterSpacing: '0.1em',
                  color: isSelected ? color : 'var(--hb-text-dim)',
                  opacity: 0.8,
                  marginTop: '0.3rem',
                  whiteSpace: 'nowrap',
                  transition: 'all 0.5s ease',
                }}>
                  {brand.modelNumber}
                </div>
              </div>
            </div>
          )
        })}
      </div>
      <div 
        className="hb-chip-amber"
        style={{
          marginTop: '2.5rem',
          padding: '0.6rem 1.2rem',
          opacity: 0.8,
          animation: `switcherContainer 1.2s ${SMOOTH_EASE} 0.5s both`,
        }}
      >
        USE <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>&larr; &rarr;</span> TO NAVIGATE &middot; <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>ENTER</span> TO SELECT &middot; <span style={{ color: 'var(--hb-amber-bright)', padding: '0 0.3rem' }}>ESC</span> TO CANCEL
      </div>
    </div>
  )
}
