/**
 * ChartBlock — the deck's chart renderer
 *
 * Triggered by ```chart code blocks in markdown.
 * SPEDA emits JSON matching ChartSpec. Rendered with Recharts on the deck's
 * glass plate: one card, a Condensed title with the unit as a right-hand
 * caption, a single hairline baseline, and columns lit from the cap down.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 *
 * Line / Area / Bar:
 * {
 *   "type": "line" | "area" | "bar",
 *   "title": "Average delay",         // optional; "TITLE_SUB" still splits
 *   "subtitle": "days · 3 carriers",  // optional right-hand caption
 *   "xKey": "month",                  // which data field is the X axis
 *   "series": [
 *     { "key": "revenue", "label": "REVENUE", "color": "var(--hb-cyan)" }
 *   ],
 *   "data": [
 *     { "month": "JAN", "revenue": 120 },
 *     ...
 *   ],
 *   "unit": "K",                      // appended to tooltip values
 *   "yDomain": [0, 500],              // optional Y axis range
 *   "height": 220                     // optional chart height px (default 210)
 * }
 *
 * Pie / Donut:
 * {
 *   "type": "pie",
 *   "title": "COMPLETION_STATUS",
 *   "data": [
 *     { "label": "BACKEND", "value": 40, "color": "var(--hb-cyan)" },
 *     ...
 *   ],
 *   "height": 220
 * }
 */

import { useMemo } from 'react'
import { looksIncomplete } from '../lib/partialJson'
import {
  LineChart, Line,
  BarChart, Bar,
  AreaChart, Area,
  PieChart, Pie, Cell,
  XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts'

/* ── Palette ──────────────────────────────────────────────────────────────── */
const PALETTE = ['var(--hb-cyan)', '#e8b96c', '#5cc98f', 'var(--hb-cyan-bright)', '#e5897c', '#b8a3e8', 'var(--hb-text-dim)']

/* The deck's chart is a plate, not a plotter readout: one hairline baseline
   under the data and nothing else. The old 10px monospace tick on a lit-accent
   axis drew a grid the eye had to look past to read the numbers. */
const GRID    = 'rgba(255,255,255,0.06)'
const AXIS_C  = 'rgba(255,255,255,0.10)'
const TICK    = {
  fontFamily: 'var(--font-read)',
  fontSize: 13,
  fill: 'var(--hb-text-faint)',
}

/* ── Shared axis props ────────────────────────────────────────────────────── */
const xAxisProps = {
  stroke: AXIS_C,
  tick: TICK,
  tickLine: false as const,
  axisLine: { stroke: AXIS_C },
  tickMargin: 8,
}
const yAxisProps = {
  stroke: AXIS_C,
  tick: TICK,
  tickLine: false as const,
  axisLine: false as const,
  width: 46,
}

/* ── Utility ──────────────────────────────────────────────────────────────── */
function resolveColor(s: Series, idx: number): string {
  return s.color ?? PALETTE[idx % PALETTE.length]
}

/* ── Custom Tooltip ───────────────────────────────────────────────────────── */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StarkTooltip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="hb-tile" style={{
      background: 'var(--glass-menu)',
      backdropFilter: 'var(--hb-holo-blur)',
      WebkitBackdropFilter: 'var(--hb-holo-blur)',
      boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.16), 0 12px 30px rgba(0,0,0,0.4)',
      border: '1px solid rgba(255,255,255,0.12)',
      padding: '8px 12px',
      fontFamily: 'var(--font-read)',
      fontSize: '0.8125rem',
      pointerEvents: 'none',
    }}>
      {label != null && (
        <div style={{ color: 'var(--hb-text-faint)', marginBottom: 3 }}>
          {String(label)}
        </div>
      )}
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {payload.map((p: any, i: number) => (
        <div key={i} style={{
          color: p.color ?? 'var(--hb-cyan-bright)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {p.name ? `${String(p.name)}  ` : ''}{p.value}{unit ?? ''}
        </div>
      ))}
    </div>
  )
}

/* ── Custom Legend ────────────────────────────────────────────────────────── */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StarkLegend({ payload }: any) {
  if (!payload?.length || payload.length < 2) return null
  return (
    <div style={{
      display: 'flex', gap: 18, justifyContent: 'center', flexWrap: 'wrap',
      paddingTop: 10,
      fontFamily: 'var(--font-read)',
      fontSize: '0.8125rem', color: 'var(--hb-text-dim)',
    }}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {payload.map((e: any, i: number) => (
        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span className="hb-round" style={{
            display: 'inline-block', width: 8, height: 8, background: e.color,
          }} />
          {String(e.value)}
        </span>
      ))}
    </div>
  )
}

/* ── Panel shell ──────────────────────────────────────────────────────────────
   The reference chart is a PLATE: one glass card, the title sitting inside its
   own padding with the unit as a quiet caption on the right, and the data
   directly beneath. The old accent header bar with 0.2em tracking belonged to
   the instrument-panel language and made every chart announce itself twice —
   once as a labelled rack unit, once as a chart.

   `TITLE_SUB` still splits, but the sub half now reads as that right-hand
   caption rather than accent text glued onto the title. A `subtitle` on the
   spec says the same thing without the underscore trick. */
