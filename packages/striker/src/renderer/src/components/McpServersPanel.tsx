/**
 * Hand-added MCP servers.
 *
 * The premise of MCP is that a tool server is a thing you point at and
 * authenticate — a command line, or a URL and a header — so there is no reason
 * adding one should require a backend release. This panel is that: name it, say
 * how to reach it, paste whatever credential it wants, save. The backend stores
 * the record, connects it immediately, and re-registers it on every boot.
 *
 * Credentials come back from the API MASKED (the key NAMES survive, the values
 * don't). A masked value sent back unchanged is understood as "leave it alone",
 * which is what lets the owner rename a server without retyping its API key.
 *
 * Lives in its own module rather than inside SettingsModal because it carries
 * real form state — a draft server, its key/value pairs, a save verdict — and
 * folding that into the modal would put four more useStates on a component that
 * already holds every other tab's.
 */

import { useEffect, useState } from 'react'
import {
  getCustomMcpServers, saveCustomMcpServer, deleteCustomMcpServer,
  type CustomMcpServer,
} from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PillBtn, ServiceRow, Switch, fieldStyle } from './settingsUI'
import { SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'

/** One key/value pair as the editor holds it — an array, not an object, so a
 *  half-typed key doesn't collapse two rows into one while you type. */
type Pair = { key: string; value: string }

const EMPTY_DRAFT = {
  name: '', transport: 'stdio' as 'stdio' | 'http',
  command: '', url: '', note: '',
}

function pairsToObject(pairs: Pair[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const p of pairs) if (p.key.trim()) out[p.key.trim()] = p.value
  return out
}

function objectToPairs(obj: Record<string, string> | undefined): Pair[] {
  return Object.entries(obj ?? {}).map(([key, value]) => ({ key, value }))
}

const ServerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <rect x="3" y="4" width="18" height="7" rx="1" />
    <rect x="3" y="13" width="18" height="7" rx="1" />
    <line x1="7" y1="7.5" x2="7.01" y2="7.5" />
    <line x1="7" y1="16.5" x2="7.01" y2="16.5" />
  </svg>
)

