/**
 * Teaser scenes — one component per beat. Visuals are driven entirely by the
 * theme CSS variables (so every scene re-hues during an agent morph) plus the
 * REAL product widgets (ChartBlock, CalendarBlock) imported from the renderer.
 */
import { BRANDS } from '@renderer/profile/brands'
import ChartBlock from '@renderer/components/ChartBlock'
import CalendarBlock from '@renderer/components/CalendarBlock'
import { clamp01, easeOut, envelope, rise } from './anim'
import { SIX_ORDER, SIX_NAME, SIX_DOMAIN, sixTiming, type TeaserParams } from './script'
import { MCP_LOGOS } from './logos'

const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"

export interface SceneProps {
  now: number
  local: number   // seconds since this beat started
  dur: number     // beat duration
  format: 'wide' | 'tall'
  params: TeaserParams
}

/* ── Shared chrome ──────────────────────────────────────────────────────────── */
function CornerBrackets({ o = 1 }: { o?: number }) {
  const arm = 26
  const s = { position: 'absolute' as const, width: arm, height: arm, opacity: o,
    borderColor: 'var(--hb-cyan)', pointerEvents: 'none' as const }
  return (
    <>
      <span style={{ ...s, top: 18, left: 18, borderTop: '1px solid', borderLeft: '1px solid' }} />
      <span style={{ ...s, top: 18, right: 18, borderTop: '1px solid', borderRight: '1px solid' }} />
      <span style={{ ...s, bottom: 18, left: 18, borderBottom: '1px solid', borderLeft: '1px solid' }} />
      <span style={{ ...s, bottom: 18, right: 18, borderBottom: '1px solid', borderRight: '1px solid' }} />
    </>
  )
}
function center(extra?: React.CSSProperties): React.CSSProperties {
  return { position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', ...extra }
}

/* ── 1 · Cold open ──────────────────────────────────────────────────────────── */
export function ColdOpen({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 1.2, 1.0)
  const blink = Math.floor(local * 1.6) % 2 === 0
  const lineW = easeOut(clamp01((local - 1.2) / 2.2)) * 220
  return (
    <div style={center({ opacity: o })}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: MONO,
        fontSize: 13, letterSpacing: '0.35em', color: 'var(--hb-text-faint)' }}>
        <span>SPEDA</span>
        <span style={{ opacity: blink ? 1 : 0, color: 'var(--hb-cyan)' }}>▮</span>
      </div>
      <div style={{ height: 1, width: lineW, marginTop: 14,
        background: 'linear-gradient(90deg, transparent, var(--hb-cyan), transparent)' }} />
      <div style={{ marginTop: 16, fontFamily: MONO, fontSize: 10.5, letterSpacing: '0.25em',
        color: 'var(--hb-text-faint)', opacity: clamp01((local - 2.6) / 1.5) * 0.7 }}>
        SYSTEM ONLINE
      </div>
    </div>
  )
}

/* ── 2 · Ignition — wordmark assembles at params.wordmarkAt ("I am SPEDA…") ──── */
export function Ignition({ local, dur, params }: SceneProps) {
  const o = envelope(local, dur, 0.6, 0.8)
  const w = local - params.wordmarkAt          // local time since wordmark cue
  const letters = 'SPEDA'.split('')
  return (
    <div style={center({ opacity: o })}>
      <CornerBrackets o={clamp01((local - 0.3) / 1.2)} />
      <TelemetryStrip reveal={clamp01((local - 0.6) / 1.4)} />

      {/* before the cue: a quiet boot dash holds the frame */}
      <div style={{ position: 'absolute', opacity: clamp01((-w) / 0.5) * clamp01((local - 0.8) / 1.0),
        fontFamily: MONO, fontSize: 12, letterSpacing: '0.3em', color: 'var(--hb-text-faint)' }}>
        INITIALISING…
      </div>

      <div style={{ display: 'flex', gap: '0.12em', opacity: clamp01(w / 0.2) }}>
        {letters.map((ch, i) => {
          const p = clamp01((w - i * 0.16) / 0.6)
          const e = easeOut(p)
          return (
            <span key={i} style={{
              fontFamily: UI, fontWeight: 700, fontSize: 'clamp(48px, 9vw, 116px)',
              letterSpacing: '0.06em', color: 'var(--hb-text)',
              opacity: e, transform: `translateY(${(1 - e) * 22}px)`,
              textShadow: '0 0 40px rgba(var(--hb-accent-rgb),0.35)',
            }}>{ch}</span>
          )
        })}
      </div>
      <div style={{ ...rise(clamp01((w - 1.0) / 0.8)), marginTop: 6, fontFamily: UI,
        fontWeight: 600, fontSize: 'clamp(16px, 2.4vw, 30px)', letterSpacing: '0.5em',
        color: 'var(--hb-cyan)', paddingLeft: '0.5em' }}>
        MARK VI
      </div>
    </div>
  )
}