function ChartPanel({ title, subtitle, children }: {
  title?: string
  subtitle?: string
  children: React.ReactNode
}) {
  const idx  = title ? title.indexOf('_') : -1
  const main = idx > -1 ? title!.slice(0, idx) : title
  const cap  = subtitle ?? (idx > -1 ? title!.slice(idx + 1).replace(/_/g, ' ') : '')

  return (
    <div className="hb-glass hb-widget" style={{
      position: 'relative',
      margin: '0.75rem 0',
      padding: '20px 22px 14px',
      overflow: 'hidden',
      animation: 'widgetEntrance 0.35s ease both',
    }}>
      {title && (
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          gap: 14, marginBottom: 16, userSelect: 'none',
        }}>
          <span style={{
            fontFamily: 'var(--font-ui)',
            fontSize: '1.125rem', fontWeight: 700,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            color: 'var(--hb-text)',
          }}>
            {main}
          </span>
          {cap && (
            <span style={{
              fontFamily: 'var(--font-read)', fontSize: '0.8125rem',
              color: 'var(--hb-text-faint)', textAlign: 'right',
            }}>
              {cap}
            </span>
          )}
        </div>
      )}

      {children}
    </div>
  )
}

/* ── Chart types ──────────────────────────────────────────────────────────── */

interface Series {
  key:    string
  label?: string
  color?: string
}

interface ChartSpec {
  type:     'line' | 'bar' | 'area' | 'pie'
  title?:   string
  /** Right-hand caption under the title — units, basis, sample size. */
  subtitle?: string
  xKey?:    string
  series?:  Series[]
  data:     Record<string, string | number>[]
  unit?:    string
  yDomain?: [number | 'auto', number | 'auto']
  height?:  number
}

/* Line ─────────────────────────────────────────────────────────────────────── */
function StarkLineChart({ spec }: { spec: ChartSpec }) {
  const { data, series = [], xKey = 'x', unit, yDomain, height = 210 } = spec
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tip = (p: any) => <StarkTooltip {...p} unit={unit} />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} {...xAxisProps} />
        <YAxis domain={yDomain ?? ['auto', 'auto']} {...yAxisProps} />
        <Tooltip content={tip} cursor={{ stroke: 'rgba(var(--hb-accent-rgb),0.18)', strokeWidth: 1 }} />
        <Legend content={StarkLegend} />
        {series.map((s, i) => {
          const c = resolveColor(s, i)
          return (
            <Line key={s.key} dataKey={s.key} name={s.label ?? s.key}
              stroke={c} strokeWidth={1.6}
              dot={{ r: 3, fill: c, stroke: 'none' }}
              activeDot={{ r: 5, fill: c, stroke: '#040c11', strokeWidth: 2 }}
              type="monotone" isAnimationActive={false}
            />
          )
        })}
      </LineChart>
    </ResponsiveContainer>
  )
}

/* Area ─────────────────────────────────────────────────────────────────────── */
function StarkAreaChart({ spec }: { spec: ChartSpec }) {
  const { data, series = [], xKey = 'x', unit, yDomain, height = 210 } = spec
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tip = (p: any) => <StarkTooltip {...p} unit={unit} />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 16, bottom: 0, left: 0 }}>
        <defs>
          {series.map((s, i) => {
            const c = resolveColor(s, i)
            return (
              <linearGradient key={s.key} id={`ag-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={c} stopOpacity={0.35} />
                <stop offset="95%" stopColor={c} stopOpacity={0.02} />
              </linearGradient>
            )
          })}
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} {...xAxisProps} />
        <YAxis domain={yDomain ?? ['auto', 'auto']} {...yAxisProps} />
        <Tooltip content={tip} cursor={{ stroke: 'rgba(var(--hb-accent-rgb),0.18)', strokeWidth: 1 }} />
        <Legend content={StarkLegend} />
        {series.map((s, i) => {
          const c = resolveColor(s, i)
          return (
            <Area key={s.key} dataKey={s.key} name={s.label ?? s.key}
              stroke={c} strokeWidth={1.6} fill={`url(#ag-${s.key})`}
              type="monotone" isAnimationActive={false}
              dot={{ r: 2.5, fill: c, stroke: 'none' }}
              activeDot={{ r: 5, fill: c, stroke: '#040c11', strokeWidth: 2 }}
            />
          )
        })}
      </AreaChart>
    </ResponsiveContainer>
  )
}

