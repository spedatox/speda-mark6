/**
 * CalendarBlock — Jarvis holographic calendar renderer
 *
 * Triggered by ```calendar code blocks in markdown. The agent emits JSON
 * matching CalendarSpec (a week or agenda of events) and it renders as a
 * layered fluid-glass hologram in the .hb-holo material — frosted panel,
 * concentric HUD ring, today's date as a large glowing numeral.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 * {
 *   "title": "THIS WEEK",                  // optional panel title
 *   "range": "30 JUN – 6 JUL 2026",        // optional subtitle
 *   "days": [
 *     {
 *       "date": "2026-06-30",              // ISO yyyy-mm-dd (required)
 *       "events": [
 *         { "time": "09:00", "end": "10:00", "title": "Standup",
 *           "location": "Zoom", "color": "#36abca" },
 *         { "title": "Dentist" }           // all-day if no time
 *       ]
 *     }
 *   ]
 * }
 *
 * Colours come from the active agent's accent via CSS vars, so the widget
 * themes itself per agent (Optimus teal, Atomix green, …).
 */

import { useMemo } from 'react'
import { looksIncomplete } from '../lib/partialJson'

/* ── Types ────────────────────────────────────────────────────────────────── */
interface CalEvent {
  time?: string
  end?: string
  title: string
  location?: string
  color?: string
}
interface CalDay {
  date: string
  label?: string
  events?: CalEvent[]
}
interface CalendarSpec {
  title?: string
  range?: string
  days: CalDay[]
}

const WEEKDAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/* ── Date helpers (timezone-safe: parse yyyy-mm-dd as local) ──────────────── */
function parseLocalDate(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s.trim())
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}
function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/* ── Background HUD ring — faint concentric arcs, like the Jarvis interface ── */
function HudRing() {
  return (
    <svg
      viewBox="0 0 200 200"
      style={{
        position: 'absolute', right: -34, top: '50%', transform: 'translateY(-50%)',
        width: 260, height: 260, opacity: 0.5, pointerEvents: 'none',
        color: 'var(--hb-cyan)',
      }}
    >
      <circle cx="100" cy="100" r="92" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.18" />
      <circle cx="100" cy="100" r="72" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.30"
        strokeDasharray="2 5" />
      <circle cx="100" cy="100" r="50" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.16" />
      <path d="M100 8 A92 92 0 0 1 192 100" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.5" />
      <path d="M100 192 A92 92 0 0 1 8 100" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.3" />
    </svg>
  )
}

/* ── One event chip ───────────────────────────────────────────────────────── */
function EventChip({ ev }: { ev: CalEvent }) {
  const accent = ev.color ?? 'var(--hb-cyan)'
  return (
    <div style={{
      position: 'relative',
      padding: '0.3rem 0.4rem 0.32rem 0.55rem',
      marginBottom: '0.3rem',
      background: 'rgba(var(--hb-accent-rgb),0.07)',
      borderRadius: 6,
      borderLeft: `2px solid ${accent}`,
      overflow: 'hidden',
    }}>
      {ev.time && (
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: '0.6rem',
          letterSpacing: '0.06em', color: 'var(--hb-cyan-bright)',
        }}>
          {ev.time}{ev.end ? `–${ev.end}` : ''}
        </div>
      )}
      <div style={{
        fontFamily: "'Rajdhani', sans-serif", fontSize: '0.74rem', fontWeight: 600,
        lineHeight: 1.2, color: 'var(--hb-text)', letterSpacing: '0.01em',
      }}>
        {ev.title}
      </div>
      {ev.location && (
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: '0.55rem',
          color: 'var(--hb-text-faint)', letterSpacing: '0.04em', marginTop: 1,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {ev.location}
        </div>
      )}
    </div>
  )
}

/* ── One day column ───────────────────────────────────────────────────────── */
function DayColumn({ day, today }: { day: CalDay; today: Date }) {
  const d = parseLocalDate(day.date)
  const isToday = d ? isSameDay(d, today) : false
  const wd = day.label ?? (d ? WEEKDAYS[d.getDay()] : '—')
  const num = d ? d.getDate() : ''
  const events = (day.events ?? []).slice().sort((a, b) => (a.time ?? '').localeCompare(b.time ?? ''))

  return (
    <div style={{
      flex: '1 1 0', minWidth: 0,
      padding: '0.55rem 0.4rem 0.6rem',
      borderRadius: 10,
      background: isToday ? 'rgba(var(--hb-accent-rgb),0.10)' : 'transparent',
      border: isToday ? '1px solid var(--hb-edge-bright)' : '1px solid transparent',
      boxShadow: isToday ? '0 0 18px rgba(var(--hb-accent-rgb),0.18) inset' : 'none',
    }}>
      {/* day header */}
      <div style={{ textAlign: 'center', marginBottom: '0.45rem' }}>
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: '0.56rem',
          letterSpacing: '0.18em',
          color: isToday ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
        }}>
          {wd}
        </div>
        <div style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: 300,
          fontSize: isToday ? '2.1rem' : '1.25rem', lineHeight: 1,
          marginTop: 2,
          color: isToday ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
          textShadow: isToday ? '0 0 14px rgba(var(--hb-cyan-bright-rgb),0.6)' : 'none',
        }}>
          {num}
        </div>
      </div>

      {/* events */}
      {events.length > 0
        ? events.map((ev, i) => <EventChip key={i} ev={ev} />)
        : (
          <div style={{
            textAlign: 'center', color: 'var(--hb-text-faint)', opacity: 0.4,
            fontFamily: "var(--font-mono)", fontSize: '0.7rem', marginTop: '0.3rem',
          }}>
            ·
          </div>
        )}
    </div>
  )
}

