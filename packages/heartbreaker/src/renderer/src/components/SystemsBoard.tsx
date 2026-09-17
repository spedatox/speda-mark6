// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useHealth } from '../lib/useHealth'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchModels, getConnections, getBudgetMode, setConnection, fetchMemoryFiles, fetchAgentModels, pinAgentModel, fetchLegionModels, pinLegionModel } from '../lib/api'
import type { ConnectionInfo, MemoryFileInfo, AgentModelInfo, LegionModelInfo } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'
import { agentColor, monogram } from '../lib/agents'
import AgentModelPicker from './AgentModelPicker'
import GlassSelect from './GlassSelect'
import { Skeleton, SkeletonList } from './Skeleton'
import MemoryExplorerModal from './MemoryExplorerModal'
import MemoryEditorModal from './MemoryEditorModal'
import GlassFolderIcon from './GlassFolderIcon'

/**
 * SYSTEMS BOARD — the deep view of the deck's instrumentation.
 *
 * What is on it:
 *   Model routing matrix  → tiles per provider; clicking one routes the agent.
 *                           Exactly one tile is ever lit, and it takes the
 *                           accent — this is a state readout, not a palette.
 *   Uplink + network nodes→ /health telemetry and the per-server token load
 *   Token budget          → the live ITPM prompt-prefix budget
 *   RTT trace             → sparkline from the /health probe
 *   Data banks            → big fluid glass folder categories
 *
 * Every value on this board comes from the backend. Nothing is set dressing —
 * the old header's "SYSTEMS 56A. / MODE 3Dx. 78A / ver 17" instrument markings
 * were invented and have been removed for exactly that reason.
 */


const MONO = "var(--font-mono)"
const UI = "'Rajdhani', sans-serif"

interface MemoryCategoryDef {
  id: string
  name: string
  subtitle: string
  path: string
  badge: 'dossier' | 'finance' | 'wellness' | 'projects' | 'social' | 'academic' | 'cybersec' | 'ops'
  color: string
  match: (path: string) => boolean
}

const MEMORY_CATEGORIES: MemoryCategoryDef[] = [
  {
    id: 'dossier',
    name: 'KİMLİK & DOSSIER',
    subtitle: 'Kişisel profil, roller ve ilkeler',
    path: '',
    badge: 'dossier',
    color: '#5fcce6',
    match: p => {
      const rel = p.replace(/^\/memories\//, '')
      return !rel.includes('/') || rel.includes('dossier') || rel.includes('owner') || rel.includes('current') || rel.includes('patterns')
    },
  },
  {
    id: 'finance',
    name: 'FİNANS & LEDGER',
    subtitle: 'Varlıklar, bütçe ve ledger',
    path: 'finance',
    badge: 'finance',
    color: '#f2b75c',
    match: p => p.startsWith('/memories/finance'),
  },
  {
    id: 'wellness',
    name: 'SAĞLIK & ATHLETE',
    subtitle: 'Antrenman, biyometri ve sağlık',
    path: 'wellness',
    badge: 'wellness',
    color: '#51cf66',
    match: p => p.startsWith('/memories/wellness'),
  },
  {
    id: 'projects',
    name: 'PROJELER & KOD',
    subtitle: 'Aktif repo, mimari ve sistemler',
    path: 'projects',
    badge: 'projects',
    color: '#4dabf7',
    match: p => p.startsWith('/memories/projects'),
  },
  {
    id: 'social',
    name: 'SOSYAL & NETWORK',
    subtitle: 'İletişim ve profesyonel ağ',
    path: 'social',
    badge: 'social',
    color: '#e599f7',
    match: p => p.startsWith('/memories/social'),
  },
  {
    id: 'academic',
    name: 'AKADEMİK & KPSS',
    subtitle: 'Dersler, testler ve çalışma',
    path: 'academic',
    badge: 'academic',
    color: '#ffd43b',
    match: p => p.startsWith('/memories/academic'),
  },
  {
    id: 'cybersec',
    name: 'SİBER GÜVENLİK',
    subtitle: 'Pentest, ağ güvenliği ve audit',
    path: 'cybersec',
    badge: 'cybersec',
    color: '#ff6b6b',
    match: p => p.startsWith('/memories/cybersec'),
  },
  {
    id: 'ops',
    name: 'SİSTEM & TELEMETRİ',
    subtitle: 'Loglar, operasyon ve telemetri',
    path: 'ops',
    badge: 'ops',
    color: '#20c997',
    match: p => p.startsWith('/memories/ops') || p.includes('.audit') || p.includes('log.md'),
  },
]

const PROVIDER_TAGS: Record<string, string> = {
  anthropic: 'ANTHROPIC', openai: 'OPENAI', gemini: 'GEMINI', zai: 'Z.AI · GLM', deepseek: 'DEEPSEEK', ollama: 'OLLAMA · LOCAL',
}

function symbolOf(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 .]/g, ' ').trim().split(/\s+/)
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
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
  // The header is a LABEL, not a bar. The old filled title plate meant every
  // panel on the board opened with a saturated stripe, and eight of those read
  // as eight alarms. A panel should announce itself once, quietly, and let its
  // contents be the loud part. `light` survives as an emphasis hint only.
  return (
    <section className="hb-holo" style={{
      position: 'relative', display: 'flex', flexDirection: 'column',
      minHeight: 0, minWidth: 0,
      overflow: 'hidden',
      padding: '16px 18px',
      ...style,
    }}>
      <header style={{
        flexShrink: 0, display: 'flex', alignItems: 'baseline',
        justifyContent: 'space-between', gap: 10, marginBottom: 12,
      }}>
        <span style={{
          fontFamily: UI, fontSize: '0.84rem', fontWeight: 600,
          letterSpacing: '0.16em', textTransform: 'uppercase',
          color: light ? 'var(--hb-text)' : 'var(--hb-text-dim)',
        }}>{title}</span>
        {right}
      </header>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0, marginInline: pad ? 0 : '-18px' }}>
        {children}
      </div>
    </section>
  )
}

