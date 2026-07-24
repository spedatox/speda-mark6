/**
 * AgentMark — the agent wordmarks, rendered in the fluid-glass material.
 *
 * Geometry lives in ../lib/agentMarks (traced from `logos/*.png`); this file
 * only decides how it is lit. Three finishes:
 *
 *   flat   — solid `currentColor`. Use inside buttons and dense lists where the
 *            surrounding text colour should carry the mark.
 *   glass  — the .hb-holo treatment: accent body, sheen down the top-left,
 *            specular bloom, lit rim, accent glow. Use at 24px and up.
 *   etched — hairline outline only. Use on top of an already-lit surface.
 *
 * Agents with no art (orion, warroom) fall back to a monogram in the same box,
 * so callers never have to branch on which marks exist.
 */
import { useId } from 'react'
import { AGENT_MARKS } from '../lib/agentMarks'
import { agentColor, monogram } from '../lib/agents'

type Finish = 'flat' | 'glass' | 'etched'

interface Props {
  /** Backend agent id — 'speda', 'sentinel', … */
  agentId: string
  /** Rendered box in px. Square. */
  size?: number
  finish?: Finish
  /** Overrides the agent's signature colour. Ignored by `flat`. */
  color?: string
  /** Decorative marks (next to a visible name) should stay out of the a11y tree. */
  title?: string
  className?: string
  style?: React.CSSProperties
}

export default function AgentMark({
  agentId, size = 24, finish = 'glass', color, title, className, style,
}: Props) {
  const uid = useId().replace(/[:]/g, '')
  const d = AGENT_MARKS[agentId]
  const accent = color ?? agentColor(agentId)
  const a11y = title
    ? { role: 'img' as const, 'aria-label': title }
    : { 'aria-hidden': true as const, focusable: 'false' as const }

  // No art for this agent — keep the same footprint and draw the monogram.
  if (!d) {
    return (
      <svg viewBox="0 0 100 100" width={size} height={size}
           className={className} style={style} {...a11y}>
        {title && <title>{title}</title>}
        <text x="50" y="50" textAnchor="middle" dominantBaseline="central"
              fontSize="44" fontWeight={600} letterSpacing="-1"
              fill={finish === 'flat' ? 'currentColor' : accent}
              fillOpacity={finish === 'etched' ? 0.75 : 0.92}>
          {monogram(agentId)}
        </text>
      </svg>
    )
  }

  if (finish === 'flat') {
    return (
      <svg viewBox="0 0 100 100" width={size} height={size} fill="currentColor"
           className={className} style={style} {...a11y}>
        {title && <title>{title}</title>}
        <path d={d} />
      </svg>
    )
  }

  if (finish === 'etched') {
    return (
      <svg viewBox="0 0 100 100" width={size} height={size} fill="none"
           className={className} style={style} {...a11y}>
        {title && <title>{title}</title>}
        <path d={d} stroke={accent} strokeOpacity={0.85} strokeWidth={1.4}
              strokeLinejoin="round" />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 100 100" width={size} height={size} fill="none"
         className={className} style={style} {...a11y}>
      {title && <title>{title}</title>}
      <defs>
        <clipPath id={`${uid}-clip`}><path d={d} /></clipPath>
        <linearGradient id={`${uid}-sheen`} x1="0.08" y1="0" x2="0.62" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity={0.62} />
          <stop offset="0.42" stopColor="#fff" stopOpacity={0.1} />
          <stop offset="1" stopColor="#fff" stopOpacity={0} />
        </linearGradient>
        <radialGradient id={`${uid}-bloom`} cx="0.28" cy="0.2" r="0.8">
          <stop offset="0" stopColor="#fff" stopOpacity={0.42} />
          <stop offset="1" stopColor="#fff" stopOpacity={0} />
        </radialGradient>
        <filter id={`${uid}-lift`} x="-35%" y="-35%" width="170%" height="170%">
          <feDropShadow dx="0" dy="1.6" stdDeviation="2.4"
                        floodColor="#000" floodOpacity={0.36} />
          <feDropShadow dx="0" dy="0" stdDeviation="4"
                        floodColor={accent} floodOpacity={0.45} />
        </filter>
      </defs>
      <g filter={`url(#${uid}-lift)`}>
        <path d={d} fill={accent} fillOpacity={0.92} />
        <g clipPath={`url(#${uid}-clip)`}>
          <rect width="100" height="100" fill={`url(#${uid}-sheen)`} />
          <rect width="100" height="100" fill={`url(#${uid}-bloom)`} />
        </g>
        <path d={d} fill="none" stroke="#fff" strokeOpacity={0.38}
              strokeWidth={0.9} strokeLinejoin="round" />
      </g>
    </svg>
  )
}
