import { useEffect, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useHealth } from '../lib/useHealth'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchModels, getConnections, getBudgetMode, setConnection, fetchMemoryFiles } from '../lib/api'
import type { ConnectionInfo, MemoryFileInfo } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'

/**
 * SYSTEMS BOARD — the "PERIODIC 56A." tactical overlay, mapped onto real data.
 *
 * Reference → function:
 *   Periodic table grid   → model routing matrix (tiles switch the active
 *                           model) + toolset matrix (tiles toggle MCP servers)
 *   IP address navigator  → uplink telemetry + per-server network node list
 *   Palladium gauge       → live ITPM prompt-prefix token budget
 *   Holographic radar     → RTT trace sparkline from the /health probe
 *   File archive rows     → session data banks
 *
 * Every value on this board comes from the backend. Nothing is set dressing.
 */


const MONO = "'Share Tech Mono', monospace"
const UI = "'Rajdhani', sans-serif"

const PROVIDER_TAGS: Record<string, string> = {
  anthropic: 'ANTHROPIC', openai: 'OPENAI', gemini: 'GEMINI', ollama: 'OLLAMA · LOCAL',
}

function symbolOf(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 .]/g, ' ').trim().split(/\s+/)
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${String(d.getFullYear()).slice(2)}`
}

/* ── Panel shell — bracketed steel module with a header plate ─────────────── */
function Panel({ title, light, right, pad = true, style, children }: {
  title: string
  light?: boolean
  right?: React.ReactNode
  pad?: boolean
  style?: React.CSSProperties
  children: React.ReactNode
}) {
  return (
    <section className="hb-holo" style={{
      position: 'relative', display: 'flex', flexDirection: 'column',
      minHeight: 0, minWidth: 0,
      overflow: 'hidden',
      ...style,
    }}>
      <header className={light ? 'hb-head-light' : 'hb-head-cyan'}
        style={{ flexShrink: 0, justifyContent: 'space-between' }}>
        <span>{title}</span>
        {right}
      </header>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0, padding: pad ? '0.5rem 0.55rem' : 0 }}>
        {children}
      </div>
    </section>
  )
}

/* ── Telemetry key/value row — the "IPv4 Adress: DENY" list style ─────────── */
function KV({ k, v, color, alt }: { k: string; v: React.ReactNode; color?: string; alt?: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8,
      padding: '0.22rem 0.35rem',
      background: alt ? 'rgba(54,171,202,0.04)' : 'transparent',
      fontFamily: MONO, fontSize: '0.6rem', letterSpacing: '0.05em',
    }}>
      <span style={{ color: '#3a6472', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{k}</span>
      <span style={{
        color: color || 'var(--hb-text-dim)', textAlign: 'right',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{v}</span>
    </div>
  )
}

/* ── Model tile — periodic-table element; click routes the active model ────── */
function ModelTile({ m, idx, active, onSelect }: {
  m: ModelInfo; idx: number; active: boolean; onSelect: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      className="hb-glass-xs"
      title={`${m.name} — ${m.description}`}
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 58, height: 58, position: 'relative', flexShrink: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer',
        border: `1px solid ${active ? 'rgba(242,183,92,0.8)' : hover ? 'rgba(110,200,228,0.75)' : 'rgba(95,165,188,0.4)'}`,
        background: active
          ? 'rgba(216, 110, 62, 0.3)'
          : hover
          ? 'rgba(54, 140, 168, 0.28)'
          : 'rgba(36, 98, 122, 0.18)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: active
          ? 'inset 0 1px 0 0 rgba(255,210,160,0.35)'
          : 'inset 0 1px 0 0 rgba(255,255,255,0.15)',
        transition: 'border-color 0.12s, background 0.12s, box-shadow 0.12s',
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: 4,
        fontFamily: MONO, fontSize: '0.48rem',
        color: active ? 'rgba(255,220,180,0.8)' : 'rgba(154,219,232,0.65)',
      }}>
        {String(idx + 1).padStart(2, '0')}
      </span>
      <span style={{
        fontFamily: UI, fontWeight: 600, fontSize: '1.18rem', lineHeight: 1,
        color: active ? '#ffd9a8' : '#bfe6f2',
        textShadow: active ? '0 0 8px rgba(232,150,74,0.4)' : '0 0 8px rgba(95,204,230,0.25)',
      }}>
        {symbolOf(m.name)}
      </span>
      <span style={{
        marginTop: 3, maxWidth: 52,
        fontFamily: MONO, fontSize: '0.42rem', letterSpacing: '0.04em',
        color: active ? 'rgba(255,225,190,0.75)' : 'rgba(154,200,215,0.6)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {m.name.toUpperCase()}
      </span>
    </button>
  )
}

/* ── Toolset tile — MCP server shard; click toggles it live ────────────────── */
function ToolTile({ c, idx, onToggle }: { c: ConnectionInfo; idx: number; onToggle: () => void }) {
  const [hover, setHover] = useState(false)
  const offline = !c.connected
  const engaged = c.connected && c.active
  return (
    <button
      className="hb-glass-xs"
      title={`${c.label} — ${c.tools} tools · ~${c.tokens} tokens${offline ? ' · OFFLINE' : engaged ? ' · click to disable' : ' · click to enable'}`}
      onClick={onToggle}
      disabled={offline}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 58, height: 58, position: 'relative', flexShrink: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        cursor: offline ? 'not-allowed' : 'pointer',
        border: `1px solid ${
          offline ? 'rgba(200,74,58,0.45)' :
          engaged ? (hover ? 'rgba(110,200,228,0.75)' : 'rgba(95,165,188,0.5)') :
          'rgba(95,165,188,0.2)'
        }`,
        background: offline
          ? 'rgba(86, 34, 30, 0.25)'
          : engaged
          ? (hover ? 'rgba(54, 140, 168, 0.28)' : 'rgba(36, 98, 122, 0.18)')
          : 'rgba(20, 42, 52, 0.15)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        opacity: !offline && !engaged ? 0.65 : 1,
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        transition: 'border-color 0.12s, background 0.12s, opacity 0.12s',
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: 4,
        fontFamily: MONO, fontSize: '0.48rem', color: 'rgba(154,219,232,0.55)',
      }}>
        {String(idx + 1).padStart(2, '0')}
      </span>
      <span style={{
        fontFamily: UI, fontWeight: 600, fontSize: '1.18rem', lineHeight: 1,
        color: offline ? '#d98a7a' : engaged ? '#bfe6f2' : '#5d8693',
      }}>
        {symbolOf(c.label)}
      </span>
      <span style={{
        marginTop: 3, fontFamily: MONO, fontSize: '0.42rem', letterSpacing: '0.04em',
        color: offline ? 'rgba(217,138,122,0.8)' : 'rgba(154,200,215,0.6)',
      }}>
        {offline ? 'OFFLINE' : `${c.tools} TOOLS · ${(c.tokens / 1000).toFixed(1)}K`}
      </span>
    </button>
  )
}

/* ── Segmented gauge — the block meter under the big percentage ────────────── */
function SegBar({ pct, color }: { pct: number; color: string }) {
  const SEGS = 22
  const lit = Math.round(Math.min(pct, 100) / 100 * SEGS)
  return (
    <div style={{ display: 'flex', gap: 2 }}>
      {Array.from({ length: SEGS }, (_, i) => (
        <span key={i} style={{
          flex: 1, height: 7,
          background: i < lit ? color : 'rgba(95,165,188,0.14)',
          boxShadow: i < lit ? `0 0 5px ${color}55` : 'none',
        }} />
      ))}
    </div>
  )
}

/* ── RTT trace — minimalist line-art sparkline from real health probes ─────── */
function Spark({ samples }: { samples: number[] }) {
  const W = 196, H = 56
  if (samples.length < 2) {
    return (
      <div style={{
        height: H, display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.18em', color: '#2e5260',
      }}>
        AWAITING TELEMETRY_
      </div>
    )
  }
  const max = Math.max(...samples, 1)
  const pts = samples
    .map((v, i) => `${(i / (samples.length - 1)) * W},${H - 4 - (v / max) * (H - 10)}`)
    .join(' ')
  return (
    <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      {[0.25, 0.5, 0.75].map(f => (
        <line key={f} x1={0} y1={H * f} x2={W} y2={H * f}
          stroke="rgba(95,165,188,0.12)" strokeWidth={1} strokeDasharray="2 4" />
      ))}
      <polyline points={pts} fill="none" stroke="#5fcce6" strokeWidth={1.2}
        style={{ filter: 'drop-shadow(0 0 3px rgba(95,204,230,0.5))' }} />
      <circle
        cx={W} cy={H - 4 - (samples[samples.length - 1] / max) * (H - 10)} r={2}
        fill="#f2b75c" />
    </svg>
  )
}

/* ── Main board ─────────────────────────────────────────────────────────────── */
export default function SystemsBoard({ config, onClose }: { config: AppConfig; onClose: () => void }) {
  const { state } = useChatContext()
  const { settings, update } = useSettings()
  const health = useHealth(config.apiBase, config.apiKey, 4000)
  const isMobile = useIsMobile()

  const [models, setModels] = useState<ModelInfo[]>([])
  const [servers, setServers] = useState<ConnectionInfo[]>([])
  const [budgetTokens, setBudgetTokens] = useState({ used: 0, limit: 30000 })
  const [budgetMode, setBudgetMode] = useState(true)
  const [rtt, setRtt] = useState<number[]>([])
  const [memFiles, setMemFiles] = useState<MemoryFileInfo[]>([])
  const [memPath, setMemPath] = useState<string | null>(null)

  const loadConns = () => getConnections(config).then(r => {
    setServers(r.servers)
    setBudgetTokens({ used: r.active_tool_tokens, limit: r.itpm_limit })
  }).catch(() => {})

  useEffect(() => {
    fetchModels(config).then(setModels).catch(() => {})
    getBudgetMode(config).then(setBudgetMode).catch(() => {})
    fetchMemoryFiles(config).then(files => {
      setMemFiles(files)
      // Open on the owner file — the extracted facts about the user.
      const preferred = files.find(f => f.path.endsWith('/owner.md')) ?? files[0]
      if (preferred) setMemPath(preferred.path)
    }).catch(() => {})
    loadConns()
  }, [config]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (health.latencyMs != null) {
      const v = health.latencyMs
      setRtt(prev => [...prev.slice(-31), v])
    }
  }, [health.latencyMs])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const toggleServer = async (c: ConnectionInfo) => {
    setServers(ss => ss.map(s => s.server === c.server ? { ...s, active: !c.active } : s))
    await setConnection(config, c.server, !c.active)
    loadConns()
  }

  const providers = Array.from(new Set(models.map(m => m.provider ?? 'anthropic')))
  const pct = Math.round((budgetTokens.used / Math.max(budgetTokens.limit, 1)) * 100)
  const gaugeColor = pct > 100 ? '#c84a3a' : pct > 70 ? '#f2b75c' : '#5fcce6'
  const maxServerTokens = Math.max(...servers.map(s => s.tokens), 1)
  const ollamaUp = models.some(m => m.provider === 'ollama')

  return (
    <div style={{
      position: 'fixed', top: 22, bottom: 4, left: 0, right: 0, zIndex: 500,
      display: 'grid',
      // Mobile collapses the tactical grid into one scrollable column;
      // panel order follows source order (uplink → matrix → budget → banks).
      gridTemplateColumns: isMobile ? 'minmax(0, 1fr)' : '218px 1fr 232px',
      gridTemplateRows: isMobile ? 'auto' : '34px 1fr 158px',
      overflowY: isMobile ? 'auto' : undefined,
      gap: 8, padding: 10,
      background: 'rgba(4, 9, 12, 0.5)',
      backdropFilter: 'blur(6px)',
      WebkitBackdropFilter: 'blur(6px)',
      animation: 'fadeIn 0.18s ease',
    }}>

      {/* ── Title plate — "PERIODIC 56A." convention ─────────────────────── */}
      <div className="hb-head-light" style={{ gridColumn: '1 / -1', minHeight: 0, gap: '0.7rem' }}>
        <span style={{ fontSize: '0.82rem' }}>SYSTEMS 56A.</span>
        <span className="hb-hide-sm" style={{
          fontFamily: MONO, fontSize: '0.56rem', letterSpacing: '0.1em',
          color: '#41606e', textTransform: 'none',
        }}>
          MODE / 3Dx. 78A
        </span>
        <span style={{ flex: 1 }} />
        <span className="hb-hide-sm" style={{ fontFamily: MONO, fontSize: '0.56rem', color: '#41606e', textTransform: 'none' }}>
          ver 17 · MK VI
        </span>
        <span style={{ width: 7, height: 14, background: 'linear-gradient(180deg, #e8a850, #c98a35)' }} />
        <button
          onClick={onClose}
          title="Close (Esc)"
          style={{
            border: 'none', background: 'transparent', cursor: 'pointer',
            color: '#2c4350', display: 'flex', alignItems: 'center', padding: '0 2px',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      {/* ── Left column — uplink telemetry + network nodes ───────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <Panel title="UPLINK_STATUS" style={{ flexShrink: 0, animation: 'hbRise 0.4s 0.05s ease both' }}>
          <KV k="LINK" v={health.online ? 'ONLINE' : 'DENY'}
              color={health.online ? 'var(--hb-green)' : 'var(--hb-red)'} />
          <KV k="HOST" v={config.apiBase.replace(/^https?:\/\//, '')} alt />
          <KV k="RTT" v={health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
              color={health.latencyMs != null && health.latencyMs < 400 ? 'var(--hb-green)' : 'var(--hb-amber)'} />
          <KV k="TOOLS REG." v={health.tools ?? '--'} alt />
          <KV k="SESSIONS" v={String(state.sessions.length).padStart(3, '0')} />
          <KV k="BUDGET MODE" v={budgetMode ? 'ENGAGED' : 'OFF'}
              color={budgetMode ? 'var(--hb-amber)' : 'var(--hb-text-faint)'} alt />
          <KV k="OLLAMA NODE" v={ollamaUp ? 'LOCAL ACTIVE' : 'NOT DETECTED'}
              color={ollamaUp ? 'var(--hb-green)' : 'var(--hb-text-faint)'} />
        </Panel>

        <Panel title="NETWORK_NODES" style={{ flex: 1, animation: 'hbRise 0.4s 0.12s ease both' }}>
          {servers.length === 0 ? (
            <p style={{ fontFamily: MONO, fontSize: '0.58rem', letterSpacing: '0.14em', color: '#2e5260', padding: '0.3rem 0.35rem' }}>
              // NO NODES
            </p>
          ) : servers.map((c, i) => (
            <div key={c.server} style={{
              display: 'flex', flexDirection: 'column', gap: 1,
              padding: '0.26rem 0.35rem',
              background: i % 2 ? 'rgba(54,171,202,0.04)' : 'transparent',
              borderLeft: `2px solid ${!c.connected ? 'rgba(200,74,58,0.55)' : c.active ? 'rgba(54,171,202,0.55)' : 'rgba(95,165,188,0.18)'}`,
            }}>
              <span style={{
                fontFamily: UI, fontSize: '0.66rem', fontWeight: 700,
                letterSpacing: '0.1em', textTransform: 'uppercase',
                color: c.connected ? '#9bbac5' : '#7d6660',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {c.label}
              </span>
              <span style={{ fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.06em',
                color: !c.connected ? 'var(--hb-red)' : c.active ? 'var(--hb-cyan)' : '#3a6472' }}>
                {!c.connected ? 'MEDIA DISCONNECTED' : c.active ? `LINKED · ${c.tools} TOOLS` : 'STANDBY'}
              </span>
            </div>
          ))}
        </Panel>
      </div>

      {/* ── Center — the routing matrix ──────────────────────────────────── */}
      <Panel title="CORE ROUTING_MATRIX" pad={false}
        right={<span style={{ fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.08em', textTransform: 'none' }}>
          {models.length} CORES · {servers.length} SHARDS
        </span>}
        style={{ animation: 'hbRise 0.45s 0.08s ease both' }}
      >
        <div style={{ position: 'relative', padding: '0.7rem 0.75rem', minHeight: '100%' }}>
          {/* faint oversized designation — "B.12" */}
          <span className="hb-num-thin" aria-hidden style={{
            position: 'absolute', right: 14, bottom: 4,
            fontSize: '5.4rem', color: 'rgba(95,204,230,0.05)', pointerEvents: 'none',
          }}>
            B.12
          </span>

          {models.length === 0 && (
            <div style={{
              width: 120, padding: '0.8rem 0',
              border: '1px solid rgba(95,165,188,0.3)',
              background: 'rgba(29,93,112,0.25)',
              textAlign: 'center',
              fontFamily: UI, fontSize: '0.72rem', fontWeight: 700,
              letterSpacing: '0.2em', color: '#46818f',
            }}>
              NOT FOUND
            </div>
          )}

          {providers.map(p => (
            <div key={p} style={{ marginBottom: '0.85rem' }}>
              <p style={{
                fontFamily: MONO, fontSize: '0.55rem', letterSpacing: '0.22em',
                color: 'var(--hb-cyan)', marginBottom: '0.35rem',
              }}>
                {'>>:'} {PROVIDER_TAGS[p] ?? p.toUpperCase()}_
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {models.filter(m => (m.provider ?? 'anthropic') === p).map((m, i) => (
                  <ModelTile key={m.id} m={m} idx={i}
                    active={settings.model === m.id}
                    onSelect={() => update({ model: m.id })} />
                ))}
              </div>
            </div>
          ))}

          {servers.length > 0 && (
            <div>
              <p style={{
                fontFamily: MONO, fontSize: '0.55rem', letterSpacing: '0.22em',
                color: 'var(--hb-amber)', marginBottom: '0.35rem',
              }}>
                {'>>:'} CONTEXT SHARDS_ MCP TOOLSETS
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {servers.map((c, i) => (
                  <ToolTile key={c.server} c={c} idx={i} onToggle={() => toggleServer(c)} />
                ))}
              </div>
            </div>
          )}
        </div>
      </Panel>

      {/* ── Right column — token budget + response trace ─────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <Panel title="TOKEN_BUDGET" style={{ flexShrink: 0, animation: 'hbRise 0.4s 0.16s ease both' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '0.2rem 0.1rem 0.45rem' }}>
            <span className="hb-num-thin" style={{ fontSize: '2.6rem', color: gaugeColor }}>
              {pct}<span style={{ fontSize: '1.1rem' }}>%</span>
            </span>
            <span style={{
              fontFamily: UI, fontSize: '0.56rem', fontWeight: 700,
              letterSpacing: '0.16em', color: '#3a6472', lineHeight: 1.5,
            }}>
              PREFIX<br/>SATURATION
            </span>
          </div>
          <SegBar pct={pct} color={gaugeColor} />
          <p style={{
            fontFamily: MONO, fontSize: '0.52rem', letterSpacing: '0.06em',
            color: '#3a6472', margin: '0.35rem 0 0.6rem',
          }}>
            ~{budgetTokens.used.toLocaleString()} / {budgetTokens.limit.toLocaleString()} ITPM
          </p>
          {[...servers].sort((a, b) => b.tokens - a.tokens).slice(0, 5).map(s => (
            <div key={s.server} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
              <span style={{
                width: 64, flexShrink: 0, fontFamily: MONO, fontSize: '0.5rem',
                color: s.active ? '#5d8693' : '#33505b', textTransform: 'uppercase',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {s.server}
              </span>
              <span style={{ flex: 1, height: 4, background: 'rgba(95,165,188,0.1)' }}>
                <span style={{
                  display: 'block', height: '100%',
                  width: `${Math.round((s.tokens / maxServerTokens) * 100)}%`,
                  background: s.active ? 'rgba(95,204,230,0.6)' : 'rgba(95,165,188,0.25)',
                }} />
              </span>
              <span style={{ width: 30, flexShrink: 0, textAlign: 'right', fontFamily: MONO, fontSize: '0.5rem', color: '#3a6472' }}>
                {(s.tokens / 1000).toFixed(1)}K
              </span>
            </div>
          ))}
        </Panel>

        <Panel title="RESPONSE_TRACE" style={{ flex: 1, animation: 'hbRise 0.4s 0.22s ease both' }}>
          <Spark samples={rtt} />
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            fontFamily: MONO, fontSize: '0.52rem', color: '#3a6472', marginTop: 4,
          }}>
            <span>RTT / 4s PROBE</span>
            <span style={{ color: '#5fcce6' }}>
              {health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
            </span>
          </div>
        </Panel>
      </div>

      {/* ── Bottom band — knowledge bank: what SPEDA knows about the owner ─ */}
      <Panel title="DATA_BANKS // KNOWLEDGE" light pad={false}
        right={<span style={{ fontFamily: MONO, fontSize: '0.54rem', letterSpacing: '0.08em', color: '#41606e', textTransform: 'none' }}>
          {memFiles.length} FILES
        </span>}
        style={{ gridColumn: '1 / -1', animation: 'hbRise 0.45s 0.26s ease both' }}
      >
        {memFiles.length === 0 ? (
          <div style={{
            margin: '0.8rem', width: 160, padding: '0.8rem 0',
            border: '1px solid rgba(95,165,188,0.3)', background: 'rgba(29,93,112,0.25)',
            textAlign: 'center', fontFamily: UI, fontSize: '0.72rem', fontWeight: 700,
            letterSpacing: '0.2em', color: '#46818f',
          }}>
            NO RECORDS
          </div>
        ) : (
          <div style={{ display: 'flex', height: isMobile ? 280 : '100%', minHeight: 0 }}>
            {/* File rail — one entry per memory file */}
            <div style={{
              width: 142, flexShrink: 0, overflowY: 'auto',
              borderRight: '1px solid rgba(95,165,188,0.14)',
            }}>
              {memFiles.map(f => {
                const name = (f.path.split('/').pop() || f.path).replace(/\.md$/, '').toUpperCase()
                const sel = f.path === memPath
                return (
                  <button
                    key={f.path}
                    onClick={() => setMemPath(f.path)}
                    style={{
                      width: '100%', padding: '0.3rem 0.55rem',
                      display: 'flex', alignItems: 'center', gap: 6,
                      border: 'none', cursor: 'pointer', textAlign: 'left',
                      borderLeft: sel ? '2px solid var(--hb-amber)' : '2px solid transparent',
                      background: sel ? 'rgba(217,156,68,0.1)' : 'transparent',
                      fontFamily: MONO, fontSize: '0.58rem', letterSpacing: '0.08em',
                      color: sel ? '#f2b75c' : '#46818f',
                      transition: 'background 0.1s, color 0.1s, border-color 0.1s',
                    }}
                  >
                    <span style={{ color: sel ? 'var(--hb-amber)' : '#2e5260' }}>▸</span>
                    {name}
                  </button>
                )
              })}
            </div>

            {/* Fact readout — the selected file's extracted knowledge */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '0.35rem 0.7rem 0.5rem' }}>
              {(() => {
                const file = memFiles.find(f => f.path === memPath)
                if (!file) return null
                const lines = file.content.split('\n').map(l => l.trim()).filter(Boolean)
                if (lines.length === 0) return (
                  <p style={{ fontFamily: MONO, fontSize: '0.58rem', letterSpacing: '0.14em', color: '#2e5260', padding: '0.3rem 0' }}>
                    // EMPTY — SPEDA HAS NOT WRITTEN HERE YET
                  </p>
                )
                return (
                  <>
                    {file.updated_at && (
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}>
                        <span style={{ fontFamily: MONO, fontSize: '0.5rem', color: '#33505b' }}>
                          LAST WRITE {fmtDate(file.updated_at)}
                        </span>
                      </div>
                    )}
                    {lines.map((line, i) => {
                      if (line.startsWith('#')) {
                        return (
                          <div key={i} style={{
                            fontFamily: UI, fontSize: '0.66rem', fontWeight: 700,
                            letterSpacing: '0.18em', textTransform: 'uppercase',
                            color: 'var(--hb-cyan)', margin: '0.45rem 0 0.15rem',
                          }}>
                            {line.replace(/^#+\s*/, '')}
                          </div>
                        )
                      }
                      const isFact = /^[-*]\s/.test(line)
                      const isNote = /^_.*_$/.test(line)
                      return (
                        <div key={i} style={{
                          display: 'flex', gap: 7, padding: '0.12rem 0',
                          fontFamily: "'SamsungOne','Inter',sans-serif",
                          fontSize: '0.74rem', lineHeight: 1.45,
                          color: isNote ? '#41606e' : '#9bbac5',
                          fontStyle: isNote ? 'italic' : 'normal',
                        }}>
                          {isFact && <span style={{ color: 'var(--hb-cyan)', fontSize: '0.62em', lineHeight: 2 }}>▸</span>}
                          <span>{line.replace(/^[-*]\s+/, '').replace(/^_|_$/g, '')}</span>
                        </div>
                      )
                    })}
                  </>
                )
              })()}
            </div>
          </div>
        )}
      </Panel>
    </div>
  )
}
