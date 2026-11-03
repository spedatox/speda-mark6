// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from 'react'
import { looksIncomplete } from '../lib/partialJson'

interface CalEvent { time?: string; end?: string; title: string; location?: string; color?: string }
interface CalDay { date: string; label?: string; events?: CalEvent[] }
interface CalendarSpec { title?: string; range?: string; days: CalDay[] }
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function parseDate(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!m) return null
  const d = new Date(+m[1], +m[2] - 1, +m[3])
  return d.getFullYear() === +m[1] && d.getMonth() === +m[2] - 1 && d.getDate() === +m[3] ? d : null
}
function isSpec(value: unknown): value is CalendarSpec {
  if (!value || typeof value !== 'object') return false
  const s = value as CalendarSpec
  return (s.title === undefined || typeof s.title === 'string')
    && (s.range === undefined || typeof s.range === 'string') && Array.isArray(s.days)
    && s.days.every(d => d && typeof d.date === 'string' && parseDate(d.date)
      && (d.label === undefined || typeof d.label === 'string')
      && (d.events === undefined || (Array.isArray(d.events) && d.events.every(e => e && typeof e.title === 'string'
        && ['time', 'end', 'location', 'color'].every(key => e[key] === undefined || typeof e[key] === 'string')))))
}

function Event({ event }: { event: CalEvent }) {
  const color = /^#[\da-f]{6}$/i.test(event.color ?? '') ? event.color : 'var(--hb-cyan)'
  return <div className="hb-calendar-event" style={{ '--event-color': color } as React.CSSProperties}>
    <div className="hb-calendar-time">{event.time || 'All day'}{event.end && <span>–{event.end}</span>}</div>
    <div className="hb-calendar-detail"><div className="hb-calendar-event-title">{event.title}</div>
      {event.location && <div className="hb-calendar-location">{event.location}</div>}
    </div>
  </div>
}

export default function CalendarBlock({ children }: { children: string }) {
  const [view, setView] = useState<'agenda' | 'week'>('agenda')
  const spec = useMemo(() => {
    try { const value: unknown = JSON.parse(children); return isSpec(value) ? value : null } catch { return null }
  }, [children])
  const today = new Date()
  if (!spec) return <div className="hb-rich-pending" role="status">{looksIncomplete(children)
    ? 'Preparing calendar…' : 'This calendar could not be displayed. Check its dates and event fields.'}</div>
  const count = spec.days.reduce((n, d) => n + (d.events?.length ?? 0), 0)
  const first = spec.days.length ? parseDate(spec.days[0].date) : null
  const month = first ? `${MONTHS[first.getMonth()]} ${first.getFullYear()}` : ''
  return <section className="hb-rich-block hb-calendar" aria-label={spec.title || 'Calendar'} data-view={view}>
    <header className="hb-calendar-header">
      <div><div className="hb-rich-title">{spec.title || 'Your schedule'}</div>
        <div className="hb-calendar-subtitle">{spec.range || month}{(spec.range || month) && ' · '}{count} {count === 1 ? 'event' : 'events'}</div>
      </div>
      {spec.days.length > 1 && <div className="hb-view-toggle" role="group" aria-label="Calendar view">
        <button aria-pressed={view === 'agenda'} onClick={() => setView('agenda')}>Agenda</button>
        <button aria-pressed={view === 'week'} onClick={() => setView('week')}>Week</button>
      </div>}
    </header>
    <div className="hb-calendar-scroll" tabIndex={view === 'week' ? 0 : undefined} role="region" aria-label={`${view} events`}>
      <div className="hb-calendar-days">
        {spec.days.map((day, i) => {
          const date = parseDate(day.date)!
          const isToday = date.toDateString() === today.toDateString()
          const events = [...(day.events ?? [])].sort((a, b) => (a.time ?? '').localeCompare(b.time ?? ''))
          return <div className="hb-calendar-day" key={`${day.date}-${i}`} data-today={isToday}>
            <div className="hb-calendar-date"><span>{day.label || WEEKDAYS[date.getDay()]}</span><time dateTime={day.date} aria-current={isToday ? 'date' : undefined}>{date.getDate()}</time>{isToday && <small>Today</small>}</div>
            <div className="hb-calendar-events">{events.length ? events.map((e, j) => <Event key={j} event={e} />) : <div className="hb-calendar-empty">No events</div>}</div>
          </div>
        })}
        {!spec.days.length && <div className="hb-calendar-empty">No days to display</div>}
      </div>
    </div>
  </section>
}
