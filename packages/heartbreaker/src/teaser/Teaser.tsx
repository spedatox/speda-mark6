import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import NeuralBackground from '@renderer/components/NeuralBackground'
import { BRANDS } from '@renderer/profile/brands'
import { applyTheme, morphTheme } from '@renderer/profile/theme'
import {
  TIMELINE, DEFAULT_PARAMS, SIX_ORDER, sixTiming, resolveTimeline,
  type Beat, type Caption, type Durations, type TeaserParams, type BeatId,
} from './script'
import { envelope } from './anim'
import {
  ColdOpen, Ignition, Capabilities, Owner, Six, Proactivity, Collaboration, Resolve,
  type SceneProps,
} from './scenes'

const SPEDA = BRANDS.speda.accent
const UI = "'Rajdhani', sans-serif"
const MONO = "'Share Tech Mono', monospace"
const DURS_KEY = 'teaser_durs_v1'
const PARAMS_KEY = 'teaser_params_v1'

function accentAt(now: number, beats: Beat[]): { key: string; accent: string } {
  const six = beats.find(b => b.id === 'six')
  if (six && now >= six.t0 && now < six.t1) {
    const { intro, slot } = sixTiming(six.dur)
    const lt = now - six.t0
    if (lt < intro) return { key: 'speda', accent: SPEDA }
    const idx = Math.min(5, Math.floor((lt - intro) / slot))
    return { key: 'six-' + idx, accent: BRANDS[SIX_ORDER[idx]].accent }
  }
  const col = beats.find(b => b.id === 'collaboration')
  if (col && now >= col.t0 && now < col.t1) {
    const step = Math.floor((now - col.t0) / 1.1) % 6
    return { key: 'col-' + step, accent: BRANDS[SIX_ORDER[step]].accent }
  }
  return { key: 'speda', accent: SPEDA }
}

const SCENES: Record<BeatId, (p: SceneProps) => JSX.Element> = {
  cold: ColdOpen, ignition: Ignition, capabilities: Capabilities, owner: Owner,
  six: Six, proactivity: Proactivity, collaboration: Collaboration, resolve: Resolve,
}

function loadJSON<T>(key: string, fallback: T): T {
  try { const r = localStorage.getItem(key); return r ? { ...fallback, ...JSON.parse(r) } : fallback }
  catch { return fallback }
}