function TelemetryStrip({ reveal }: { reveal: number }) {
  const cells = [
    ['HOST', 'speda.local'], ['LINK', 'ONLINE'], ['RTT', '3ms'],
    ['TOOLS', '24'], ['MODEL', 'OPUS 4.8'], ['SESS', '02'],
  ]
  return (
    <div style={{ position: 'absolute', top: 26, left: 0, right: 0, display: 'flex',
      justifyContent: 'center', gap: 22, fontFamily: MONO, fontSize: 11,
      letterSpacing: '0.12em', opacity: reveal }}>
      {cells.map(([k, v], i) => (
        <span key={i} style={{ display: 'inline-flex', gap: 5 }}>
          <span style={{ color: 'var(--hb-text-faint)' }}>{k}</span>
          <span style={{ color: k === 'LINK' ? '#4fa377' : 'var(--hb-text-dim)' }}>{v}</span>
        </span>
      ))}
    </div>
  )
}

/* ── 3 · Capabilities — a guided focus tour (one capability at a time) ───────── */
const CHART_SPEC = JSON.stringify({
  type: 'area', title: 'THROUGHPUT_LIVE', xKey: 'x',
  series: [{ key: 'v', label: 'OPS' }],
  data: [{ x: 'A', v: 12 }, { x: 'B', v: 34 }, { x: 'C', v: 28 }, { x: 'D', v: 52 }, { x: 'E', v: 47 }, { x: 'F', v: 68 }],
  height: 230,
})
const CAL_SPEC = JSON.stringify({
  title: 'THIS WEEK', range: '30 JUN – 6 JUL',
  days: [
    { date: '2026-06-30', events: [{ time: '09:00', title: 'Standup' }, { time: '14:00', title: 'Dentist' }] },
    { date: '2026-07-01', events: [{ time: '11:00', title: '1:1', color: '#d99c44' }] },
    { date: '2026-07-02', events: [] },
    { date: '2026-07-03', events: [{ time: '16:00', title: 'Review' }] },
    { date: '2026-07-04', events: [] },
  ],
})

const FOCUS = [
  { key: 'chart', label: 'INLINE RENDER · LIVE CHARTS' },
  { key: 'calendar', label: 'HOLOGRAPHIC CALENDAR' },
  { key: 'files', label: 'FILE DELIVERY · PPTX · PDF · CODE' },
  { key: 'mcp', label: 'CONNECTED · MCP INTEGRATIONS' },
  { key: 'subagents', label: 'SUB-AGENTS · PARALLEL WORKERS' },
  { key: 'memory', label: 'PERSISTENT MEMORY' },
  { key: 'voice', label: 'VOICE · SPEAK & LISTEN' },
] as const