/* ── Telemetry key/value row — the "IPv4 Adress: DENY" list style ─────────── */
// `alt` is still accepted (callers pass it) but no longer read — see below.
function KV({ k, v, color }: { k: string; v: React.ReactNode; color?: string; alt?: boolean }) {
  return (
    // `alt` used to paint every other row with an accent wash — zebra striping
    // that fought the glass underneath it. The rows are legible on their own;
    // the prop stays so call sites need no edit, and now does nothing.
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10,
      padding: '5px 0',
      fontSize: '0.875rem',
    }}>
      <span style={{ color: 'var(--hb-text-dim)', whiteSpace: 'nowrap' }}>{k}</span>
      <span style={{
        color: color || 'var(--hb-text)', textAlign: 'right',
        fontVariantNumeric: 'tabular-nums',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{v}</span>
    </div>
  )
}

/* ── Model tile — periodic-table element; click routes the active model ────── */
function ModelTile({ m, active, onSelect }: {
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
      // Only the ROUTED model is coloured, and it takes the accent — the tile
      // grid is a state readout, so exactly one tile should ever be lit. It was
      // amber-on-orange before, which read as a warning rather than a selection.
      style={{
        width: 76, height: 76, position: 'relative', flexShrink: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: 4,
        cursor: 'pointer',
        border: `1px solid ${active
          ? 'rgba(var(--hb-accent-rgb),0.5)'
          : hover ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
        background: active
          ? 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.24), rgba(var(--hb-accent-rgb),0.08))'
          : hover
          ? 'var(--glass-sheen-hi)'
          : 'var(--glass-sheen)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: active
          ? 'inset 0 1px 0 0 rgba(255,255,255,0.2), 0 0 20px rgba(var(--hb-accent-rgb),0.2)'
          : 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
        transition: 'border-color 0.12s, background 0.12s, box-shadow 0.12s',
      }}
    >
      <span style={{
        fontFamily: UI, fontWeight: 700, fontSize: '1rem', lineHeight: 1,
        color: active ? '#eaf3f7' : 'var(--hb-text)',
      }}>
        {symbolOf(m.name)}
      </span>
      <span style={{
        maxWidth: 66,
        fontSize: '0.625rem',
        color: active ? 'var(--hb-cyan-bright)' : 'var(--hb-text-faint)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {m.name}
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
          engaged ? (hover ? 'rgba(var(--hb-accent-rgb),0.75)' : 'rgba(var(--hb-accent-rgb),0.5)') :
          'rgba(var(--hb-accent-rgb),0.2)'
        }`,
        background: offline
          ? 'rgba(86, 34, 30, 0.25)'
          : engaged
          ? (hover ? 'rgba(var(--hb-accent-rgb), 0.28)' : 'rgba(var(--hb-cyan-dim-rgb), 0.18)')
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
        color: offline ? '#d98a7a' : engaged ? '#bfe6f2' : 'var(--hb-icon-bright)',
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

/* The segmented block meter that used to sit under the big percentage was
   removed with the FUI language — the deck's gauges are continuous rounded
   bars now, and nothing had called it since. */

/* ── RTT trace — minimalist line-art sparkline from real health probes ─────── */
function Spark({ samples }: { samples: number[] }) {
  const W = 196, H = 56
  if (samples.length < 2) {
    return (
      <div className="hb-skeleton-group" style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: H, padding: '0 2px' }}>
        {Array.from({ length: 18 }).map((_, i) => (
          <Skeleton
            key={i}
            width="100%"
            height={`${22 + ((i * 11) % 6) * 11}%`}
            radius={1}
            style={{ flex: 1, ['--hb-skeleton-delay' as string]: `${i * 0.03}s` }}
          />
        ))}
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
          stroke="rgba(var(--hb-accent-rgb),0.12)" strokeWidth={1} strokeDasharray="2 4" />
      ))}
      <polyline points={pts} fill="none" stroke="var(--hb-cyan-bright)" strokeWidth={1.2}
        style={{ filter: 'drop-shadow(0 0 3px rgba(var(--hb-cyan-bright-rgb),0.5))' }} />
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
  const [banksWide, setBanksWide] = useState(false)
  const [showExplorer, setShowExplorer] = useState(false)
  const [explorerInitialPath, setExplorerInitialPath] = useState<string | null>(null)
  const [editorFile, setEditorFile] = useState<MemoryFileInfo | null>(null)
  const [agentInfos, setAgentInfos] = useState<AgentModelInfo[]>([])
  const [legionInfos, setLegionInfos] = useState<LegionModelInfo[]>([])
  // False until the boot batch below settles — without it, an agent switch
  // (this re-fetches on every `config` change) briefly reads as "no servers
  // connected" / "no model catalogue" instead of loading.
  const [boardLoaded, setBoardLoaded] = useState(false)

  const loadConns = () => getConnections(config).then(r => {
    setServers(r.servers)
    setBudgetTokens({ used: r.active_tool_tokens, limit: r.itpm_limit })
  }).catch(() => {})

  useEffect(() => {
    setBoardLoaded(false)
    Promise.all([
      fetchModels(config).then(setModels).catch(() => {}),
      fetchAgentModels(config).then(setAgentInfos),
      fetchLegionModels(config).then(setLegionInfos),
      getBudgetMode(config).then(setBudgetMode).catch(() => {}),
      fetchMemoryFiles(config).then(setMemFiles).catch(() => {}),
      loadConns(),
    ]).finally(() => setBoardLoaded(true))
  }, [config]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (health.latencyMs != null) {
      const v = health.latencyMs
      setRtt(prev => [...prev.slice(-31), v])
    }
  }, [health.latencyMs])

  useEffect(() => {
    // Esc retracts the extended knowledge bank first; a second Esc closes the board.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (banksWide) setBanksWide(false)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, banksWide])

  const applyFresh = (f: MemoryFileInfo) => {
    setMemFiles(prev => prev.map(x => (x.path === f.path ? { ...x, ...f } : x)))
  }

  const toggleServer = async (c: ConnectionInfo) => {
    setServers(ss => ss.map(s => s.server === c.server ? { ...s, active: !c.active } : s))
    await setConnection(config, c.server, !c.active)
    loadConns()
  }

  const providers = Array.from(new Set(models.map(m => m.provider ?? 'anthropic')))
  const activeProvider = models.find(m => m.id === settings.model)?.provider ?? 'anthropic'

  // Provider groups collapse, as they do on Speda GO. The catalogue runs to a
  // hundred-odd models across seven providers, and a live Ollama or NVIDIA
  // listing alone can be dozens of rows — enough to bury the one model the
  // owner opened the board to change. Only the group holding the active model
  // is open; the rest are one click away. Groups toggle independently (the
  // mobile behaviour) rather than as a single-open accordion, so two providers
  // can be compared side by side.
  const [openProviders, setOpenProviders] = useState<Set<string>>(new Set())
  useEffect(() => { setOpenProviders(new Set([activeProvider])) }, [activeProvider])
  const toggleProvider = (p: string): void => setOpenProviders(prev => {
    const next = new Set(prev)
    next.has(p) ? next.delete(p) : next.add(p)
    return next
  })
  const pct = Math.round((budgetTokens.used / Math.max(budgetTokens.limit, 1)) * 100)
  const gaugeColor = pct > 100 ? '#c84a3a' : pct > 70 ? '#f2b75c' : 'var(--hb-cyan-bright)'
  const maxServerTokens = Math.max(...servers.map(s => s.tokens), 1)
  const ollamaUp = models.some(m => m.provider === 'ollama')

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 500,
      display: 'grid',
      // Mobile collapses the tactical grid into one scrollable column;
      // panel order follows source order (uplink → matrix → budget → banks).
      // The deck draws these rails at a fixed 300/268, which only works at its
      // own 1920. Fixed rails on a 1400 window ate the middle column down to
      // ~230px and folded the routing matrix into a single stack. So they are
      // ranges: the rails give their width back to the matrix as the window
      // narrows, and the matrix keeps a floor of its own.
      gridTemplateColumns: isMobile
        ? 'minmax(0, 1fr)'
        : 'minmax(220px, 300px) minmax(340px, 1fr) minmax(200px, 268px)',
      // Bottom track is fr-based so EXTEND can animate it: the knowledge bank
      // rises to ~80% of the board while the tactical grid compresses upward.
      gridTemplateRows: isMobile ? 'auto' : `44px 1fr ${banksWide ? '4.4fr' : '230px'}`,
      transition: 'grid-template-rows 0.5s cubic-bezier(0.22, 0.9, 0.3, 1)',
      overflowY: isMobile ? 'auto' : undefined,
      gap: 16, padding: '10px 24px 24px',
      background: 'rgba(5, 7, 10, 0.55)',
      backdropFilter: 'blur(8px)',
      WebkitBackdropFilter: 'blur(8px)',
      animation: 'fadeIn 0.18s ease',
    }}>

      {/* ── Board header ───────────────────────────────────────────────────
          The old plate read "SYSTEMS 56A. / MODE / 3Dx. 78A / ver 17 · MK VI"
          — invented instrument markings on a board whose whole point is that
          every number on it is real. Replaced with what is actually true: what
          this board is, and the live registered-tool count. */}
      <div style={{
        gridColumn: '1 / -1', minHeight: 0,
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <span className="hb-tile" style={{
          width: 38, height: 38, flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.2), rgba(var(--hb-accent-rgb),0.06))',
          border: '1px solid rgba(var(--hb-accent-rgb),0.32)',
        }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan-bright)" strokeWidth="1.5" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>
            <rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>
          </svg>
        </span>
        <span style={{
          fontFamily: UI, fontSize: '1.32rem', fontWeight: 600,
          letterSpacing: '0.06em', color: 'var(--hb-text)',
        }}>
          Systems
        </span>
        <span className="hb-hide-sm" style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>
          Model routing · toolsets · bandwidth
        </span>
        <span style={{ flex: 1 }} />
        {health.tools != null && (
          <span className="glass-round" style={{
            display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
            height: 30, padding: '0 14px',
            background: 'rgba(79, 163, 119, 0.1)',
            border: '1px solid rgba(79, 163, 119, 0.3)',
            fontSize: '0.8125rem', color: '#8fdcb3',
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: 'var(--hb-green)', boxShadow: '0 0 6px var(--hb-green)',
            }} />
            {health.tools} tools registered
          </span>
        )}
        <button
          className="hb-btn hb-tile"
          onClick={onClose}
          title="Close (Esc)"
          style={{ width: 38, height: 38, flexShrink: 0 }}
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>

      {/* ── Left column — uplink telemetry + network nodes ───────────────── */}
      {/* overflow hidden: when the knowledge bank extends, the compressed row
          clips these panels instead of letting them spill over the bank */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minHeight: 0, overflow: 'hidden' }}>
        <Panel title="Uplink" style={{ flexShrink: 0, animation: 'hbRise 0.4s 0.05s ease both' }}>
          <KV k="Link" v={health.online ? 'Online' : 'Offline'}
              color={health.online ? 'var(--hb-green)' : 'var(--hb-red)'} />
          <KV k="Host" v={config.apiBase.replace(/^https?:\/\//, '')} alt />
          <KV k="RTT" v={health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
              color={health.latencyMs != null && health.latencyMs < 400 ? 'var(--hb-green)' : 'var(--hb-amber)'} />
          <KV k="Tools registered" v={health.tools ?? '--'} alt />
          <KV k="Sessions" v={String(state.sessions.length).padStart(3, '0')} />
          <KV k="Budget mode" v={budgetMode ? 'Engaged' : 'Off'}
              color={budgetMode ? 'var(--hb-amber)' : 'var(--hb-text-faint)'} alt />
          <KV k="Ollama node" v={ollamaUp ? 'Local active' : 'Not detected'}
              color={ollamaUp ? 'var(--hb-green)' : 'var(--hb-text-faint)'} />
          <KV k="Forge" v="Legion execution" color="var(--hb-green)" alt />
        </Panel>

        <Panel title="Connected servers" style={{ flex: 1, animation: 'hbRise 0.4s 0.12s ease both' }}>
          {!boardLoaded ? (
            <SkeletonList rows={3} mark={false} />
          ) : servers.length === 0 ? (
            <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', padding: '4px 0' }}>
              No servers connected
            </p>
          ) : servers.map((c, i) => (
            <div key={c.server} style={{
              display: 'flex', flexDirection: 'column', gap: 1,
              padding: '0.26rem 0.35rem',
              background: i % 2 ? 'rgba(var(--hb-accent-rgb),0.04)' : 'transparent',
              borderLeft: `2px solid ${!c.connected ? 'rgba(200,74,58,0.55)' : c.active ? 'rgba(var(--hb-accent-rgb),0.55)' : 'rgba(var(--hb-accent-rgb),0.18)'}`,
            }}>
              <span style={{
                fontFamily: UI, fontSize: '0.66rem', fontWeight: 700,
                letterSpacing: '0.1em', textTransform: 'uppercase',
                color: c.connected ? 'var(--hb-text-dim)' : '#7d6660',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {c.label}
              </span>
              <span style={{ fontSize: '0.8125rem',
                color: !c.connected ? 'var(--hb-red)' : c.active ? 'var(--hb-cyan)' : 'var(--hb-icon)' }}>
                {!c.connected ? 'disconnected' : c.active ? `linked · ${c.tools} tools` : 'standby'}
              </span>
            </div>
          ))}
        </Panel>
      </div>

      {/* ── Center — the routing matrix ──────────────────────────────────── */}
      <Panel title="Model routing matrix" pad={false}
        right={<span style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
          {providers.length} providers · {models.length} models
        </span>}
        style={{ animation: 'hbRise 0.45s 0.08s ease both' }}
      >
        {/* The "B.12" watermark that used to sit bottom-right is gone: an
            oversized invented designation is the exact set dressing this board
            is not allowed to carry. */}
        <div style={{ position: 'relative', padding: '4px 2px', minHeight: '100%' }}>
          {!boardLoaded ? (
            <SkeletonList rows={3} />
          ) : models.length === 0 && (
            <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', padding: '4px 0' }}>
              No model catalogue — no provider keys are configured on this server.
            </p>
          )}

          {providers.map(p => {
            const group = models.filter(m => (m.provider ?? 'anthropic') === p)
            const open = openProviders.has(p)
            const holdsActive = group.some(m => m.id === settings.model)
            return (
              <div key={p} style={{ marginBottom: '0.85rem' }}>
                <p
                  onClick={() => toggleProvider(p)}
                  title={open ? `Collapse ${p}` : `Expand ${p} — ${group.length} models`}
                  // The provider that holds the routed model takes the accent;
                  // the rest sit back. Deck label scale, not 0.55rem mono.
                  style={{
                    fontFamily: UI, fontSize: '0.78rem', fontWeight: 600,
                    letterSpacing: '0.12em', textTransform: 'uppercase',
                    color: holdsActive ? 'var(--hb-cyan)' : 'var(--hb-text-dim)',
                    marginBottom: 10, cursor: 'pointer', userSelect: 'none',
                    display: 'flex', alignItems: 'center', gap: 7,
                  }}
                >
                  <span style={{
                    display: 'inline-block', width: '0.5rem',
                    transform: open ? 'rotate(90deg)' : 'none',
                    transition: 'transform 120ms ease',
                  }}>{'›'}</span>
                  {PROVIDER_TAGS[p] ?? p.toUpperCase()}
                  <span style={{ color: 'var(--hb-text-faint)', fontWeight: 500 }}>{group.length}</span>
                </p>
                {open && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                    {group.map((m, i) => (
                      <ModelTile key={m.id} m={m} idx={i}
                        active={settings.model === m.id}
                        onSelect={() => update({ model: m.id })} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {servers.length > 0 && (
            <div>
              <p style={{
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 600,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                color: 'var(--hb-text-dim)', marginBottom: 10,
              }}>
                MCP toolsets
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {servers.map((c, i) => (
                  <ToolTile key={c.server} c={c} idx={i} onToggle={() => toggleServer(c)} />
                ))}
              </div>
            </div>
          )}

          {/* Per-agent model pins — which core each agent runs on */}
          {agentInfos.length > 0 && (
            <div style={{ marginTop: '0.85rem' }}>
              <p style={{
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 600,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                color: 'var(--hb-text-dim)', marginBottom: 10,
              }}>
                Per-agent model routing
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {agentInfos.map(info => (
                  // A pin card is a glass row, not a coloured box. The agent's
                  // colour lands on a 6px dot and its monogram — enough to find
                  // your agent, without eight saturated rims competing across
                  // the grid the way the old per-agent borders did.
                  <div key={info.agent_id} className="hb-glass-sm" style={{
                    width: 176, padding: '10px 12px',
                    display: 'flex', flexDirection: 'column', gap: 8,
                    border: `1px solid ${info.override ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
                    background: 'var(--glass-sheen), var(--glass-fill)',
                    backdropFilter: 'var(--hb-holo-blur)',
                    WebkitBackdropFilter: 'var(--hb-holo-blur)',
                  }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                        background: agentColor(info.agent_id),
                        boxShadow: `0 0 6px ${agentColor(info.agent_id)}`,
                      }} />
                      <span style={{
                        fontFamily: UI, fontWeight: 700, fontSize: '0.78rem',
                        color: agentColor(info.agent_id), letterSpacing: '0.08em',
                      }}>
                        {monogram(info.agent_id)}
                      </span>
                      <span style={{
                        fontSize: '0.8125rem', color: 'var(--hb-text)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {info.name}
                      </span>
                    </span>
                    <AgentModelPicker
                      large
                      info={info}
                      models={models}
                      onPin={async m => {
                        const infos = await pinAgentModel(config, info.agent_id, m)
                        if (infos.length) setAgentInfos(infos)
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Legion worker pins — which core each legionnaire type runs on.
              Legionnaires have no profile of their own: unpinned, their model is
              derived from effort against whichever agent deployed them, so the
              "default" option names that rule rather than a model. */}
          {legionInfos.length > 0 && (
            <div style={{ marginTop: '0.85rem' }}>
              <p style={{
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 600,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                color: 'var(--hb-text-dim)', marginBottom: 10,
              }}>
                Per-worker model routing (the Legion)
              </p>
              {legionInfos[0].deployment_pin && (
                <p style={{
                  fontSize: '0.8125rem',
                  color: 'var(--hb-cyan-bright)', marginBottom: 10,
                }}>
                  LEGION_MODEL_OVERRIDE={legionInfos[0].deployment_pin} — every worker is
                  pinned there; these selectors stay inert until it is cleared.
                </p>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {legionInfos.map(info => (
                  <div key={info.worker_id} className="hb-glass-sm" style={{
                    width: 176, padding: '10px 12px',
                    display: 'flex', flexDirection: 'column', gap: 8,
                    border: `1px solid ${info.override ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
                    background: 'var(--glass-sheen), var(--glass-fill)',
                    backdropFilter: 'var(--hb-holo-blur)',
                    WebkitBackdropFilter: 'var(--hb-holo-blur)',
                  }}>
                    <span style={{
                      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8,
                      overflow: 'hidden', whiteSpace: 'nowrap',
                    }}>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--hb-text)', textTransform: 'capitalize' }}>
                        {info.worker_id}
                      </span>
                      <span style={{ fontSize: '0.78rem', color: 'var(--hb-text-faint)', flexShrink: 0 }}>
                        {info.effort}
                      </span>
                    </span>
                    <GlassSelect
                      large
                      value={info.override ?? ''}
                      options={[
                        { value: '', label: `Effort — ${info.derived_from}` },
                        ...models.map(m => ({
                          value: m.id,
                          label: m.name,
                          group: m.provider ?? 'anthropic',
                        })),
                      ]}
                      onChange={async v => {
                        const infos = await pinLegionModel(config, info.worker_id, v || null)
                        if (infos.length) setLegionInfos(infos)
                      }}
                      tint="var(--hb-amber)"
                      active={!!info.override}
                      title={info.override
                        ? `${info.worker_id} pinned to ${info.override} — select EFFORT to restore the derived model`
                        : `${info.worker_id}: ${info.when_to_use}\nUnpinned → ${info.derived_from}`}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Panel>

      {/* ── Right column — token budget + response trace ─────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minHeight: 0, overflow: 'hidden' }}>
        <Panel title="Context budget" style={{ flexShrink: 0, animation: 'hbRise 0.4s 0.16s ease both' }}>
          {/* The deck's budget block: the figure leads, the limit sits beside
              it, one bar underneath. The old version made the PERCENTAGE the
              hero next to a stacked "PREFIX / SATURATION" caption, which is a
              derived number shouting over the real one. */}
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 9 }}>
            <span style={{
              fontFamily: UI, fontSize: '1.9rem', fontWeight: 600,
              color: gaugeColor, fontVariantNumeric: 'tabular-nums', lineHeight: 1,
            }}>
              {budgetTokens.used >= 1000
                ? `${(budgetTokens.used / 1000).toFixed(1)}k`
                : budgetTokens.used}
            </span>
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>
              / {(budgetTokens.limit / 1000).toFixed(0)}k ITPM
            </span>
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
            <span style={{ display: 'block', height: '100%', width: `${pct}%`, background: gaugeColor }} />
          </div>
          <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '10px 0 14px' }}>
            Tool definitions carried on every prompt
          </p>

          {[...servers].sort((a, b) => b.tokens - a.tokens).slice(0, 5).map(s => (
            <div key={s.server} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.875rem', marginBottom: 5 }}>
                <span style={{
                  color: s.active ? 'var(--hb-text)' : 'var(--hb-text-dim)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {s.label}
                </span>
                <span style={{ color: 'var(--hb-text-faint)', flexShrink: 0, marginLeft: 8, fontVariantNumeric: 'tabular-nums' }}>
                  {s.active ? `${(s.tokens / 1000).toFixed(1)}k tok` : 'standby'}
                </span>
              </div>
              <div style={{ height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                <span style={{
                  display: 'block', height: '100%',
                  width: s.active ? `${Math.round((s.tokens / maxServerTokens) * 100)}%` : '0%',
                  background: 'var(--hb-cyan)',
                }} />
              </div>
            </div>
          ))}
        </Panel>

        <Panel title="Response trace" style={{ flex: 1, animation: 'hbRise 0.4s 0.22s ease both' }}>
          <Spark samples={rtt} />
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginTop: 8,
          }}>
            <span>RTT · 4s probe</span>
            <span style={{ color: 'var(--hb-cyan-bright)' }}>
              {health.latencyMs != null ? `${health.latencyMs}ms` : '--'}
            </span>
          </div>
        </Panel>
      </div>

      {/* ── Bottom band — knowledge bank: what Speda knows about the owner ─ */}
      <Panel title="Memory files" light pad={false}
        right={
          <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
              {memFiles.length} files
            </span>
            <button
              onClick={() => {
                setExplorerInitialPath('')
                setShowExplorer(true)
              }}
              title="Tüm hafıza dosyalarını Windows Gezgini tarzı pencerede aç"
              className="hb-btn hb-btn-tint"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                height: 26, padding: '0 10px', fontSize: '0.75rem',
                cursor: 'pointer',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="#d29922" stroke="none">
                <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
              </svg>
              <span>Dosya Gezgini</span>
            </button>
            <button
              onClick={() => setBanksWide(w => !w)}
              title={banksWide ? 'Retract (Esc)' : 'Extend the knowledge bank'}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                border: 'none', background: 'transparent', cursor: 'pointer',
                padding: '0 2px',
                fontSize: '0.8125rem',
                color: banksWide ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
                transition: 'color 0.15s',
              }}
            >
              {banksWide ? 'Retract' : 'Extend'}
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                style={{ transform: banksWide ? 'rotate(180deg)' : 'none', transition: 'transform 0.3s cubic-bezier(0.22, 0.9, 0.3, 1)' }}>
                <polyline points="6 14 12 8 18 14" />
              </svg>
            </button>
          </span>
        }
        style={{ gridColumn: '1 / -1', animation: 'hbRise 0.45s 0.26s ease both' }}
      >
        <div style={{
          display: 'flex', flexDirection: 'column',
          height: isMobile ? (banksWide ? '68vh' : 320) : '100%',
          transition: 'height 0.5s cubic-bezier(0.22, 0.9, 0.3, 1)',
          overflow: 'hidden', padding: '10px 18px',
        }}>
          {/* Top Fluid Glass Header Strip */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '2px 4px 10px', flexWrap: 'wrap', gap: 8,
            borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
            marginBottom: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{
                width: 7, height: 7, borderRadius: '50%',
                background: 'var(--hb-cyan-bright)',
                boxShadow: '0 0 8px var(--hb-cyan-bright)',
              }} />
              <span style={{ fontFamily: UI, fontSize: '0.82rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--hb-cyan-bright)' }}>
                SPEDA // STARK FLUID GLASS KNOWLEDGE VAULT
              </span>
              <span style={{ fontSize: '0.78rem', color: 'var(--hb-text-faint)' }}>
                ({memFiles.length} aktif arşiv dosyası · 8 dinamik alan)
              </span>
            </div>

            <button
              onClick={() => {
                setExplorerInitialPath('')
                setShowExplorer(true)
              }}
              className="glass glass-interactive"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8,
                padding: '4px 14px', borderRadius: 20,
                fontFamily: UI, fontSize: '0.78rem', fontWeight: 700, letterSpacing: '0.08em',
                color: 'var(--hb-cyan-bright)', cursor: 'pointer',
                border: '1px solid rgba(var(--hb-cyan-bright-rgb), 0.38)',
                background: 'linear-gradient(180deg, rgba(var(--hb-cyan-bright-rgb), 0.14) 0%, rgba(var(--hb-accent-rgb), 0.04) 100%), var(--glass-fill)',
                boxShadow: '0 2px 12px rgba(0,0,0,0.35)',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
              </svg>
              <span>TÜM HAFIZA GEZGİNİ (ROOT EXPLORER) ↗</span>
            </button>
          </div>

          {/* Big Glass Folder Categories Grid */}
          <div style={{
            flex: 1, overflowY: 'auto',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: 12, alignContent: 'start',
            paddingRight: 4,
          }}>
            {!boardLoaded ? (
              <div style={{ gridColumn: '1 / -1' }}>
                <SkeletonList rows={2} mark={false} />
              </div>
            ) : (
              MEMORY_CATEGORIES.map(cat => {
                const fileCount = memFiles.filter(f => cat.match(f.path)).length
                return (
                  <div
                    key={cat.id}
                    onClick={() => {
                      setExplorerInitialPath(cat.path)
                      setShowExplorer(true)
                    }}
                    className="glass glass-interactive"
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12,
                      padding: '12px 14px', borderRadius: 12,
                      cursor: 'pointer', position: 'relative', overflow: 'hidden',
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      background: 'linear-gradient(135deg, rgba(255, 255, 255, 0.04) 0%, rgba(12, 16, 22, 0.7) 100%), var(--glass-fill)',
                      boxShadow: 'var(--glass-shadow)',
                      transition: 'all 0.22s cubic-bezier(0.2, 0.9, 0.3, 1)',
                    }}
                    onMouseEnter={e => {
                      e.currentTarget.style.borderColor = cat.color
                      e.currentTarget.style.boxShadow = `0 8px 24px ${cat.color}25, inset 0 1px 0 rgba(255,255,255,0.22)`
                      e.currentTarget.style.transform = 'translateY(-2px)'
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.1)'
                      e.currentTarget.style.boxShadow = 'var(--glass-shadow)'
                      e.currentTarget.style.transform = 'translateY(0)'
                    }}
                  >
                    <GlassFolderIcon size={48} color={cat.color} badgeIcon={cat.badge} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, marginBottom: 2 }}>
                        <span style={{
                          fontFamily: UI, fontSize: '0.86rem', fontWeight: 700,
                          letterSpacing: '0.06em', color: '#fff',
                          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        }}>
                          {cat.name}
                        </span>
                        <span style={{
                          fontSize: '0.66rem', fontWeight: 600,
                          padding: '1px 6px', borderRadius: 4,
                          background: `${cat.color}20`, color: cat.color,
                          border: `1px solid ${cat.color}38`,
                          flexShrink: 0,
                        }}>
                          {fileCount}
                        </span>
                      </div>
                      <div style={{
                        fontSize: '0.74rem', color: 'var(--hb-text-dim)',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {cat.subtitle}
                      </div>
                      <div style={{
                        fontSize: '0.68rem', color: 'var(--hb-text-faint)',
                        marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {cat.path ? `/memories/${cat.path}` : '/memories (kök)'}
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </Panel>

      {showExplorer && (
        <MemoryExplorerModal
          config={config}
          initialPath={explorerInitialPath}
          onClose={() => setShowExplorer(false)}
          onFilesChanged={() => {
            fetchMemoryFiles(config).then(files => {
              setMemFiles(files)
            }).catch(() => {})
          }}
        />
      )}

      {editorFile && (
        <MemoryEditorModal
          config={config}
          file={editorFile}
          onClose={() => setEditorFile(null)}
          onSave={updated => {
            applyFresh(updated)
            setEditorFile(null)
          }}
        />
      )}
    </div>
  )
}