/* Bar ──────────────────────────────────────────────────────────────────────── */
function StarkBarChart({ spec }: { spec: ChartSpec }) {
  const { data, series = [], xKey = 'x', unit, yDomain, height = 210 } = spec
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tip = (p: any) => <StarkTooltip {...p} unit={unit} />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 6, right: 16, bottom: 0, left: 0 }} barCategoryGap="30%">
        {/* A column in the reference deck is a lit slab: bright at the cap,
            fading toward the baseline. A flat 52% fill with a 1px stroke read
            as a bar CHART; this reads as the data standing up. */}
        <defs>
          {series.map((s, i) => {
            const c = resolveColor(s, i)
            return (
              <linearGradient key={s.key} id={`bg-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor={c} stopOpacity={0.85} />
                <stop offset="100%" stopColor={c} stopOpacity={0.18} />
              </linearGradient>
            )
          })}
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} {...xAxisProps} />
        <YAxis domain={yDomain ?? [0, 'auto']} {...yAxisProps} />
        <Tooltip content={tip} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
        <Legend content={StarkLegend} />
        {series.map(s => (
          <Bar key={s.key} dataKey={s.key} name={s.label ?? s.key}
            fill={`url(#bg-${s.key})`}
            radius={[10, 10, 0, 0]}
            maxBarSize={64}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

/* Pie / Donut ──────────────────────────────────────────────────────────────── */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PieLabel({ cx, cy, midAngle, outerRadius, name, percent }: any) {
  const RAD    = Math.PI / 180
  const radius = outerRadius + 22
  const x      = cx + radius * Math.cos(-midAngle * RAD)
  const y      = cy + radius * Math.sin(-midAngle * RAD)
  return (
    <text
      x={x} y={y}
      textAnchor={x > cx ? 'start' : 'end'}
      dominantBaseline="central"
      style={{ fontFamily: 'var(--font-read)', fontSize: 13, fill: 'var(--hb-text-dim)' }}
    >
      {`${String(name)} ${(percent * 100).toFixed(0)}%`}
    </text>
  )
}

function StarkPieChart({ spec }: { spec: ChartSpec }) {
  const { data, height = 230 } = spec
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tip = (p: any) => <StarkTooltip {...p} />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={data} dataKey="value" nameKey="label"
          cx="50%" cy="50%" outerRadius={75} innerRadius={38}
          strokeWidth={0} isAnimationActive={false}
          labelLine={{ stroke: 'rgba(var(--hb-accent-rgb),0.28)', strokeWidth: 1 }}
          label={PieLabel}
        >
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {data.map((entry: any, i: number) => (
            <Cell key={i}
              fill={(entry.color ?? PALETTE[i % PALETTE.length])}
              opacity={0.82}
            />
          ))}
        </Pie>
        <Tooltip content={tip} />
      </PieChart>
    </ResponsiveContainer>
  )
}

/* ── Parse error block ────────────────────────────────────────────────────── */
function ParseError({ raw }: { raw: string }) {
  return (
    <div className="hb-tile" style={{
      padding: '10px 14px',
      background: 'rgba(216,72,60,0.09)',
      border: '1px solid rgba(216,72,60,0.32)',
      fontFamily: 'var(--font-read)',
      fontSize: '0.875rem', color: '#e5897c',
      margin: '0.5rem 0',
    }}>
      Chart — could not read the spec
      <div style={{ color: 'var(--hb-text-faint)', fontSize: '0.8125rem', marginTop: 3 }}>
        {raw.slice(0, 120)}
      </div>
    </div>
  )
}

/* ── Materializing — quiet placeholder while the JSON is still streaming ──── */
function Materializing() {
  return (
    <ChartPanel>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '2px 0' }}>
        {/* The same ring every other pending thing in the deck uses. */}
        <span className="hb-round" style={{
          width: 14, height: 14, flexShrink: 0,
          border: '1.5px solid rgba(var(--hb-accent-rgb),0.3)',
          borderTopColor: 'var(--hb-cyan)',
          animation: 'spin 0.7s linear infinite',
        }} />
        <span style={{
          fontFamily: 'var(--font-read)', fontSize: '0.875rem',
          color: 'var(--hb-text-faint)',
        }}>
          Drawing the chart…
        </span>
      </div>
    </ChartPanel>
  )
}

/* ── Main export ──────────────────────────────────────────────────────────── */
export default function ChartBlock({ children }: { children: string }) {
  const spec = useMemo<ChartSpec | null>(() => {
    try   { return JSON.parse(children) as ChartSpec }
    catch { return null }
  }, [children])

  // Unbalanced JSON means it's still streaming, not actually malformed — a
  // quiet placeholder beats a scary error that vanishes a second later.
  if (!spec) return looksIncomplete(children) ? <Materializing /> : <ParseError raw={children} />

  return (
    <ChartPanel title={spec.title} subtitle={spec.subtitle}>
      {spec.type === 'line' && <StarkLineChart spec={spec} />}
      {spec.type === 'area' && <StarkAreaChart spec={spec} />}
      {spec.type === 'bar'  && <StarkBarChart  spec={spec} />}
      {spec.type === 'pie'  && <StarkPieChart  spec={spec} />}
      {!['line','area','bar','pie'].includes(spec.type) && (
        <ParseError raw={`unknown type: ${spec.type}`} />
      )}
    </ChartPanel>
  )
}