export function Capabilities({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 0.6, 0.8)
  const per = dur / FOCUS.length
  const idx = Math.min(FOCUS.length - 1, Math.floor(local / per))
  const lt = local - idx * per
  const item = FOCUS[idx]
  const inP = clamp01(lt / 0.55)
  const outP = clamp01((per - lt) / 0.5)
  const vis = Math.min(inP, outP)
  const scale = 0.92 + easeOut(inP) * 0.08

  return (
    <div style={center({ opacity: o })}>
      {/* capability label */}
      <div style={{ fontFamily: MONO, fontSize: 'clamp(10px,1.1vw,13px)', letterSpacing: '0.28em',
        color: 'var(--hb-cyan)', marginBottom: 18, opacity: vis }}>{item.label}</div>

      {/* focused visual */}
      <div style={{ opacity: vis, transform: `scale(${scale})`,
        width: 'min(720px, 86vw)', display: 'flex', justifyContent: 'center' }}>
        {item.key === 'chart' && <Framed><div style={{ pointerEvents: 'none' }}><ChartBlock>{CHART_SPEC}</ChartBlock></div></Framed>}
        {item.key === 'calendar' && <div style={{ pointerEvents: 'none', width: '100%' }}><CalendarBlock>{CAL_SPEC}</CalendarBlock></div>}
        {item.key === 'files' && <FocusFiles t={lt} />}
        {item.key === 'mcp' && <FocusMcp t={lt} />}
        {item.key === 'subagents' && <FocusSubAgents t={lt} />}
        {item.key === 'memory' && <FocusMemory t={lt} />}
        {item.key === 'voice' && <FocusVoice t={lt} />}
      </div>

      {/* progress pips */}
      <div style={{ display: 'flex', gap: 8, marginTop: 26, opacity: o }}>
        {FOCUS.map((f, i) => (
          <span key={f.key} style={{ width: i === idx ? 22 : 7, height: 7, borderRadius: 4,
            background: i === idx ? 'var(--hb-cyan)' : 'var(--hb-line)', transition: 'all .35s' }} />
        ))}
      </div>
    </div>
  )
}

function Framed({ children }: { children: React.ReactNode }) {
  return <div className="hb-holo" style={{ padding: '0.6rem 0.8rem', width: '100%' }}>{children}</div>
}

/* File delivery — generating → delivered download card. */
function FocusFiles({ t }: { t: number }) {
  const prog = clamp01((t - 0.4) / 1.4)
  const done = prog >= 1
  return (
    <div className="hb-holo" style={{ width: 'min(440px,78vw)', padding: '1.3rem 1.4rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ position: 'relative', width: 56, height: 70, borderRadius: 6,
          border: '1px solid var(--hb-edge-bright)', background: 'rgba(var(--hb-accent-rgb),0.10)',
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.08em',
            color: 'var(--hb-cyan-bright)' }}>PPTX</span>
          <span style={{ position: 'absolute', top: 0, right: 0, width: 16, height: 16,
            borderLeft: '1px solid var(--hb-edge-bright)', borderBottom: '1px solid var(--hb-edge-bright)' }} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: UI, fontWeight: 600, fontSize: 18, color: 'var(--hb-text)' }}>
            Q3_Briefing.pptx
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--hb-text-dim)', marginTop: 2 }}>
            {done ? '1.8 MB · ready to download' : 'generating…'}
          </div>
          <div style={{ height: 4, marginTop: 10, borderRadius: 2, background: 'rgba(var(--hb-accent-rgb),0.14)' }}>
            <div style={{ height: '100%', width: `${prog * 100}%`, borderRadius: 2,
              background: 'var(--hb-cyan)', boxShadow: '0 0 8px var(--hb-cyan)' }} />
          </div>
        </div>
      </div>
      <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        opacity: done ? 1 : 0.3, transition: 'opacity .3s' }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: '#4fa377', letterSpacing: '0.1em' }}>
          {done ? '✓ DELIVERED' : '· · ·'}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.12em',
          padding: '4px 12px', borderRadius: 6, border: '1px solid var(--hb-edge-bright)',
          color: 'var(--hb-cyan-bright)' }}>▾ DOWNLOAD</span>
      </div>
    </div>
  )
}