export default function McpServersPanel({ config }: { config: AppConfig }) {
  const t = useT()
  const [servers, setServers] = useState<CustomMcpServer[]>([])
  const [reserved, setReserved] = useState<string[]>([])
  const [loaded, setLoaded] = useState(false)
  const [draft, setDraft] = useState({ ...EMPTY_DRAFT })
  const [envPairs, setEnvPairs] = useState<Pair[]>([])
  const [headerPairs, setHeaderPairs] = useState<Pair[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = async () => {
    try {
      const r = await getCustomMcpServers(config)
      setServers(r.servers)
      setReserved(r.reserved)
    } finally {
      // Always clear — an unreachable backend must not skeleton this forever.
      setLoaded(true)
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => {
    setDraft({ ...EMPTY_DRAFT })
    setEnvPairs([])
    setHeaderPairs([])
    setEditing(null)
    setOpen(false)
    setMsg(null)
  }

  const startEdit = (s: CustomMcpServer) => {
    setDraft({
      name: s.name,
      transport: s.transport,
      command: Array.isArray(s.command) ? s.command.join(' ') : (s.command || ''),
      url: s.url || '',
      note: s.note || '',
    })
    setEnvPairs(objectToPairs(s.env))
    setHeaderPairs(objectToPairs(s.headers))
    setEditing(s.name)
    setOpen(true)
    setMsg(null)
  }

  const save = async () => {
    setBusy(true)
    setMsg(null)
    const r = await saveCustomMcpServer(config, {
      name: draft.name.trim().toLowerCase(),
      transport: draft.transport,
      command: draft.command,
      url: draft.url,
      env: pairsToObject(envPairs),
      headers: pairsToObject(headerPairs),
      note: draft.note,
      enabled: true,
    })
    setBusy(false)
    await load()
    if (r.error) { setMsg({ text: r.error, ok: false }); return }
    setMsg({
      text: r.connected
        ? t.mcpServersPanel.connected(r.tools ?? 0)
        : (r.message ?? t.mcpServersPanel.saved),
      ok: true,
    })
    if (r.connected) setTimeout(reset, 1600)
  }

  const remove = async (name: string) => {
    setServers(ss => ss.filter(s => s.name !== name)) // optimistic
    await deleteCustomMcpServer(config, name)
    load()
  }

  const toggle = async (s: CustomMcpServer, enabled: boolean) => {
    setServers(ss => ss.map(x => x.name === s.name ? { ...x, enabled } : x))
    await saveCustomMcpServer(config, { ...s, enabled })
    load()
  }

  const nameTaken = reserved.includes(draft.name.trim().toLowerCase())
  const canSave = Boolean(
    draft.name.trim() && !nameTaken && !busy &&
    (draft.transport === 'http' ? draft.url.trim() : draft.command.trim()),
  )

  return (
    <div style={{ marginTop: -8 }}>
      <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: '0 0 14px' }}>
        {t.mcpServersPanel.intro}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {!loaded && <SkeletonList rows={2} />}
        {loaded && servers.map(s => (
          <ServiceRow
            key={s.name}
            tint={s.connected ? '#5cc98f' : undefined}
            icon={<ServerIcon />}
            name={s.name}
            desc={
              s.connected
                ? `${s.tools ?? 0} tools · ${s.transport === 'http' ? s.url : (Array.isArray(s.command) ? s.command.join(' ') : s.command)}`
                : (s.enabled ? t.mcpServersPanel.notConnected : t.mcpServersPanel.switchedOff)
            }
          >
            <Switch
              on={s.enabled}
              onChange={v => toggle(s, v)}
              title={s.enabled ? t.mcpServersPanel.enabledClickDisable : t.mcpServersPanel.disabledClickEnable}
            />
            <PillBtn onClick={() => startEdit(s)}>{t.mcpServersPanel.edit}</PillBtn>
            <PillBtn onClick={() => remove(s.name)} tone="danger" title={t.mcpServersPanel.forgetServerTitle}>{t.mcpServersPanel.remove}</PillBtn>
          </ServiceRow>
        ))}
        {loaded && servers.length === 0 && !open && (
          <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', margin: 0 }}>
            {t.mcpServersPanel.noneYet}
          </p>
        )}
      </div>

      {!open && (
        <div style={{ marginTop: 14 }}>
          <PillBtn onClick={() => setOpen(true)} tone="accent">{t.mcpServersPanel.addServer}</PillBtn>
        </div>
      )}

      {open && (
        <div className="hb-tile" style={{
          marginTop: 14, padding: 18, display: 'flex', flexDirection: 'column', gap: 14,
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)',
        }}>
          <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>
            {editing ? t.mcpServersPanel.editServer(editing) : t.mcpServersPanel.newServer}
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{t.mcpServersPanel.name}</div>
              <input
                style={fieldStyle}
                value={draft.name}
                disabled={Boolean(editing)}
                placeholder="linear"
                onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div style={{ width: 170 }}>
              <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{t.mcpServersPanel.transport}</div>
              <select
                style={{ ...fieldStyle, cursor: 'pointer' }}
                value={draft.transport}
                onChange={e => setDraft(d => ({ ...d, transport: e.target.value as 'stdio' | 'http' }))}
              >
                <option value="stdio">{t.mcpServersPanel.stdioCommand}</option>
                <option value="http">{t.mcpServersPanel.httpUrl}</option>
              </select>
            </div>
          </div>
          {nameTaken && (
            <p style={{ fontSize: '0.8125rem', color: '#e5897c', margin: 0 }}>
              {t.mcpServersPanel.nameTaken(draft.name)}
            </p>
          )}

          {draft.transport === 'stdio' ? (
            <div>
              <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{t.mcpServersPanel.command}</div>
              <input
                style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                value={draft.command}
                placeholder="npx -y @some/mcp-server"
                onChange={e => setDraft(d => ({ ...d, command: e.target.value }))}
              />
              <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '8px 0 0', lineHeight: 1.5 }}>
                {t.mcpServersPanel.commandHint}
              </p>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{t.mcpServersPanel.endpointUrl}</div>
              <input
                style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                value={draft.url}
                placeholder="https://mcp.example.com/mcp"
                onChange={e => setDraft(d => ({ ...d, url: e.target.value }))}
              />
            </div>
          )}

          <PairEditor
            label={draft.transport === 'stdio' ? t.mcpServersPanel.envVars : t.mcpServersPanel.httpHeaders}
            hint={draft.transport === 'stdio' ? t.mcpServersPanel.envVarsHint : t.mcpServersPanel.httpHeadersHint}
            keyPlaceholder={draft.transport === 'stdio' ? 'SOME_API_KEY' : 'Authorization'}
            valuePlaceholder={draft.transport === 'stdio' ? 'sk-…' : 'Bearer sk-…'}
            pairs={draft.transport === 'stdio' ? envPairs : headerPairs}
            onChange={draft.transport === 'stdio' ? setEnvPairs : setHeaderPairs}
          />

          <div>
            <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{t.mcpServersPanel.note}</div>
            <input
              style={fieldStyle}
              value={draft.note}
              placeholder={t.mcpServersPanel.notePlaceholder}
              onChange={e => setDraft(d => ({ ...d, note: e.target.value }))}
            />
          </div>

          {msg && (
            <p style={{ fontSize: '0.845rem', color: msg.ok ? '#8fdcb3' : '#e5897c', margin: 0, lineHeight: 1.5 }}>
              {msg.text}
            </p>
          )}

          <div style={{ display: 'flex', gap: 10 }}>
            <PillBtn onClick={canSave ? save : undefined} tone="accent"
              title={canSave ? undefined : t.mcpServersPanel.fillNameFirst}>
              {busy ? t.mcpServersPanel.connecting : editing ? t.mcpServersPanel.saveAndReconnect : t.mcpServersPanel.saveAndConnect}
            </PillBtn>
            <PillBtn onClick={reset}>{t.mcpServersPanel.cancel}</PillBtn>
          </div>
        </div>
      )}
    </div>
  )
}

