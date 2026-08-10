import { useEffect, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useHealth } from '../lib/useHealth'
import { useOnlineAgents } from '../lib/useOnlineAgents'
import { getConnections, fetchAgentModels, fetchLegionModels } from '../lib/api'
import type { ConnectionInfo, AgentModelInfo, LegionModelInfo } from '../lib/api'
import type { AppConfig } from '../lib/types'

/**
 * THE TELEMETRY COLUMN — the deck's right-hand standing panel.
 *
 * Four readings, in the order you actually want them: is the link healthy,
 * what has this session cost, where is work being routed, and what is armed.
 * The Systems Board is still the deep view — this is the glanceable one that
 * does not need opening.
 *
 * EVERY number here is real. Latency and the tool count come from /health, the
 * spend from the live turn accounting in the chat store, routing from
 * /agents/models + /agents/legion-models, the toolsets from /connections. There
 * is deliberately NO invented denominator on the spend meter: the backend has
 * no per-session token ceiling to divide by, so the bar shows the input/output
 * split of what was actually spent rather than progress toward a made-up limit.
 */

const LABEL: React.CSSProperties = {
  fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 600,
  letterSpacing: '0.16em', textTransform: 'uppercase',
  color: 'var(--hb-text-faint)', marginBottom: 12,
}

/** 847 → "847", 1_240 → "1.2k", 12_400 → "12k", 1_240_000 → "1.24M". */
function compact(n: number): string {
  if (n < 1000) return String(n)
  if (n < 999_500) {
    const k = n / 1000
    return `${k < 10 ? k.toFixed(1) : Math.round(k)}k`
  }
  const m = n / 1_000_000
  return `${m < 10 ? m.toFixed(2) : m.toFixed(1)}M`
}

/** The API base reduced to the host the deck is actually talking to. */
function hostOf(apiBase?: string): string {
  if (!apiBase) return '—'
  try { return new URL(apiBase).host } catch { return apiBase }
}

/** Model refs arrive as "provider/model-id"; the column has room for the tail. */
function shortModel(ref: string | null | undefined): string {
  if (!ref) return '—'
  return ref.includes('/') ? ref.slice(ref.lastIndexOf('/') + 1) : ref
}

function Row({ k, v, color }: { k: string; v: React.ReactNode; color?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, padding: '5px 0' }}>
      <span style={{ fontSize: 14, color: 'var(--hb-text-dim)', flexShrink: 0 }}>{k}</span>
      <span style={{
        fontSize: 14, color: color || 'var(--hb-text)',
        fontVariantNumeric: 'tabular-nums',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{v}</span>
    </div>
  )
}

/** RTT trace. Bars are the last 15 samples; the newest is lit. */
function Trace({ samples }: { samples: number[] }) {
  if (!samples.length) {
    return <div style={{ height: 34, marginTop: 10, fontSize: 12.5, color: 'var(--hb-text-faint)' }}>no samples yet</div>
  }
  const shown = samples.slice(-15)
  const peak = Math.max(...shown, 1)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 34, marginTop: 10 }}>
      {shown.map((v, i) => (
        <span
          key={i}
          title={`${v} ms`}
          style={{
            flex: 1,
            height: `${Math.max(12, (v / peak) * 100)}%`,
            background: i === shown.length - 1
              ? 'var(--hb-cyan)'
              : 'rgba(var(--hb-accent-rgb), 0.32)',
          }}
        />
      ))}
    </div>
  )
}

interface Props {
  config: AppConfig
  agentId: string
  /** Collapsed → the column unmounts entirely and the chat takes the width. */
  open: boolean
}