/* MCP — real logos lighting up around a SPEDA hub. */
function FocusMcp({ t }: { t: number }) {
  return (
    <div className="hb-holo" style={{ width: 'min(620px,86vw)', padding: '1.6rem 1.4rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        {MCP_LOGOS.map((l, i) => {
          const on = t > 0.5 + i * 0.18
          return (
            <div key={l.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 9,
              padding: '0.9rem 0.5rem', borderRadius: 10,
              border: `1px solid ${on ? 'var(--hb-edge-bright)' : 'var(--hb-line)'}`,
              background: on ? 'rgba(var(--hb-accent-rgb),0.08)' : 'transparent',
              boxShadow: on ? '0 0 22px rgba(var(--hb-accent-rgb),0.16) inset' : 'none',
              transition: 'all .3s' }}>
              <span style={{ width: 34, height: 34, display: 'inline-flex',
                color: on ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)', transition: 'color .3s' }}
                dangerouslySetInnerHTML={{ __html: l.svg }} />
              <span style={{ fontFamily: MONO, fontSize: 10.5, letterSpacing: '0.06em',
                color: on ? 'var(--hb-text-dim)' : 'var(--hb-text-faint)' }}>{l.name}</span>
              <span style={{ width: 6, height: 6, borderRadius: '50%',
                background: on ? '#4fa377' : 'var(--hb-line)',
                boxShadow: on ? '0 0 7px #4fa377' : 'none' }} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* Sub-agents — a delegation graph: SPEDA hub → parallel workers, pulses outward. */
function FocusSubAgents({ t }: { t: number }) {
  const workers = [
    { a: -0.78, label: 'RESEARCH', effort: 'medium' },
    { a: -0.26, label: 'SYNTHESIS', effort: 'high' },
    { a: 0.26, label: 'PRE-FILTER', effort: 'low' },
    { a: 0.78, label: 'VERIFY', effort: 'low' },
  ]
  const R = 150
  return (
    <svg viewBox="-260 -150 520 300" style={{ width: 'min(640px,88vw)' }}>
      {workers.map((w, i) => {
        const x = Math.sin(w.a) * R, y = 70
        const lit = t > 0.4 + i * 0.2
        // pulse position along the line
        const pp = (t * 0.7 + i * 0.25) % 1
        const px = x * pp, py = -40 + (y + 40) * pp
        return (
          <g key={i} opacity={easeOut(clamp01((t - 0.2 - i * 0.18) / 0.6))}>
            <line x1={0} y1={-40} x2={x} y2={y} stroke="var(--hb-cyan)" strokeWidth={0.8}
              opacity={lit ? 0.4 : 0.15} />
            {lit && <circle cx={px} cy={py} r={3} fill="var(--hb-cyan-bright)" />}
            <circle cx={x} cy={y} r={30} fill="rgba(var(--hb-accent-rgb),0.10)"
              stroke="var(--hb-edge-bright)" strokeWidth={0.8} />
            <text x={x} y={y - 2} textAnchor="middle" fontFamily={MONO} fontSize={10}
              fill="var(--hb-text)" letterSpacing="1">{w.label}</text>
            <text x={x} y={y + 12} textAnchor="middle" fontFamily={MONO} fontSize={8.5}
              fill="var(--hb-cyan)" letterSpacing="1">{w.effort}</text>
          </g>
        )
      })}
      {/* hub */}
      <circle cx={0} cy={-40} r={36} fill="rgba(var(--hb-accent-rgb),0.16)"
        stroke="var(--hb-edge-bright)" strokeWidth={1} />
      <text x={0} y={-36} textAnchor="middle" fontFamily={UI} fontSize={15} fontWeight="700"
        fill="var(--hb-cyan-bright)">SPEDA</text>
    </svg>
  )
}

/* Memory — shards of context streaming into a persistent glowing core. */
function FocusMemory({ t }: { t: number }) {
  const shards = ['OWNER PROFILE', 'PREFERENCES', 'PROJECT STATE', 'PAST DECISIONS', 'FEEDBACK']
  const R = 165
  return (
    <svg viewBox="-260 -150 520 300" style={{ width: 'min(620px,86vw)' }}>
      {shards.map((s, i) => {
        const a = (i / shards.length) * Math.PI * 2 - Math.PI / 2
        const draw = easeOut(clamp01((t - 0.3 - i * 0.18) / 0.8))
        const pull = easeOut(clamp01((t - 1.6) / 1.6))   // late: stream into core
        const x = Math.cos(a) * R * (1 - pull * 0.7)
        const y = Math.sin(a) * R * 0.62 * (1 - pull * 0.7)
        return (
          <g key={i} opacity={draw}>
            <line x1={x} y1={y} x2={0} y2={0} stroke="var(--hb-cyan)" strokeWidth={0.6} opacity={0.18 + pull * 0.25} />
            <g transform={`translate(${x},${y})`}>
              <rect x={-44} y={-11} width={88} height={22} rx={5}
                fill="rgba(var(--hb-accent-rgb),0.10)" stroke="var(--hb-edge-bright)" strokeWidth={0.7} />
              <text x={0} y={4} textAnchor="middle" fontFamily={MONO} fontSize={8.5}
                fill="var(--hb-text-dim)" letterSpacing="0.5">{s}</text>
            </g>
          </g>
        )
      })}
      {/* core */}
      <circle cx={0} cy={0} r={24 + easeOut(clamp01((t - 1.0) / 1.5)) * 12}
        fill="var(--hb-cyan)" opacity={0.22} />
      <circle cx={0} cy={0} r={16} fill="var(--hb-cyan-bright)" opacity={0.85} />
      <text x={0} y={42} textAnchor="middle" fontFamily={MONO} fontSize={10}
        fill="var(--hb-cyan)" letterSpacing="2" opacity={clamp01((t - 1.2) / 1)}>NEVER FADES</text>
    </svg>
  )
}

/* Voice — large waveform. */
function FocusVoice({ t }: { t: number }) {
  const bars = 40
  return (
    <div className="hb-holo" style={{ width: 'min(560px,82vw)', padding: '2rem 1.6rem',
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, height: 150 }}>
      {Array.from({ length: bars }).map((_, i) => {
        const env = Math.sin((i / bars) * Math.PI)
        const h = 6 + (Math.sin(t * 7 + i * 0.5) * 0.5 + 0.5) * 78 * (0.35 + env)
        return <span key={i} style={{ width: 4, height: h, background: 'var(--hb-cyan)',
          opacity: 0.4 + (Math.sin(t * 7 + i * 0.5) * 0.5 + 0.5) * 0.6, borderRadius: 2 }} />
      })}
    </div>
  )
}

/* ── 4 · Owner — single user, NO name ───────────────────────────────────────── */
export function Owner({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 0.6, 0.7)
  // a field of faint users; exactly one lights up — "for one".
  const cols = 11, rows = 5
  const cx = Math.floor(cols / 2), cy = Math.floor(rows / 2)
  return (
    <div style={center({ opacity: o })}>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 'clamp(14px,2vw,30px)',
        marginBottom: 36 }}>
        {Array.from({ length: cols * rows }).map((_, i) => {
          const c = i % cols, r = Math.floor(i / cols)
          const isOne = c === cx && r === cy
          const appear = clamp01((local - 0.2 - (Math.abs(c - cx) + Math.abs(r - cy)) * 0.06) / 0.6)
          const dim = clamp01((local - 1.4) / 1.2)   // others fade as the one rises
          return (
            <div key={i} style={{ position: 'relative', width: 'clamp(8px,0.9vw,12px)', height: 'clamp(8px,0.9vw,12px)',
              borderRadius: '50%', justifySelf: 'center',
              background: isOne ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
              opacity: isOne ? appear : appear * (1 - dim * 0.8),
              boxShadow: isOne ? `0 0 ${10 + dim * 16}px var(--hb-cyan-bright)` : 'none',
              transform: isOne ? `scale(${1 + dim * 0.9})` : 'scale(1)' }} />
          )
        })}
      </div>
      <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 'clamp(22px,3.4vw,44px)',
        letterSpacing: '0.14em', color: 'var(--hb-text)', ...rise(clamp01((local - 1.3) / 0.9)) }}>
        BUILT FOR ONE
      </div>
      <div style={{ fontFamily: MONO, fontSize: 'clamp(11px,1.4vw,15px)', letterSpacing: '0.26em',
        color: 'var(--hb-cyan)', marginTop: 12, opacity: clamp01((local - 1.8) / 1.0) }}>
        A SINGLE USER · ONE VOICE · ONE STANDARD
      </div>
    </div>
  )
}

/* ── 5 · The Superior Six ───────────────────────────────────────────────────── */
export function Six({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 0.5, 0.8)
  const { intro, slot } = sixTiming(dur)
  const idx = local < intro ? -1 : Math.min(5, Math.floor((local - intro) / slot))

  if (idx < 0) {
    return (
      <div style={center({ opacity: o })}>
        <div style={{ fontFamily: UI, fontWeight: 600, fontSize: 'clamp(20px,3vw,38px)',
          letterSpacing: '0.1em', color: 'var(--hb-text)', textAlign: 'center', maxWidth: 760,
          ...rise(clamp01(local / 1.0)) }}>
          Six specialists.<br /><span style={{ color: 'var(--hb-cyan)' }}>Each master of a single domain.</span>
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 30, opacity: clamp01((local - 1.0) / 1.2) }}>
          {SIX_ORDER.map((id) => (
            <span key={id} style={{ width: 12, height: 12, borderRadius: '50%',
              background: BRANDS[id].accent, boxShadow: `0 0 12px ${BRANDS[id].accent}` }} />
          ))}
        </div>
      </div>
    )
  }

  const id = SIX_ORDER[idx]
  const b = BRANDS[id]
  const lt = (local - intro) % slot
  const ip = clamp01(lt / 0.7)
  const op = clamp01((slot - lt) / 0.6)
  const card = Math.min(ip, op)
  return (
    <div style={center({ opacity: o })}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'clamp(20px,4vw,60px)',
        opacity: card, transform: `translateY(${(1 - easeOut(ip)) * 22}px)` }}>
        <div style={{ position: 'relative', width: 'clamp(90px,11vw,150px)', height: 'clamp(90px,11vw,150px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ position: 'absolute', inset: 0, borderRadius: '50%',
            border: '1px solid var(--hb-edge-bright)',
            boxShadow: `0 0 40px rgba(var(--hb-accent-rgb),0.4), inset 0 0 24px rgba(var(--hb-accent-rgb),0.18)` }} />
          <span style={{ fontFamily: UI, fontWeight: 700, fontSize: 'clamp(40px,6vw,76px)',
            color: 'var(--hb-cyan-bright)' }}>{b.avatarInitial}</span>
        </div>
        <div style={{ textAlign: 'left' }}>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.25em',
            color: 'var(--hb-text-faint)' }}>{b.modelNumber.toUpperCase()}</div>
          <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 'clamp(34px,6vw,76px)',
            letterSpacing: '0.04em', color: 'var(--hb-text)', lineHeight: 1.02 }}>
            {SIX_NAME[id].toUpperCase()}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 'clamp(12px,1.5vw,17px)', letterSpacing: '0.22em',
            color: 'var(--hb-cyan)', marginTop: 8 }}>{SIX_DOMAIN[id]}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 9, marginTop: 44 }}>
        {SIX_ORDER.map((sid, i) => (
          <span key={sid} style={{ width: i === idx ? 26 : 8, height: 8, borderRadius: 4,
            background: i === idx ? 'var(--hb-cyan)' : 'var(--hb-line)', transition: 'all .4s' }} />
        ))}
      </div>
    </div>
  )
}