/** A growable list of key/value rows — the credential surface for both
 *  transports. An empty trailing row is always present so adding the first
 *  variable takes no click. */
function PairEditor({ label, hint, pairs, onChange, keyPlaceholder, valuePlaceholder }: {
  label: string
  hint: string
  pairs: Pair[]
  onChange: (p: Pair[]) => void
  keyPlaceholder: string
  valuePlaceholder: string
}) {
  const rows = pairs.length ? pairs : [{ key: '', value: '' }]
  const set = (i: number, patch: Partial<Pair>) => {
    const next = rows.map((p, j) => (j === i ? { ...p, ...patch } : p))
    // Keep exactly one blank row at the end so the list always has room.
    const trimmed = next.filter((p, j) => p.key || p.value || j === next.length - 1)
    if (trimmed[trimmed.length - 1]?.key || trimmed[trimmed.length - 1]?.value) {
      trimmed.push({ key: '', value: '' })
    }
    onChange(trimmed)
  }
  return (
    <div>
      <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', marginBottom: 8, lineHeight: 1.5 }}>
        {hint}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {rows.map((p, i) => (
          <div key={i} style={{ display: 'flex', gap: 8 }}>
            <input
              style={{ ...fieldStyle, flex: '0 0 40%', fontFamily: 'var(--font-mono)' }}
              value={p.key}
              placeholder={keyPlaceholder}
              onChange={e => set(i, { key: e.target.value })}
            />
            <input
              style={{ ...fieldStyle, flex: 1, fontFamily: 'var(--font-mono)' }}
              value={p.value}
              placeholder={valuePlaceholder}
              onChange={e => set(i, { value: e.target.value })}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
