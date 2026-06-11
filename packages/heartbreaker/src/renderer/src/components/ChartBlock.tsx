/**
 * ChartBlock — Stark FUI chart renderer
 *
 * Triggered by ```chart code blocks in markdown.
 * SPEDA emits JSON matching ChartSpec. Rendered with Recharts, styled to the
 * Iron Man 2 / Jayse Hansen holographic UI palette.
 *
 * ── Spec format ────────────────────────────────────────────────────────────
 *
 * Line / Area / Bar:
 * {
 *   "type": "line" | "area" | "bar",
 *   "title": "PANEL_TITLE",           // optional, underscore splits style
 *   "xKey": "month",                  // which data field is the X axis
 *   "series": [
 *     { "key": "revenue", "label": "REVENUE", "color": "#36abca" }
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
 *     { "label": "BACKEND", "value": 40, "color": "#36abca" },
 *     ...
 *   ],
 *   "height": 220
 * }
 */

import { useMemo } from 'react'
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
const PALETTE = ['#36abca', '#d39a3a', '#4fa377', '#5fcce6', '#c84a3a', '#9b72cf', '#7aa4b5']
const GRID    = 'rgba(95,165,188,0.10)'
const AXIS_C  = 'rgba(95,165,188,0.30)'
const TICK    = {
  fontFamily: "'Share Tech Mono', monospace",
  fontSize: 10,
  fill: '#46626d',
}

/* ── Shared axis props ────────────────────────────────────────────────────── */
const xAxisProps = {
  stroke: AXIS_C,
  tick: TICK,
  tickLine: { stroke: AXIS_C },
  axisLine: { stroke: AXIS_C },
}
const yAxisProps = {
  stroke: AXIS_C,
  tick: TICK,
  tickLine: false as const,
  axisLine: false as const,
  width: 44,
}

/* ── Utility ──────────────────────────────────────────────────────────────── */
function hexToRgb(hex: string): string {
  const c = hex.replace('#', '')
  const r = parseInt(c.slice(0, 2), 16)
  const g = parseInt(c.slice(2, 4), 16)
  const b = parseInt(c.slice(4, 6), 16)
  return `${r},${g},${b}`
}

function resolveColor(s: Series, idx: number): string {
  return s.color ?? PALETTE[idx % PALETTE.length]
}

/* ── Custom Tooltip ───────────────────────────────────────────────────────── */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StarkTooltip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: 'rgba(3,8,12,0.97)',
      border: '1px solid rgba(95,165,188,0.5)',
      padding: '0.35rem 0.6rem',
      fontFamily: "'Share Tech Mono', monospace",
      fontSize: '0.69rem',
      letterSpacing: '0.05em',
      pointerEvents: 'none',
    }}>
      {label != null && (
        <div style={{ color: '#46626d', marginBottom: '0.2rem', textTransform: 'uppercase' }}>
          {String(label)}
        </div>
      )}
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color ?? '#5fcce6' }}>
          {p.name ? `${String(p.name).toUpperCase()}  ` : ''}{p.value}{unit ?? ''}
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
      display: 'flex', gap: '1.1rem', justifyContent: 'center',
      paddingTop: '0.35rem',
      fontFamily: "'Share Tech Mono', monospace",
      fontSize: '0.62rem', letterSpacing: '0.1em', color: '#46626d',
    }}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {payload.map((e: any, i: number) => (
        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          <span style={{ display: 'inline-block', width: 16, height: 1.5, background: e.color }} />
          {String(e.value).toUpperCase()}
        </span>
      ))}
    </div>
  )
}

/* ── Panel shell ──────────────────────────────────────────────────────────── */
function ChartPanel({ title, children }: { title?: string; children: React.ReactNode }) {
  const idx  = title ? title.indexOf('_') : -1
  const main = idx > -1 ? title!.slice(0, idx) : title
  const sub  = idx > -1 ? title!.slice(idx)    : ''

  return (
    <div style={{
      position: 'relative',
      background: '#040c11',
      border: '1px solid rgba(95,165,188,0.24)',
      margin: '0.75rem 0',
      animation: 'widgetEntrance 0.35s ease both',
    }}>
      {/* corner brackets */}
      <span style={{ position:'absolute', top:-1, left:-1, width:11, height:11,
        borderTop:'1px solid #36abca', borderLeft:'1px solid #36abca', pointerEvents:'none' }} />
      <span style={{ position:'absolute', bottom:-1, right:-1, width:11, height:11,
        borderBottom:'1px solid #36abca', borderRight:'1px solid #36abca', pointerEvents:'none' }} />

      {/* panel header */}
      {title && (
        <div style={{
          display: 'flex', alignItems: 'center',
          height: 28, padding: '0 0.75rem',
          background: 'linear-gradient(90deg, rgba(29,93,112,0.85) 0%, rgba(29,93,112,0.22) 55%, transparent 100%)',
          borderBottom: '1px solid rgba(95,165,188,0.22)',
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: '0.82rem', fontWeight: 700,
          letterSpacing: '0.2em', textTransform: 'uppercase',
          userSelect: 'none',
          position: 'relative',
        }}>
          <span style={{ color: '#ffffff' }}>{main}</span>
          {sub && <span style={{ color: '#36abca' }}>{sub}</span>}
        </div>
      )}

      <div style={{ padding: '0.75rem 0.25rem 0.5rem 0' }}>
        {children}
      </div>
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
        <Tooltip content={tip} cursor={{ stroke: 'rgba(95,165,188,0.18)', strokeWidth: 1 }} />
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
        <Tooltip content={tip} cursor={{ stroke: 'rgba(95,165,188,0.18)', strokeWidth: 1 }} />
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
      <BarChart data={data} margin={{ top: 6, right: 16, bottom: 0, left: 0 }} barCategoryGap="32%">
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} {...xAxisProps} />
        <YAxis domain={yDomain ?? [0, 'auto']} {...yAxisProps} />
        <Tooltip content={tip} cursor={{ fill: 'rgba(95,165,188,0.05)' }} />
        <Legend content={StarkLegend} />
        {series.map((s, i) => {
          const c = resolveColor(s, i)
          return (
            <Bar key={s.key} dataKey={s.key} name={s.label ?? s.key}
              fill={`rgba(${hexToRgb(c)},0.52)`} stroke={c} strokeWidth={1}
              isAnimationActive={false}
            />
          )
        })}
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
      style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: 10, fill: '#7a96a1', letterSpacing: '0.06em' }}
    >
      {`${String(name).toUpperCase()} ${(percent * 100).toFixed(0)}%`}
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
          labelLine={{ stroke: 'rgba(95,165,188,0.28)', strokeWidth: 1 }}
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
    <div style={{
      padding: '0.5rem 0.75rem',
      background: 'rgba(200,74,58,0.09)',
      border: '1px solid rgba(200,74,58,0.35)',
      fontFamily: "'Share Tech Mono', monospace",
      fontSize: '0.71rem', color: '#c84a3a',
      margin: '0.5rem 0', letterSpacing: '0.05em',
    }}>
      CHART // PARSE ERROR<br />
      <span style={{ color: '#46626d', fontSize: '0.65rem' }}>{raw.slice(0, 120)}</span>
    </div>
  )
}

/* ── Main export ──────────────────────────────────────────────────────────── */
export default function ChartBlock({ children }: { children: string }) {
  const spec = useMemo<ChartSpec | null>(() => {
    try   { return JSON.parse(children) as ChartSpec }
    catch { return null }
  }, [children])

  if (!spec) return <ParseError raw={children} />

  return (
    <ChartPanel title={spec.title}>
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