/* ── 6 · Proactivity (n8n) ──────────────────────────────────────────────────── */
const WATCHERS = [
  ['CRON', 'morning_brief', '06:30'],
  ['WATCHDOG', 'market_alert · AAPL', 'armed'],
  ['SIGNAL', 'budget_exceeded', 'armed'],
  ['CRON', 'inbox_triage', '08:00'],
]
export function Proactivity({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 0.6, 0.8)
  const fired = local > dur * 0.55
  return (
    <div style={center({ opacity: o, gap: 26 })}>
      <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.3em',
        color: 'var(--hb-text-faint)' }}>AUTOMATIONS · ACTIVE WHILE YOU'RE AWAY</div>
      <div style={{ width: 'min(560px, 80vw)', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {WATCHERS.map((w, i) => {
          const e = easeOut(clamp01((local - 0.4 - i * 0.35) / 0.7))
          return (
            <div key={i} className="hb-holo" style={{ display: 'flex', alignItems: 'center',
              gap: 12, padding: '0.6rem 0.85rem', opacity: e, transform: `translateX(${(1 - e) * -18}px)` }}>
              <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '0.12em',
                color: 'var(--hb-cyan)', minWidth: 78 }}>{w[0]}</span>
              <span style={{ fontFamily: MONO, fontSize: 12.5, color: 'var(--hb-text)', flex: 1 }}>{w[1]}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--hb-text-dim)' }}>{w[2]}</span>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#4fa377',
                boxShadow: '0 0 8px #4fa377' }} />
            </div>
          )
        })}
      </div>
      <div className="hb-holo" style={{
        position: 'absolute', right: 'clamp(20px,6vw,90px)', bottom: 'clamp(40px,10vh,120px)',
        width: 300, padding: '0.7rem 0.9rem',
        opacity: fired ? easeOut(clamp01((local - dur * 0.55) / 0.6)) : 0,
        transform: `translateY(${fired ? 0 : 20}px)`, transition: 'opacity .3s' }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '0.2em',
          color: 'var(--hb-cyan)', marginBottom: 5 }}>◆ SPEDA · PUSH</div>
        <div style={{ fontFamily: UI, fontWeight: 600, fontSize: 15, color: 'var(--hb-text)' }}>
          AAPL +4.2% — threshold hit.
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--hb-text-dim)', marginTop: 3 }}>
          Brief ready. Tap to review.
        </div>
      </div>
    </div>
  )
}