/* ── Parse error fallback ─────────────────────────────────────────────────── */
function ParseError({ raw }: { raw: string }) {
  return (
    <div style={{
      padding: '0.5rem 0.75rem',
      background: 'rgba(200,74,58,0.09)',
      border: '1px solid rgba(200,74,58,0.35)',
      borderRadius: 8,
      fontFamily: "var(--font-mono)",
      fontSize: '0.71rem', color: '#c84a3a',
      margin: '0.5rem 0', letterSpacing: '0.05em',
    }}>
      CALENDAR // PARSE ERROR<br />
      <span style={{ color: 'var(--hb-text-faint)', fontSize: '0.65rem' }}>{raw.slice(0, 120)}</span>
    </div>
  )
}

/* ── Materializing — quiet placeholder while the JSON is still streaming ──── */
function Materializing() {
  return (
    <div className="hb-holo" style={{
      position: 'relative', overflow: 'hidden', margin: '0.85rem 0',
      padding: '1.1rem 0.95rem', display: 'flex', alignItems: 'center',
      gap: '0.55rem', animation: 'widgetEntrance 0.3s ease both',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
        background: 'var(--hb-cyan-bright)', animation: 'skeletonPulse 1.4s ease-in-out infinite',
      }} />
      <span style={{
        fontFamily: "var(--font-mono)", fontSize: '0.68rem',
        letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--hb-text-faint)',
      }}>
        CALENDAR // MATERIALIZING
      </span>
    </div>
  )
}

/* ── Main export ──────────────────────────────────────────────────────────── */
export default function CalendarBlock({ children }: { children: string }) {
  const spec = useMemo<CalendarSpec | null>(() => {
    try {
      const s = JSON.parse(children) as CalendarSpec
      return Array.isArray(s.days) ? s : null
    } catch {
      return null
    }
  }, [children])

  const today = useMemo(() => new Date(), [])

  // Unbalanced JSON means it's still streaming, not actually malformed — a
  // quiet placeholder beats a scary error that vanishes a second later.
  if (!spec) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />

  const title = spec.title ?? 'CALENDAR'
  // Header focus cluster: month of the first day (Jarvis "JANUARY 2012" feel).
  const first = spec.days.length ? parseLocalDate(spec.days[0].date) : null
  const monthLabel = first ? `${MONTHS[first.getMonth()]} ${first.getFullYear()}` : ''

  return (
    <div style={{ position: 'relative', margin: '0.85rem 0', animation: 'widgetEntrance 0.4s ease both' }}>
      {/* layered glass ghosts behind — stacked-depth like the reference */}
      <div style={{
        position: 'absolute', inset: '8px -10px -10px 8px', borderRadius: 16,
        border: '1px solid rgba(var(--hb-accent-rgb),0.10)',
        background: 'rgba(190,215,235,0.018)', pointerEvents: 'none',
      }} />
      <div style={{
        position: 'absolute', inset: '4px -5px -5px 4px', borderRadius: 16,
        border: '1px solid rgba(var(--hb-accent-rgb),0.14)',
        background: 'rgba(190,215,235,0.025)', pointerEvents: 'none',
      }} />

      {/* main holographic panel */}
      <div className="hb-holo" style={{ position: 'relative', overflow: 'hidden', padding: '0.85rem 0.95rem 0.95rem' }}>
        <HudRing />

        {/* header */}
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          marginBottom: '0.7rem', position: 'relative', zIndex: 1, gap: '0.75rem',
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: '0.92rem',
              letterSpacing: '0.22em', textTransform: 'uppercase', color: '#ffffff',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {title}
            </div>
            {spec.range && (
              <div style={{
                fontFamily: "var(--font-mono)", fontSize: '0.62rem',
                letterSpacing: '0.1em', color: 'var(--hb-text-faint)', marginTop: 2,
              }}>
                {spec.range}
              </div>
            )}
          </div>
          {monthLabel && (
            <div style={{
              fontFamily: "var(--font-mono)", fontSize: '0.62rem',
              letterSpacing: '0.14em', color: 'var(--hb-cyan)', whiteSpace: 'nowrap',
            }}>
              {monthLabel}
            </div>
          )}
        </div>

        {/* hairline divider */}
        <div style={{
          height: 1, marginBottom: '0.6rem', position: 'relative', zIndex: 1,
          background: 'linear-gradient(90deg, var(--hb-edge-bright), transparent)',
        }} />

        {/* day columns */}
        <div style={{ display: 'flex', gap: '0.3rem', position: 'relative', zIndex: 1, overflowX: 'auto' }}>
          {spec.days.map((day, i) => (
            <DayColumn key={i} day={day} today={today} />
          ))}
        </div>
      </div>
    </div>
  )
}