export default function Teaser() {
  const params0 = new URLSearchParams(location.search)
  const frozenAt = params0.has('t') ? Number(params0.get('t')) : null
  const format: 'wide' | 'tall' = params0.get('v') ? 'tall' : 'wide'
  const clean = params0.has('clean')   // hide all controls (for recording)

  const [durs, setDurs] = useState<Durations>(() => loadJSON<Durations>(DURS_KEY, {}))
  const [tparams, setTparams] = useState<TeaserParams>(() => loadJSON<TeaserParams>(PARAMS_KEY, DEFAULT_PARAMS))
  const [showCaptions, setShowCaptions] = useState<boolean>(() => loadJSON<{ c: boolean }>('teaser_cap_v1', { c: true }).c)
  const [panelOpen, setPanelOpen] = useState(params0.has('panel'))

  const { beats, captions, duration } = useMemo(() => resolveTimeline(durs), [durs])

  const [now, setNow] = useState(frozenAt ?? 0)
  const [playing, setPlaying] = useState(params0.has('autoplay'))
  const startRef = useRef(0)
  const lastAccent = useRef(SPEDA)

  useLayoutEffect(() => { applyTheme(SPEDA); lastAccent.current = SPEDA }, [])

  // persist
  useEffect(() => { localStorage.setItem(DURS_KEY, JSON.stringify(durs)) }, [durs])
  useEffect(() => { localStorage.setItem(PARAMS_KEY, JSON.stringify(tparams)) }, [tparams])
  useEffect(() => { localStorage.setItem('teaser_cap_v1', JSON.stringify({ c: showCaptions })) }, [showCaptions])

  // clock
  useEffect(() => {
    if (frozenAt != null || !playing) return
    let raf = 0
    startRef.current = performance.now() - now * 1000
    const tick = (t: number) => {
      const elapsed = (t - startRef.current) / 1000
      if (elapsed < duration) { setNow(elapsed); raf = requestAnimationFrame(tick) }
      else { setNow(duration); setPlaying(false) }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, frozenAt, duration])

  // theme driver
  const target = useMemo(() => accentAt(now, beats), [now, beats])
  useEffect(() => {
    if (frozenAt != null) { applyTheme(target.accent); lastAccent.current = target.accent; return }
    if (target.accent !== lastAccent.current) {
      morphTheme(lastAccent.current, target.accent, 650)
      lastAccent.current = target.accent
    }
  }, [target.key, frozenAt])
  if (frozenAt != null) applyTheme(target.accent)

  const beat = beats.find(b => now >= b.t0 && now < b.t1) ?? beats[beats.length - 1]
  const Scene = SCENES[beat.id]
  const sceneProps: SceneProps = { now, local: now - beat.t0, dur: beat.dur, format, params: tparams }

  return (
    <div style={{ position: 'fixed', inset: 0, overflow: 'hidden', background: 'var(--hb-void)' }}>
      <NeuralBackground />

      <div style={{ position: 'absolute', inset: 0, zIndex: 1 }}>
        <Scene {...sceneProps} />
      </div>

      {showCaptions && <Captions now={now} captions={captions} />}

      <div style={{ position: 'absolute', inset: 0, zIndex: 3, pointerEvents: 'none',
        boxShadow: 'inset 0 0 240px 60px rgba(0,0,0,0.65)' }} />

      {/* play gate */}
      {frozenAt == null && !playing && !panelOpen && (
        <button onClick={() => { if (now >= duration) setNow(0); setPlaying(true) }} style={gateStyle}>
          <span style={{ fontSize: 40, lineHeight: 1 }}>▶</span>
          <span style={{ fontFamily: MONO, fontSize: 12, letterSpacing: '0.3em', marginTop: 14 }}>
            {now > 0 && now < duration ? 'RESUME' : 'PLAY TEASER'}
          </span>
        </button>
      )}

      {/* settings */}
      {frozenAt == null && !clean && (
        <button onClick={() => { setPlaying(false); setPanelOpen(o => !o) }} title="Timing & sections"
          style={gearStyle}>⚙</button>
      )}
      {panelOpen && (
        <SettingsPanel
          beats={beats} durs={durs} setDurs={setDurs}
          tparams={tparams} setTparams={setTparams}
          showCaptions={showCaptions} setShowCaptions={setShowCaptions}
          now={now} setNow={(v) => { setPlaying(false); setNow(v) }}
          duration={duration} onClose={() => setPanelOpen(false)}
          onPlay={() => { setPanelOpen(false); if (now >= duration) setNow(0); setPlaying(true) }}
        />
      )}
    </div>
  )
}

function Captions({ now, captions }: { now: number; captions: Caption[] }) {
  const active = captions.filter(c => now >= c.t0 && now < c.t1)
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, bottom: '9%', zIndex: 4,
      display: 'flex', flexDirection: 'column', alignItems: 'center', pointerEvents: 'none', padding: '0 8vw' }}>
      {active.map((c, i) => {
        const o = envelope(now - c.t0, c.t1 - c.t0, 0.35, 0.35)
        return (
          <div key={i} style={{ opacity: o, textAlign: 'center' }}>
            <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 'clamp(18px,2.4vw,30px)',
              letterSpacing: '0.02em', color: 'var(--hb-text)', textShadow: '0 2px 18px rgba(0,0,0,0.8)' }}>
              {c.text}
            </div>
            {c.sub && (
              <div style={{ fontFamily: MONO, fontSize: 'clamp(11px,1.3vw,15px)',
                letterSpacing: '0.28em', color: 'var(--hb-cyan)', marginTop: 8 }}>{c.sub}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ── Settings / editor ──────────────────────────────────────────────────────── */
function SettingsPanel(props: {
  beats: Beat[]; durs: Durations; setDurs: (d: Durations) => void
  tparams: TeaserParams; setTparams: (p: TeaserParams) => void
  showCaptions: boolean; setShowCaptions: (b: boolean) => void
  now: number; setNow: (n: number) => void; duration: number
  onClose: () => void; onPlay: () => void
}) {
  const { beats, durs, setDurs, tparams, setTparams, showCaptions, setShowCaptions, now, setNow, duration } = props
  const ignDur = beats.find(b => b.id === 'ignition')!.dur

  const row: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 7 }
  const lbl: React.CSSProperties = { fontFamily: MONO, fontSize: 11, letterSpacing: '0.06em', color: 'var(--hb-text-dim)' }

  return (
    <div className="hb-holo" style={{ position: 'absolute', top: 16, right: 16, zIndex: 20,
      width: 320, maxHeight: '92vh', overflowY: 'auto', padding: '0.9rem 1rem',
      pointerEvents: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontFamily: UI, fontWeight: 700, letterSpacing: '0.2em', color: '#fff', fontSize: 14 }}>
          DIRECTOR
        </span>
        <button onClick={props.onClose} style={miniBtn}>✕</button>
      </div>

      {/* scrubber */}
      <div style={{ ...lbl, marginBottom: 4 }}>SCRUB · {now.toFixed(1)}s / {duration.toFixed(0)}s</div>
      <input type="range" min={0} max={duration} step={0.1} value={now}
        onChange={e => setNow(Number(e.target.value))} style={{ width: '100%', marginBottom: 6 }} />
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <button onClick={props.onPlay} style={{ ...miniBtn, flex: 1 }}>▶ PLAY</button>
        <button onClick={() => setNow(0)} style={{ ...miniBtn, flex: 1 }}>⟲ START</button>
      </div>

      <div style={{ ...lbl, color: 'var(--hb-cyan)', marginBottom: 8 }}>SECTION DURATIONS (s)</div>
      {beats.map(b => (
        <div key={b.id} style={row}>
          <span style={lbl}>{TIMELINE.find(t => t.id === b.id)!.label}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button style={stepBtn} onClick={() => setDurs({ ...durs, [b.id]: Math.max(2, b.dur - 0.5) })}>−</button>
            <input type="number" step={0.5} min={2} value={b.dur}
              onChange={e => setDurs({ ...durs, [b.id]: Math.max(2, Number(e.target.value) || 2) })}
              style={numInput} />
            <button style={stepBtn} onClick={() => setDurs({ ...durs, [b.id]: b.dur + 0.5 })}>+</button>
          </span>
        </div>
      ))}

      <div style={{ ...lbl, color: 'var(--hb-cyan)', margin: '14px 0 8px' }}>TIMING</div>
      <div style={{ ...lbl, marginBottom: 4 }}>
        SPEDA wordmark appears · {tparams.wordmarkAt.toFixed(1)}s into Ignition
      </div>
      <input type="range" min={0} max={Math.max(1, ignDur)} step={0.1} value={tparams.wordmarkAt}
        onChange={e => setTparams({ ...tparams, wordmarkAt: Number(e.target.value) })}
        style={{ width: '100%', marginBottom: 12 }} />

      <div style={row}>
        <span style={lbl}>Captions</span>
        <button style={miniBtn} onClick={() => setShowCaptions(!showCaptions)}>
          {showCaptions ? 'ON' : 'OFF'}
        </button>
      </div>

      <button onClick={() => { setDurs({}); setTparams(DEFAULT_PARAMS) }}
        style={{ ...miniBtn, width: '100%', marginTop: 12 }}>RESET TO DEFAULTS</button>
      <div style={{ ...lbl, marginTop: 10, color: 'var(--hb-text-faint)', lineHeight: 1.5 }}>
        Add <code>?clean</code> to the URL to hide all controls for recording. Total: {duration.toFixed(1)}s.
      </div>
    </div>
  )
}

const gateStyle: React.CSSProperties = {
  position: 'absolute', inset: 0, zIndex: 10, display: 'flex', flexDirection: 'column',
  alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
  background: 'rgba(2,5,8,0.55)', border: 'none', color: 'var(--hb-text)',
  fontFamily: UI, letterSpacing: '0.1em',
}
const gearStyle: React.CSSProperties = {
  position: 'absolute', bottom: 16, right: 16, zIndex: 15, width: 38, height: 38,
  borderRadius: 10, cursor: 'pointer', fontSize: 18,
  background: 'rgba(var(--hb-accent-rgb),0.12)', border: '1px solid var(--hb-edge)',
  color: 'var(--hb-cyan-bright)',
}
const miniBtn: React.CSSProperties = {
  fontFamily: MONO, fontSize: 11, letterSpacing: '0.08em', cursor: 'pointer',
  padding: '5px 10px', borderRadius: 7, background: 'rgba(var(--hb-accent-rgb),0.10)',
  border: '1px solid var(--hb-edge)', color: 'var(--hb-cyan-bright)',
}
const stepBtn: React.CSSProperties = {
  width: 22, height: 22, borderRadius: 6, cursor: 'pointer', lineHeight: 1,
  background: 'rgba(var(--hb-accent-rgb),0.10)', border: '1px solid var(--hb-edge)',
  color: 'var(--hb-cyan-bright)', fontSize: 14,
}
const numInput: React.CSSProperties = {
  width: 46, textAlign: 'center', fontFamily: MONO, fontSize: 12,
  background: 'rgba(var(--hb-accent-rgb),0.06)', border: '1px solid var(--hb-edge)',
  borderRadius: 6, color: 'var(--hb-text)', padding: '3px 2px',
}