/* ── 7 · Collaboration (the six move as one) ────────────────────────────────── */
export function Collaboration({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 0.6, 0.9)
  const R = 150
  const conv = easeOut(clamp01((local - dur * 0.5) / (dur * 0.4)))
  return (
    <div style={center({ opacity: o })}>
      <svg viewBox="-240 -200 480 400" style={{ width: 'min(620px,86vw)', height: 'auto' }}>
        {SIX_ORDER.map((_, i) =>
          SIX_ORDER.map((_, j) => {
            if (j <= i) return null
            const ai = (i / 6) * Math.PI * 2, aj = (j / 6) * Math.PI * 2
            const x1 = Math.cos(ai) * R * (1 - conv), y1 = Math.sin(ai) * R * (1 - conv)
            const x2 = Math.cos(aj) * R * (1 - conv), y2 = Math.sin(aj) * R * (1 - conv)
            const lit = local > 1.0 + ((i + j) % 6) * 0.25
            return <line key={`${i}-${j}`} x1={x1} y1={y1} x2={x2} y2={y2}
              stroke="var(--hb-cyan)" strokeWidth={0.6} opacity={lit ? 0.18 + conv * 0.25 : 0.05} />
          })
        )}
        {SIX_ORDER.map((id, i) => {
          const a = (i / 6) * Math.PI * 2
          const x = Math.cos(a) * R * (1 - conv), y = Math.sin(a) * R * (1 - conv)
          const e = easeOut(clamp01((local - 0.3 - i * 0.18) / 0.7))
          return (
            <g key={id} opacity={e}>
              <circle cx={x} cy={y} r={10 + conv * 6} fill={BRANDS[id].accent} opacity={0.9} />
              <circle cx={x} cy={y} r={18} fill="none" stroke={BRANDS[id].accent} strokeWidth={0.8} opacity={0.5} />
            </g>
          )
        })}
        <circle cx={0} cy={0} r={10 + conv * 30} fill="var(--hb-cyan-bright)" opacity={conv * 0.85} />
      </svg>
      <div style={{ marginTop: 18, fontFamily: UI, fontWeight: 600, fontSize: 'clamp(16px,2.4vw,28px)',
        letterSpacing: '0.12em', color: 'var(--hb-text)', opacity: clamp01((local - dur * 0.55) / 1.5) }}>
        THE SIX MOVE AS ONE
      </div>
    </div>
  )
}

