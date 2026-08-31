// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * CalendarBlock — Jarvis holographic calendar renderer
 *
 * Triggered by ```calendar code blocks in markdown. The agent emits JSON
 * matching CalendarSpec (a week or agenda of events) and it renders as a
 * single fluid-glass card in the .hb-holo material — a week strip of day
 * columns, today's column lit, each event a small accent card.
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

/* ── One event chip ───────────────────────────────────────────────────────── */
function EventChip({ ev }: { ev: CalEvent }) {
  const accent = ev.color ?? 'var(--hb-cyan)'
  return (
    // An event is a small accent card, per the deck — not a 2px stripe against
    // a wash. Title first at reading size, time under it in the accent.
    <div className="hb-glass-xs" style={{
      position: 'relative',
      padding: '10px 12px',
      marginBottom: 6,
      background: 'rgba(var(--hb-accent-rgb),0.08)',
      border: `1px solid ${ev.color ? `${accent}38` : 'rgba(var(--hb-accent-rgb),0.22)'}`,
      boxShadow: 'none',
      overflow: 'hidden',
    }}>
      <div style={{
        fontSize: '0.875rem', fontWeight: 500,
        lineHeight: 1.35, color: 'var(--hb-text)',
      }}>
        {ev.title}
      </div>
      {ev.time && (
        <div style={{ fontSize: '0.78rem', color: accent, marginTop: 3 }}>
          {ev.time}{ev.end ? `–${ev.end}` : ''}
        </div>
      )}
      {ev.location && (
        <div style={{
          fontSize: '0.78rem',
          color: 'var(--hb-text-faint)', marginTop: 2,
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
    <div className={isToday ? 'hb-glass-sm' : undefined} style={{
      flex: '1 1 0', minWidth: 0,
      padding: '10px 8px 12px',
      background: isToday ? 'rgba(var(--hb-accent-rgb),0.10)' : 'transparent',
      border: isToday ? '1px solid rgba(var(--hb-accent-rgb),0.4)' : '1px solid transparent',
      boxShadow: 'none',
    }}>
      {/* day header */}
      <div style={{ textAlign: 'center', marginBottom: 10 }}>
        <div style={{
          fontSize: '0.78rem', letterSpacing: '0.14em',
          color: isToday ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
        }}>
          {wd}
        </div>
        {/* Today is stated by the lit cell it sits in, not by doubling its
            point size — the old 2.1rem/1.25rem jump made the week strip lurch. */}
        <div style={{
          fontFamily: "'Rajdhani', sans-serif", fontWeight: isToday ? 600 : 400,
          fontSize: '1.35rem', lineHeight: 1,
          marginTop: 3,
          color: isToday ? 'var(--hb-text)' : 'var(--hb-text-dim)',
        }}>
          {num}
        </div>
      </div>

      {/* events */}
      {events.length > 0
        ? events.map((ev, i) => <EventChip key={i} ev={ev} />)
        : (
          <div style={{
            textAlign: 'center', color: 'var(--hb-text-faint)', opacity: 0.35,
            fontSize: '0.875rem', marginTop: 4,
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
      fontSize: '0.875rem', color: '#e88a7c',
      margin: '0.5rem 0',
    }}>
      Calendar — could not parse<br />
      <span style={{ color: 'var(--hb-text-faint)', fontSize: '0.8125rem' }}>{raw.slice(0, 120)}</span>
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
        fontSize: '0.875rem', color: 'var(--hb-text-faint)',
      }}>
        Building the calendar…
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
    <div className="hb-widget" style={{ position: 'relative', margin: '0.85rem 0', animation: 'widgetEntrance 0.4s ease both' }}>
      {/* The two offset "glass ghost" plates and the dashed HudRing reticle that
          used to sit behind/over this panel are gone. They were movie-prop
          decoration in the same family as the banned corner brackets, and the
          reference deck draws a calendar as one clean card. */}
      <div className="hb-holo" style={{ position: 'relative', overflow: 'hidden', padding: '22px 24px' }}>

        {/* header */}
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          marginBottom: 16, gap: 12,
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontFamily: "'Rajdhani', sans-serif", fontWeight: 600, fontSize: '1.06rem',
              letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--hb-text)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {title}
            </div>
            {spec.range && (
              <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 3 }}>
                {spec.range}
              </div>
            )}
          </div>
          {monthLabel && (
            <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-dim)', whiteSpace: 'nowrap' }}>
              {monthLabel}
            </div>
          )}
        </div>

        {/* day columns */}
        <div style={{ display: 'flex', gap: 5, overflowX: 'auto' }}>
          {spec.days.map((day, i) => (
            <DayColumn key={i} day={day} today={today} />
          ))}
        </div>
      </div>
    </div>
  )
}