export default function TelemetryColumn({ config, agentId, open }: Props) {
  const { state } = useChatContext()
  const health = useHealth(config.apiBase, config.apiKey, 4000)
  const onlineAgents = useOnlineAgents(config, 8000)
  const forgeOnline = onlineAgents.some(a => a.agent_id === 'optimus')

  const [rtt, setRtt] = useState<number[]>([])
  const [servers, setServers] = useState<ConnectionInfo[]>([])
  const [agentInfos, setAgentInfos] = useState<AgentModelInfo[]>([])
  const [legionInfos, setLegionInfos] = useState<LegionModelInfo[]>([])

  useEffect(() => {
    if (!open) return
    getConnections(config).then(r => setServers(r.servers)).catch(() => {})
    fetchAgentModels(config).then(setAgentInfos).catch(() => {})
    fetchLegionModels(config).then(setLegionInfos).catch(() => {})
  }, [config, open])

  useEffect(() => {
    if (health.latencyMs != null) setRtt(prev => [...prev.slice(-31), health.latencyMs as number])
  }, [health.latencyMs])

  if (!open) return null

  const me = agentInfos.find(a => a.agent_id === agentId)
  // The owner's pin outranks the profile default — same precedence the backend
  // applies, so the column states what will actually run, not the fallback.
  const chatModel = me ? (me.override ?? me.default_main) : null
  const bgModel = me ? me.default_background : null
  // The Legion's general worker inherits the parent model; the pinned/derived
  // ref on any worker is the honest answer for "what do legionnaires run on".
  const legion = legionInfos.find(w => w.deployment_pin) ?? legionInfos.find(w => w.override) ?? legionInfos[0]
  const legionModel = legion ? (legion.deployment_pin ?? legion.override) : null

  const { input, output } = state.tokenUsage
  const total = input + output
  const inPct = total ? (input / total) * 100 : 0
  const outPct = total ? (output / total) * 100 : 0

  const armed = servers.filter(s => s.active)

  return (
    <aside
      className="hb-holo hb-island"
      style={{
        width: 'var(--sidebar-width)', flexShrink: 0,
        margin: '0 20px 20px 0',
        padding: '22px 20px',
        display: 'flex', flexDirection: 'column', gap: 24,
        overflow: 'auto',
        position: 'relative', zIndex: 2,
      }}
    >
      {/* ── Link ─────────────────────────────────────────────────────────── */}
      <div>
        <div style={LABEL}>Telemetry</div>
        <Row k="Host" v={hostOf(config.apiBase)} />
        <Row
          k="Latency"
          v={health.online && health.latencyMs != null ? `${health.latencyMs} ms` : 'offline'}
          color={health.online ? 'var(--hb-green)' : 'var(--hb-red)'}
        />
        <Trace samples={rtt} />
      </div>

      {/* ── Spend ────────────────────────────────────────────────────────── */}
      <div>
        <div style={LABEL}>This session</div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 9 }}>
          <span style={{
            fontFamily: 'var(--font-ui)', fontSize: 30, fontWeight: 600,
            color: 'var(--hb-text)', fontVariantNumeric: 'tabular-nums',
          }}>{compact(total)}</span>
          <span style={{ fontSize: 13.5, color: 'var(--hb-text-faint)' }}>tokens</span>
        </div>
        <div style={{ height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.07)', overflow: 'hidden', display: 'flex' }}>
          <span style={{ width: `${inPct}%`, background: 'var(--hb-cyan)' }} />
          <span style={{ width: `${outPct}%`, background: 'var(--hb-cyan-dim)' }} />
        </div>
        <div style={{ display: 'flex', gap: 18, marginTop: 10 }}>
          <span
            title="Input counts the whole prompt each turn: system, memory, tools and history."
            style={{ fontSize: 13.5, color: 'var(--hb-text-dim)' }}
          >
            <span style={{ color: 'var(--hb-cyan)' }}>▲</span> {compact(input)} in
          </span>
          <span style={{ fontSize: 13.5, color: 'var(--hb-text-dim)' }}>
            <span style={{ color: 'var(--hb-cyan-dim)' }}>▼</span> {compact(output)} out
          </span>
        </div>
      </div>

      {/* ── Routing ──────────────────────────────────────────────────────── */}
      <div>
        <div style={LABEL}>Routing</div>
        <div style={{ borderBottom: '1px solid var(--hb-line)' }}>
          <Row k="Chat" v={shortModel(chatModel)} color="var(--hb-cyan-bright)" />
        </div>
        <div style={{ borderBottom: '1px solid var(--hb-line)' }}>
          <Row k="Background" v={shortModel(bgModel)} />
        </div>
        <Row k="Legion" v={legionModel ? shortModel(legionModel) : 'inherits'} />
      </div>

      {/* ── Toolsets ─────────────────────────────────────────────────────── */}
      <div>
        <div style={LABEL}>Toolsets</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {armed.length === 0 && (
            <span style={{ fontSize: 13, color: 'var(--hb-text-faint)' }}>none armed</span>
          )}
          {armed.map(s => (
            <span
              key={s.server}
              className="glass-round"
              title={`${s.label} — ${s.tools} tools, ${s.tokens} tokens of definitions`}
              style={{
                height: 28, padding: '0 12px', display: 'flex', alignItems: 'center',
                border: '1px solid var(--hb-edge)', background: 'var(--glass-tint)',
                fontSize: 13, color: 'var(--hb-text-dim)',
              }}
            >
              {s.label} {s.tools}
            </span>
          ))}
          {forgeOnline && (
            <span
              className="glass-round"
              title="The Forge peer is connected — Optimus runs agentically in its Cell."
              style={{
                height: 28, padding: '0 12px', display: 'flex', alignItems: 'center',
                border: '1px solid rgba(var(--hb-accent-rgb),0.32)',
                background: 'rgba(var(--hb-accent-rgb),0.12)',
                fontSize: 13, color: 'var(--hb-cyan-bright)',
              }}
            >
              Forge linked
            </span>
          )}
        </div>
      </div>

      <div style={{ flex: 1 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--hb-text-faint)' }}>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: health.online ? 'var(--hb-green)' : 'var(--hb-red)',
        }} />
        {health.tools != null ? `${health.tools} tools` : 'tools —'}
        {' · '}
        {state.sessions.length} session{state.sessions.length === 1 ? '' : 's'}
        {' · '}
        {state.messages.length} msg
      </div>
    </aside>
  )
}