/* ── 8 · Resolve ────────────────────────────────────────────────────────────── */
export function Resolve({ local, dur }: SceneProps) {
  const o = envelope(local, dur, 1.0, 1.4)
  return (
    <div style={center({ opacity: o })}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4em', ...rise(clamp01(local / 1.2)) }}>
        <span style={{ fontFamily: UI, fontWeight: 700, fontSize: 'clamp(46px,8vw,104px)',
          letterSpacing: '0.08em', color: 'var(--hb-text)',
          textShadow: '0 0 40px rgba(var(--hb-accent-rgb),0.35)' }}>SPEDA</span>
        <span style={{ fontFamily: UI, fontWeight: 600, fontSize: 'clamp(16px,2.6vw,34px)',
          letterSpacing: '0.5em', color: 'var(--hb-cyan)' }}>MARK VI</span>
      </div>
      <div style={{ height: 1, width: 280, margin: '22px 0',
        background: 'linear-gradient(90deg, transparent, var(--hb-cyan), transparent)',
        opacity: clamp01((local - 1.2) / 1.0) }} />
      <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 'clamp(16px,2.2vw,26px)',
        letterSpacing: '0.18em', color: 'var(--hb-text-dim)', ...rise(clamp01((local - 1.5) / 1.2)) }}>
        Ready when you are.
      </div>
    </div>
  )
}
