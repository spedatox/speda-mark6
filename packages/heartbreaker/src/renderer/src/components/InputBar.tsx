import { useRef, useState, useCallback, useEffect, useMemo } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchModels, fileToImageBlock, fileToDocBlock, getBudgetMode, setBudgetMode } from '../lib/api'
import { MicSession, micAvailable, type MicState } from '../lib/mic'
import { fetchVoices } from '../lib/voice'
import type { AppConfig, ModelInfo, ImageBlock, DocBlock, UploadedFile } from '../lib/types'
import { useT } from '../lib/i18n'
import type { Dict } from '../lib/i18n/en'

interface AttachedFile {
  id: string
  file: File
  name: string
  url: string
  isImage: boolean
  size: number
}

interface Props {
  onSend: (message: string, opts?: { images?: ImageBlock[]; documents?: DocBlock[]; uploads?: UploadedFile[] }) => void
  onStop?: () => void
  /** Inject `message` into the turn that is CURRENTLY streaming, instead of
   *  waiting for it to finish. Resolves to whether it landed — false means the
   *  running turn is not steerable (in-process, or it just ended), and the
   *  composer keeps the text rather than losing it. Absent entirely on a
   *  surface with no steering (only ChatMain wires this today). */
  onSteer?: (message: string) => Promise<boolean>
  config: AppConfig
  /** In voice mode a finished utterance IS the turn and sends itself. Outside
   *  it, the same mic dictates into the composer for the owner to edit. */
  voiceMode?: boolean
  /** The agent is speaking right now, so mic onset counts as barge-in. */
  agentSpeaking?: boolean
  /** Barge-in: the owner started talking over the agent. Fires on onset. */
  onSpeechStart?: () => void
  /** Mic state, so the orb can show that it is listening. */
  onMicState?: (s: MicState) => void
  /** Filled with a getter for the live input level while the mic is open, and
   *  nulled when it closes. A ref rather than a value because the orb polls it
   *  per frame — pushing sixty renders a second through React to move one
   *  number would cost more than the whole scene does. */
  micLevelRef?: React.MutableRefObject<(() => number) | null>
}

function shortModelName(name: string): string {
  return name.replace(/^(anthropic|openai|gemini|zai|deepseek|nvidia|ollama):/, '').replace(/^Claude\s+/i, '').toUpperCase()
}

// Company/product names stay untranslated (ANTHROPIC, OPENAI, …) — only the
// one playful in-house codename (ollama's "dead zone") is localized.
function providerLabels(t: Dict): Record<string, string> {
  return {
    anthropic: 'ANTHROPIC',
    openai: 'OPENAI',
    gemini: 'GOOGLE GEMINI',
    zai: 'Z.AI · GLM',
    deepseek: 'DEEPSEEK',
    nvidia: 'NVIDIA NIM',
    ollama: t.inputBar.providerOllama,
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

/* ── Toolbar button — sharp square ───────────────────────────────────────── */
function ToolBtn({
  title, onClick, active = false, danger = false, children,
}: {
  title: string; onClick?: () => void; active?: boolean; danger?: boolean; children: React.ReactNode
}) {
  const [hover, setHover] = useState(false)
  const lit = active || hover
  return (
    <button
      className="hb-glass-xs"
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 30, height: 30,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `1px solid ${active ? 'var(--hb-edge-bright)' : lit ? 'rgba(0,163,255,0.3)' : 'var(--hb-edge)'}`,
        background: active
          ? 'rgba(var(--hb-accent-rgb),0.18)'
          : hover
          ? 'rgba(255,255,255,0.08)'
          : 'var(--hb-holo-fill)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        color: danger
          ? (lit ? '#c84a3a' : 'var(--hb-icon-dim)')
          : active
          ? 'var(--hb-cyan-bright)'
          : lit
          ? 'var(--hb-text-dim)'
          : 'var(--hb-icon-dim)',
        cursor: 'pointer',
        transition: 'border-color 0.12s, background 0.12s, color 0.12s',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

/* ── Send / Stop button ───────────────────────────────────────────────────── */
function SendBtn({ canSend, isStreaming, canSteer, onSend, onStop }: {
  canSend: boolean; isStreaming: boolean; canSteer?: boolean; onSend: () => void; onStop?: () => void
}) {
  const t = useT()
  const [press, setPress] = useState(false)
  // Typing something into a streaming turn swaps Stop for Send: the button now
  // offers to inject what's in the composer rather than end the run. An empty
  // composer keeps Stop — clear the text to get it back.
  if (isStreaming && !canSteer) {
    return (
      <button
        className="hb-glass-xs"
        title={t.inputBar.stopGenerating}
        onClick={onStop}
        onMouseDown={() => setPress(true)}
        onMouseUp={() => setPress(false)}
        style={{
          width: 32, height: 32,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: '1px solid rgba(200,74,58,0.5)',
          background: 'rgba(200,74,58,0.18)',
          color: '#c84a3a',
          cursor: 'pointer',
          transform: press ? 'scale(0.9)' : 'scale(1)',
          transition: 'transform 0.1s',
        }}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
          <rect x="4" y="4" width="16" height="16"/>
        </svg>
      </button>
    )
  }
  // Two ways to reach this render: a normal composer (canSend) or a streaming
  // one with steerable text (canSteer, isStreaming true). Either enables it;
  // the title says which this click will do.
  const active = isStreaming ? !!canSteer : canSend
  return (
    <button
      className="hb-glass-xs"
      title={isStreaming ? t.inputBar.steerMessage : t.inputBar.sendMessage}
      onClick={onSend}
      disabled={!active}
      onMouseDown={() => { if (active) setPress(true) }}
      onMouseUp={() => setPress(false)}
      style={{
        width: 32, height: 32,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: active ? '1px solid rgba(var(--hb-cyan-bright-rgb),0.7)' : '1px solid var(--hb-edge)',
        background: active ? 'rgba(var(--hb-accent-rgb),0.35)' : 'var(--hb-holo-fill)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: active
          ? 'inset 0 1px 0 0 rgba(255,255,255,0.3)'
          : 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        color: active ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-dim)',
        cursor: active ? 'pointer' : 'default',
        transform: press ? 'scale(0.9)' : 'scale(1)',
        transition: 'background 0.15s, border-color 0.15s, color 0.15s, transform 0.1s',
      }}
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round">
        <path d="M12 19V5M5 12l7-7 7 7"/>
      </svg>
    </button>
  )
}

/* ── Model item (extracted — hooks cannot live inside .map()) ────────────── */
function ModelItem({ model, selected, onSelect }: {
  model: ModelInfo; selected: boolean; onSelect: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        // Indented — these rows hang off the provider header above them.
        width: '100%', padding: '0.45rem 0.7rem 0.45rem 1.6rem',
        display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
        border: 'none',
        borderLeft: selected
          ? '2px solid var(--hb-cyan)'
          : hover
          ? '2px solid rgba(var(--hb-accent-rgb),0.3)'
          : '2px solid transparent',
        background: selected
          ? 'rgba(var(--hb-accent-rgb),0.1)'
          : hover
          ? 'rgba(var(--hb-accent-rgb),0.05)'
          : 'transparent',
        cursor: 'pointer', textAlign: 'left',
        transition: 'background 0.1s, border-color 0.1s',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: "'Rajdhani',sans-serif",
          fontSize: '0.8rem', fontWeight: selected ? 700 : 600,
          letterSpacing: '0.08em', textTransform: 'uppercase',
          color: selected ? 'var(--hb-cyan-bright)' : hover ? 'var(--hb-text-dim)' : 'var(--hb-icon-bright)',
        }}>
          {shortModelName(model.name)}
        </div>
        {model.description && (
          <div style={{
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '0.7rem', color: 'var(--hb-icon-dim)',
            marginTop: '0.1rem', lineHeight: 1.35,
          }}>
            {model.description}
          </div>
        )}
      </div>
      {selected && (
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)"
          strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '0.2rem' }}>
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      )}
    </button>
  )
}

/* ── Provider group header — one row per provider, collapsed ─────────────── */
function ProviderRow({ label, count, open, holdsActive, onClick }: {
  label: string; count: number; open: boolean; holdsActive: boolean; onClick: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', padding: '0.5rem 0.7rem 0.5rem 0.8rem',
        display: 'flex', alignItems: 'center', gap: '0.5rem',
        border: 'none',
        borderLeft: open
          ? '2px solid var(--hb-cyan)'
          : hover
          ? '2px solid rgba(var(--hb-accent-rgb),0.3)'
          : '2px solid transparent',
        background: open
          ? 'rgba(var(--hb-accent-rgb),0.1)'
          : hover
          ? 'rgba(var(--hb-accent-rgb),0.05)'
          : 'transparent',
        cursor: 'pointer', textAlign: 'left',
        transition: 'background 0.1s, border-color 0.1s',
      }}
    >
      {/* A shut group still says where the pin lives. */}
      <span style={{
        width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
        background: holdsActive ? 'var(--hb-cyan-bright)' : 'transparent',
      }}/>
      <span style={{
        flex: 1, minWidth: 0,
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.68rem', fontWeight: 700,
        letterSpacing: '0.18em', textTransform: 'uppercase',
        color: open || holdsActive
          ? 'var(--hb-cyan-bright)'
          : hover ? 'var(--hb-text-dim)' : 'var(--hb-icon-bright)',
      }}>
        {label}
      </span>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
        letterSpacing: '0.1em', color: 'var(--hb-text-faint)', flexShrink: 0,
      }}>
        {count}
      </span>
      <svg width="9" height="9" viewBox="0 0 24 24" fill="none"
        stroke={open ? 'var(--hb-cyan)' : 'var(--hb-icon-dim)'} strokeWidth="2.5"
        style={{ flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    </button>
  )
}

/* ── Model picker ─────────────────────────────────────────────────────────── */
/**
 * Picks the text model AND the voice, on two tabs.
 *
 * They share this control rather than getting one each because they are the
 * same kind of decision — pick an engine, pick a model — and the composer has
 * no room for a second dropdown. They are separate AXES though: the agent can
 * think on Claude and speak with an OpenAI voice, and nothing couples them.
 */
function ModelPicker({ models, activeId, onSelect, voices, activeVoiceId, onSelectVoice }: {
  models: ModelInfo[]; activeId: string; onSelect: (id: string) => void
  voices: ModelInfo[]; activeVoiceId: string; onSelectVoice: (id: string) => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'text' | 'voice'>('text')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  const active = models.find(m => m.id === activeId)
  const label  = active ? shortModelName(active.name) : shortModelName(activeId)
  const [hover, setHover] = useState(false)

  // The catalogue runs to a hundred-odd models across seven providers, so the
  // panel lists PROVIDERS and opens exactly one group at a time. Provider order
  // follows the backend's catalogue order.
  // Whichever tab is showing supplies the list, the selection and the setter,
  // so everything below is written once instead of twice.
  const list = tab === 'text' ? models : voices
  const selectedId = tab === 'text' ? activeId : activeVoiceId
  const choose = tab === 'text' ? onSelect : onSelectVoice

  const groups = useMemo(() => {
    const by = new Map<string, ModelInfo[]>()
    for (const m of list) {
      const p = m.provider || 'anthropic'
      const g = by.get(p)
      if (g) g.push(m)
      else by.set(p, [m])
    }
    return [...by.entries()]
  }, [list])
  const activeProvider = tab === 'text'
    ? (active ? (active.provider || 'anthropic') : null)
    : (voices.find(v => v.id === activeVoiceId)?.provider ?? null)
  const [expanded, setExpanded] = useState<string | null>(activeProvider)

  // Every time the panel opens, land on the pinned model's group rather than
  // wherever the last visit left the accordion.
  // Also re-runs when the tab changes, so switching to VOICE lands on the
  // engine the current voice belongs to rather than on a text provider that
  // has no voices under it.
  useEffect(() => {
    if (open) setExpanded(activeProvider)
  }, [open, tab, activeProvider])

  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button
        className="hb-glass-xs"
        title={t.inputBar.selectModel}
        onClick={() => setOpen(v => !v)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          height: 30, padding: '0 0.45rem',
          display: 'flex', alignItems: 'center', gap: '0.3rem',
          border: `1px solid ${open ? 'var(--hb-edge-bright)' : hover ? 'rgba(var(--hb-accent-rgb),0.35)' : 'var(--hb-edge)'}`,
          background: open ? 'rgba(var(--hb-accent-rgb),0.14)' : 'var(--hb-holo-fill)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
          color: open || hover ? 'var(--hb-text-dim)' : 'var(--hb-icon)',
          cursor: 'pointer',
          transition: 'border-color 0.12s, background 0.12s, color 0.12s',
        }}
      >
        <span style={{
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: '0.78rem', fontWeight: 600,
          letterSpacing: '0.08em', textTransform: 'uppercase',
        }}>
          {label}
        </span>
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }}>
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {open && (
        <div className="hb-glass" style={{
          position: 'absolute', bottom: 'calc(100% + 6px)', right: 0,
          // Dense frost: this dropdown lives inside the composer, which has its
          // own backdrop-filter. Nested backdrop roots cancel the child's blur
          // (Chromium), so the fill itself must occlude what's behind it.
          background: 'var(--glass-menu)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          border: '1px solid var(--hb-edge)',
          boxShadow: 'var(--hb-holo-shadow)',
          animation: 'dropDown 0.12s ease',
          zIndex: 100,
          width: 290,
          overflow: 'hidden',
        }}>
          {/* Panel header, doubling as the tab strip. Text and voice are
              separate axes, so they get separate lists rather than one merged
              catalogue where picking a voice would look like changing brains. */}
          <div style={{
            height: 24, display: 'flex', alignItems: 'stretch',
            background: 'rgba(var(--hb-accent-rgb),0.12)',
            boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
            borderBottom: '1px solid rgba(var(--hb-accent-rgb),0.2)',
          }}>
            {(['text', 'voice'] as const).map(tabId => (
              <button
                key={tabId}
                onClick={() => setTab(tabId)}
                style={{
                  flex: 1, border: 'none', cursor: 'pointer',
                  background: tab === tabId ? 'rgba(var(--hb-accent-rgb),0.22)' : 'transparent',
                  color: tab === tabId ? 'var(--hb-cyan-bright)' : 'var(--hb-text-dim)',
                  borderBottom: tab === tabId ? '2px solid var(--hb-cyan)' : '2px solid transparent',
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.62rem', fontWeight: 700,
                  letterSpacing: '0.2em', textTransform: 'uppercase',
                  transition: 'background 0.1s, color 0.1s, border-color 0.1s',
                }}
              >
                {tabId === 'text' ? t.inputBar.tabText : t.inputBar.tabVoice}
              </button>
            ))}
          </div>
          <div style={{ padding: '0.2rem 0', maxHeight: 420, overflowY: 'auto' }}>
            {groups.length === 0 && (
              <div style={{
                padding: '0.8rem 0.9rem',
                fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
                lineHeight: 1.6, color: 'var(--hb-icon-dim)',
              }}>
                {tab === 'voice' ? t.inputBar.noVoices : t.inputBar.noModels}
              </div>
            )}
            {groups.map(([provider, items]) => (
              <div key={provider}>
                <ProviderRow
                  label={providerLabels(t)[provider] ?? provider.toUpperCase()}
                  count={items.length}
                  open={expanded === provider}
                  holdsActive={provider === activeProvider}
                  onClick={() => setExpanded(p => (p === provider ? null : provider))}
                />
                {expanded === provider && items.map(m => (
                  <ModelItem
                    key={m.id}
                    model={m}
                    selected={m.id === selectedId}
                    onSelect={() => { choose(m.id); setOpen(false) }}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Mobile overflow menu — collapses the secondary composer toggles ──────────
 * Under 768px the attach / budget / voice controls live in a single "+" menu
 * so the toolbar holds only: [+] · model picker · send. The model picker stays
 * outside on purpose — it is the one control worth a permanent slot. */
function MenuRow({ icon, label, value, valueColor, onClick }: {
  icon: React.ReactNode; label: string; value?: string; valueColor?: string; onClick: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%', padding: '0.6rem 0.8rem',
        display: 'flex', alignItems: 'center', gap: '0.6rem',
        background: hover ? 'rgba(var(--hb-accent-rgb),0.1)' : 'transparent',
        border: 'none',
        borderLeft: hover ? '2px solid var(--hb-cyan)' : '2px solid transparent',
        color: hover ? 'var(--hb-text)' : 'var(--hb-text-dim)',
        cursor: 'pointer',
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.76rem', fontWeight: 600,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        textAlign: 'left',
        transition: 'background 0.1s, color 0.1s, border-color 0.1s',
      }}
    >
      <span style={{ color: hover ? 'var(--hb-cyan)' : 'var(--hb-icon)', flexShrink: 0, display: 'flex' }}>{icon}</span>
      <span style={{ flex: 1 }}>{label}</span>
      {value && (
        <span style={{
          fontFamily: "var(--font-mono)", fontSize: '0.62rem',
          letterSpacing: '0.1em', color: valueColor || 'var(--hb-text-faint)',
        }}>
          {value}
        </span>
      )}
    </button>
  )
}

function MobileToolsMenu({ budget, listening, onAttach, onToggleBudget, onVoice }: {
  budget: boolean; listening: boolean
  onAttach: () => void; onToggleBudget: () => void; onVoice: () => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="hb-glass-xs"
        title={t.inputBar.moreTools}
        onClick={() => setOpen(v => !v)}
        style={{
          width: 30, height: 30,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: `1px solid ${open ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
          background: open ? 'rgba(var(--hb-accent-rgb),0.18)' : 'var(--hb-holo-fill)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
          color: open ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-dim)',
          cursor: 'pointer',
          transition: 'border-color 0.12s, background 0.12s, color 0.12s',
          flexShrink: 0,
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          style={{ transform: open ? 'rotate(45deg)' : 'none', transition: 'transform 0.15s' }}>
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
      </button>

      {open && (
        <div className="hb-glass" style={{
          position: 'absolute', bottom: 'calc(100% + 6px)', left: 0,
          width: 224,
          // Dense frost: the composer's own backdrop-filter creates a nested
          // backdrop root, which stops this panel's blur from sampling the
          // textarea beneath it — so the fill itself must do the occluding.
          background: 'var(--glass-menu)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          border: '1px solid var(--hb-edge)',
          boxShadow: 'var(--hb-holo-shadow)',
          animation: 'dropDown 0.12s ease',
          zIndex: 100,
          overflow: 'hidden',
        }}>
          {/* panel header */}
          <div style={{
            height: 22, padding: '0 0.6rem',
            display: 'flex', alignItems: 'center',
            background: 'rgba(var(--hb-accent-rgb),0.12)',
            boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
            borderBottom: '1px solid rgba(var(--hb-accent-rgb),0.2)',
            fontFamily: "'Rajdhani', sans-serif",
            fontSize: '0.62rem', fontWeight: 700,
            letterSpacing: '0.2em', textTransform: 'uppercase',
            color: 'var(--hb-text-dim)',
          }}>
            {t.inputBar.tools}
          </div>
          <MenuRow
            icon={<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>}
            label={t.inputBar.attachFiles}
            onClick={() => { onAttach(); setOpen(false) }}
          />
          <MenuRow
            icon={<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.2a2.4 2.4 0 0 1 2.5-1.7c1.3 0 2.3.8 2.3 1.9 0 2.4-4.6 1.4-4.6 3.7 0 1.1 1 1.9 2.3 1.9a2.4 2.4 0 0 0 2.5-1.7"/></svg>}
            label={t.inputBar.budgetMode}
            value={budget ? t.inputBar.on : t.inputBar.off}
            valueColor={budget ? '#5fc78f' : '#d3a04a'}
            onClick={onToggleBudget}
          />
          {/* Available while streaming too — that is when barge-in happens. */}
          <MenuRow
            icon={<svg width="13" height="13" viewBox="0 0 24 24" fill={listening ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>}
            label={listening ? t.inputBar.stopListening : t.inputBar.voiceInput}
            onClick={() => { onVoice(); setOpen(false) }}
          />
        </div>
      )}
    </div>
  )
}

/* ── File icon ────────────────────────────────────────────────────────────── */
function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
      <polyline points="13 2 13 9 20 9"/>
    </svg>
  )
}

/* ── Image thumbnail ──────────────────────────────────────────────────────────
 * Reads the file as a data URL via FileReader instead of URL.createObjectURL.
 * Electron's renderer CSP can block blob: URLs in <img>, which showed a broken
 * image. data: URLs render reliably and match what the vision pipeline sends. */
function Thumb({ file, alt }: { file: File; alt: string }) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    let live = true
    const r = new FileReader()
    r.onload = () => { if (live) setSrc(r.result as string) }
    r.readAsDataURL(file)
    return () => { live = false }
  }, [file])
  return (
    <div style={{
      width: 56, height: 56, flexShrink: 0,
      border: '1px solid rgba(var(--hb-accent-rgb),0.3)',
      background: src ? `center/cover no-repeat url("${src}")` : 'rgba(var(--hb-accent-rgb),0.06)',
    }} title={alt} />
  )
}

/* The background-dispatch command, as typed into the composer. Must stay in
 * lock-step with the backend's BG_COMMAND (app/core/dispatch.py); typing it
 * plus a space lifts the composer into Background mode (see onChangeValue). */
const BG_PREFIX = '/bg '

/* ── Main component ───────────────────────────────────────────────────────── */
export default function InputBar({
  onSend, onStop, onSteer, config, voiceMode, agentSpeaking, onSpeechStart, onMicState, micLevelRef,
}: Props) {
  const t = useT()
  const { state } = useChatContext()
  const { settings, update } = useSettings()
  const profile = useProfile()
  const isMobile = useIsMobile()
  const [value, setValue]           = useState('')
  const [focused, setFocused]       = useState(false)
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const [dragOver, setDragOver]     = useState(false)
  const [listening, setListening]   = useState(false)
  const [models, setModels]         = useState<ModelInfo[]>([])
  const [voices, setVoices]         = useState<ModelInfo[]>([])
  const [budget, setBudget]         = useState(true)
  const [bgMode, setBgMode]         = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  useEffect(() => { fetchModels(config).then(setModels).catch(() => {}) }, [config])
  // The voice catalogue is a separate call: Azure's list is a live per-region
  // lookup that can fail on its own, and a slow or dead voice endpoint must not
  // hold up the text models the composer actually needs to function.
  useEffect(() => { fetchVoices(config).then(setVoices).catch(() => {}) }, [config])
  // Load budget state on mount, and re-sync whenever a turn finishes (Speda can
  // toggle it itself via the set_budget_mode tool).
  useEffect(() => { getBudgetMode(config).then(setBudget).catch(() => {}) }, [config])
  useEffect(() => {
    if (!state.isStreaming) getBudgetMode(config).then(setBudget).catch(() => {})
  }, [state.isStreaming, config])

  const toggleBudget = useCallback(async () => {
    const next = !budget
    setBudget(next) // optimistic
    const confirmed = await setBudgetMode(config, next)
    setBudget(confirmed)
  }, [budget, config])

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [])
  useEffect(() => { resize() }, [value, resize])

  /* ── Attachments ──────────────────────────────────────────────────────── */
  const addFiles = useCallback((files: File[]) => {
    if (!files.length) return
    setAttachments(prev => [...prev, ...files.map(f => ({
      id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`,
      file: f,
      name: f.name || (f.type.startsWith('image/') ? 'pasted-image.png' : 'file'),
      url: URL.createObjectURL(f),
      isImage: f.type.startsWith('image/'),
      size: f.size,
    }))])
  }, [])

  const removeAttachment = (id: string) => {
    setAttachments(prev => {
      const t = prev.find(a => a.id === id)
      if (t) URL.revokeObjectURL(t.url)
      return prev.filter(a => a.id !== id)
    })
  }

  const clearAttachments = () => {
    setAttachments(prev => { prev.forEach(a => URL.revokeObjectURL(a.url)); return [] })
  }

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items ?? [])
    const imgs = items.filter(it => it.kind === 'file' && it.type.startsWith('image/'))
    if (imgs.length) {
      e.preventDefault()
      addFiles(imgs.map(it => it.getAsFile()).filter((f): f is File => !!f))
    }
  }

  const onDragOver  = (e: React.DragEvent) => { e.preventDefault() }
  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault(); dragDepth.current += 1
    if (e.dataTransfer.types.includes('Files')) setDragOver(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault(); dragDepth.current -= 1
    if (dragDepth.current <= 0) { setDragOver(false); dragDepth.current = 0 }
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); dragDepth.current = 0; setDragOver(false)
    addFiles(Array.from(e.dataTransfer.files))
  }

  /* Typing the background command + space flips the composer into Background
   * mode: the literal prefix is lifted out of the field and shown as a chip, so
   * what the owner types and sees afterward is just the task — never the command
   * echoed back. submit() re-prepends it. The chip's × exits the mode and keeps
   * whatever task text was already typed as an ordinary message. */
  const onChangeValue = (next: string) => {
    if (!bgMode && next.startsWith(BG_PREFIX)) {
      setBgMode(true)
      setValue(next.slice(BG_PREFIX.length))
      return
    }
    setValue(next)
  }

  /* ── Submit ───────────────────────────────────────────────────────────── */
  const submit = async () => {
    const task = value.trim()
    // In Background mode an empty field is a no-op, not a bare "/bg" send.
    if (bgMode && !task) return
    const msg = bgMode ? `${BG_PREFIX}${task}` : task

    // A turn is already streaming: this is steering, not a second turn. Text
    // only — Background mode and attachments still wait for the composer to
    // free up, since /bg starts an unrelated job and an attachment has nowhere
    // to go in a steer. Failure (not an external turn, or it just ended) keeps
    // the text in the composer rather than losing it or silently sending late.
    if (state.isStreaming) {
      if (bgMode || attachments.length > 0 || !msg || !onSteer) return
      const landed = await onSteer(msg)
      if (landed) { setValue(''); setTimeout(resize, 0) }
      return
    }

    if (!msg && attachments.length === 0) return
    const imageFiles = attachments.filter(a => a.isImage).map(a => a.file)
    const docFiles   = attachments.filter(a => !a.isImage).map(a => a.file)
    setValue(''); setBgMode(false); clearAttachments(); setTimeout(resize, 0)

    let images: ImageBlock[] = []
    if (imageFiles.length) {
      try { images = await Promise.all(imageFiles.map(fileToImageBlock)) } catch { images = [] }
    }
    let documents: DocBlock[] = []
    if (docFiles.length) {
      try { documents = await Promise.all(docFiles.map(fileToDocBlock)) } catch { documents = [] }
    }
    const uploads: UploadedFile[] = docFiles.map(f => ({ name: f.name || 'file', size: f.size }))

    const opts: { images?: ImageBlock[]; documents?: DocBlock[]; uploads?: UploadedFile[] } = {}
    if (images.length) opts.images = images
    if (documents.length) { opts.documents = documents; opts.uploads = uploads }
    onSend(msg, Object.keys(opts).length ? opts : undefined)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }

  /* ── Microphone ───────────────────────────────────────────────────────────
   * Replaces the browser's SpeechRecognition, which could not work here on two
   * counts: it was pinned to en-US, and Chromium's implementation reaches a
   * Google endpoint using a key that Electron builds do not carry, so start()
   * failed silently. This routes through the backend's Azure Speech instead —
   * the same key that already speaks the replies.
   *
   * The mic and the keyboard are peers, never modes. Outside voice mode a
   * transcript lands in the composer to be edited and sent by hand; inside it,
   * the utterance IS the turn and goes straight out. Either way the textarea
   * stays live, so a sentence can be started out loud and finished by typing. */
  const micRef = useRef<MicSession | null>(null)
  const [micState, setMicState] = useState<MicState>('off')

  // Read through refs: the mic session outlives any single render, and a
  // transcript arriving mid-turn must see the CURRENT voice-mode flag rather
  // than the one captured when the mic was switched on.
  const voiceModeRef = useRef(!!voiceMode)
  voiceModeRef.current = !!voiceMode
  const streamingRef = useRef(state.isStreaming)
  streamingRef.current = state.isStreaming

  const stopMic = useCallback(() => {
    micRef.current?.stop()
    micRef.current = null
    if (micLevelRef) micLevelRef.current = null
    setListening(false)
    setMicState('off')
  }, [micLevelRef])

  const handleVoiceInput = useCallback(async () => {
    if (micRef.current) { stopMic(); return }
    if (!micAvailable()) return

    const session = new MicSession(config, {
      locale: settings.voiceLocale,
      onState: s => { setMicState(s); onMicState?.(s) },
      // Onset, not transcript — see mic.ts. Cutting the agent off is the
      // owner's most time-critical action in the whole feature.
      onSpeechStart: () => onSpeechStart?.(),
      onTranscript: text => {
        if (voiceModeRef.current) {
          // In voice mode the utterance is the turn. A transcript that lands
          // while a turn is still streaming is the tail of a barge-in, and
          // ChatMain has already cancelled that run, so it sends normally.
          onSend(text)
        } else {
          // Dictation: append and let the owner edit. Appended rather than
          // replaced so a second sentence does not erase the first.
          setValue(prev => (prev ? prev.trimEnd() + ' ' : '') + text)
          setTimeout(resize, 0)
        }
      },
    })
    micRef.current = session
    if (micLevelRef) micLevelRef.current = () => session.amplitude()
    setListening(true)
    try {
      await session.start()
    } catch {
      // Denied permission or no device. Nothing to say that the absent
      // recording indicator does not already say.
      stopMic()
    }
  }, [config, settings.voiceLocale, onMicState, onSpeechStart, onSend, resize, stopMic, micLevelRef])

  // The mic has to know when the agent is talking, so speech onset can be read
  // as an interruption rather than as an ordinary utterance.
  useEffect(() => { micRef.current?.setAgentSpeaking(!!agentSpeaking) }, [agentSpeaking])

  // Leaving voice mode, or unmounting, releases the device. An input indicator
  // that outlives its UI is the same class of failure as audio that keeps
  // playing after the orb is gone.
  useEffect(() => { if (voiceMode === false) stopMic() }, [voiceMode, stopMic])
  useEffect(() => () => { micRef.current?.stop() }, [])

  const canSend = (value.trim().length > 0 || attachments.length > 0) && !state.isStreaming
  // While a turn streams, the up-arrow steers it instead of starting a new
  // one — text only, and only when there is somewhere to steer INTO.
  const canSteer = state.isStreaming && !bgMode && attachments.length === 0
                   && value.trim().length > 0 && !!onSteer

  /* ── Render ───────────────────────────────────────────────────────────── */
  return (
    <div className="hb-composer" style={{ padding: '0.5rem 1.25rem 0.875rem', flexShrink: 0 }}>
      <div style={{ maxWidth: 780, margin: '0 auto' }}>

        {/* ── Composer panel — precision-machined holographic glass ─────── */}
        <div
          className="hb-holo"
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          style={{
            position: 'relative',
            borderColor: dragOver
              ? 'rgba(242,183,92,0.7)'
              : focused
              ? 'var(--hb-edge-bright)'
              : 'var(--hb-edge)',
            // Focus brightens the specular horizon + aura; base stack from .hb-holo
            boxShadow: focused ? 'var(--hb-holo-shadow-active)' : undefined,
            transition: 'border-color 0.2s, box-shadow 0.2s',
          }}
        >
          {/* Attachment previews */}
          {attachments.length > 0 && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: '0.45rem',
              padding: '0.6rem 0.85rem',
              borderBottom: '1px solid rgba(var(--hb-accent-rgb),0.14)',
            }}>
              {attachments.map(a => (
                <div key={a.id} style={{ position: 'relative' }}>
                  {a.isImage ? (
                    <Thumb file={a.file} alt={a.name} />
                  ) : (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '0.45rem',
                      height: 56, padding: '0 0.65rem 0 0.5rem',
                      background: 'rgba(var(--hb-accent-rgb),0.06)',
                      border: '1px solid rgba(var(--hb-accent-rgb),0.22)',
                      maxWidth: 200,
                    }}>
                      <span style={{ color: 'var(--hb-cyan)', flexShrink: 0 }}><FileIcon /></span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{
                          fontFamily: "'SamsungOne','Inter',sans-serif",
                          fontSize: '0.75rem', color: 'var(--hb-text)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{a.name}</div>
                        <div style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: '0.62rem', color: 'var(--hb-icon-dim)', marginTop: '1px',
                        }}>{formatSize(a.size)}</div>
                      </div>
                    </div>
                  )}
                  {a.isImage && (
                    <span style={{
                      position: 'absolute', bottom: 0, left: 0, right: 0,
                      padding: '1px 4px', fontSize: '0.56rem',
                      fontFamily: "var(--font-mono)",
                      color: 'var(--hb-text-dim)', background: 'rgba(4,8,12,0.75)', textAlign: 'right',
                    }}>{formatSize(a.size)}</span>
                  )}
                  <button onClick={() => removeAttachment(a.id)} title={t.inputBar.remove}
                    className="hb-glass-xs"
                    style={{
                      position: 'absolute', top: -6, right: -6,
                      width: 16, height: 16,
                      background: 'var(--glass-fill)',
                      backdropFilter: 'var(--hb-holo-blur)',
                      WebkitBackdropFilter: 'var(--hb-holo-blur)',
                      border: '1px solid rgba(var(--hb-accent-rgb),0.4)',
                      boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.14)',
                      color: 'var(--hb-icon-bright)', cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '0.65rem', lineHeight: 1,
                    }}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Background-mode chip — shown instead of the literal "/bg " prefix
              once the composer is in Background mode. Bold, dismissable; on
              send the prefix is re-applied so the backend routes it. */}
          {bgMode && (
            <div style={{ padding: '0.85rem 1.05rem 0' }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                padding: '0.2rem 0.55rem', borderRadius: 999,
                background: 'rgba(var(--hb-accent-rgb),0.16)',
                border: '1px solid rgba(var(--hb-accent-rgb),0.4)',
                color: 'var(--hb-text)', fontSize: '0.8rem',
                fontFamily: "'SamsungOne','Inter',sans-serif",
              }}>
                <strong style={{ fontWeight: 700 }}>Background mode</strong>
                <button
                  onClick={() => { setBgMode(false); textareaRef.current?.focus() }}
                  title="Exit background mode"
                  style={{
                    background: 'transparent', border: 'none', cursor: 'pointer',
                    color: 'var(--hb-icon-bright)', padding: 0, lineHeight: 1,
                    fontSize: '0.9rem', display: 'flex', alignItems: 'center',
                  }}
                >×</button>
              </span>
            </div>
          )}

          {/* Textarea */}
          <div style={{ padding: '1.05rem 1.05rem 0.5rem' }}>
            <textarea
              ref={textareaRef}
              rows={1}
              value={value}
              onChange={e => onChangeValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={onPaste}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={bgMode ? 'Describe the background task…' : t.inputBar.placeholder}
              style={{
                width: '100%', background: 'transparent', border: 'none', outline: 'none',
                resize: 'none', color: 'var(--hb-text)',
                fontSize: '0.9375rem', lineHeight: 1.65,
                fontFamily: "'SamsungOne','Inter',sans-serif",
                overflowY: 'hidden', maxHeight: 200,
                caretColor: 'var(--hb-cyan)',
                userSelect: 'text',
              }}
            />
          </div>

          {/* ── Toolbar ────────────────────────────────────────────── */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.25rem 0.6rem 0.5rem',
            borderTop: '1px solid rgba(var(--hb-accent-rgb),0.1)',
          }}>
            {/* Left controls — on mobile the secondary toggles collapse into
                a single "+" overflow menu; the model picker keeps its slot */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              {isMobile ? (
                <MobileToolsMenu
                  budget={budget}
                  listening={listening}
                  onAttach={() => fileInputRef.current?.click()}
                  onToggleBudget={toggleBudget}
                  onVoice={handleVoiceInput}
                />
              ) : (<>
              {/* Attach */}
              <ToolBtn title={t.inputBar.attachFilesOrImages} onClick={() => fileInputRef.current?.click()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
              </ToolBtn>

              {/* Budget mode toggle — green when frugal, amber when unleashed */}
              <button
                className="hb-glass-xs"
                title={budget ? t.inputBar.budgetOnTitle : t.inputBar.budgetOffTitle}
                onClick={toggleBudget}
                style={{
                  height: 30, padding: '0 0.55rem',
                  display: 'flex', alignItems: 'center', gap: '0.35rem',
                  border: `1px solid ${budget ? 'rgba(79,163,119,0.45)' : 'rgba(211,154,58,0.4)'}`,
                  background: budget ? 'rgba(79,163,119,0.1)' : 'rgba(211,154,58,0.08)',
                  backdropFilter: 'var(--hb-holo-blur)',
                  WebkitBackdropFilter: 'var(--hb-holo-blur)',
                  boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
                  color: budget ? '#5fc78f' : '#d3a04a',
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                  fontFamily: "'Rajdhani',sans-serif",
                  fontSize: '0.7rem', fontWeight: 700,
                  letterSpacing: '0.12em', textTransform: 'uppercase',
                }}
              >
                {/* coin/wallet glyph */}
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="9"/>
                  <path d="M12 7v10M9.5 9.2a2.4 2.4 0 0 1 2.5-1.7c1.3 0 2.3.8 2.3 1.9 0 2.4-4.6 1.4-4.6 3.7 0 1.1 1 1.9 2.3 1.9a2.4 2.4 0 0 0 2.5-1.7"/>
                </svg>
                {budget ? t.inputBar.budgetShort : t.inputBar.fullShort}
              </button>
              </>)}
            </div>

            {/* Right controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              <ModelPicker
                models={models}
                activeId={settings.model}
                onSelect={id => update({ model: id })}
                voices={voices}
                activeVoiceId={settings.voiceModel}
                onSelectVoice={id => update({ voiceModel: id })}
              />

              {/* Deliberately NOT hidden while streaming: barge-in means
                  talking over the agent, so the mic has to be reachable at
                  exactly the moment the old gate removed it. */}
              {!isMobile && micAvailable() && (
                <ToolBtn
                  title={
                    !listening ? (voiceMode ? t.inputBar.speakInsteadOfTyping : t.inputBar.voiceInputDictate)
                    : micState === 'hearing' ? t.inputBar.listeningNow
                    : micState === 'recognizing' ? t.inputBar.transcribing
                    : t.inputBar.micOnClickToStop
                  }
                  onClick={handleVoiceInput}
                  active={listening}
                  danger={micState === 'hearing'}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24"
                    fill={micState === 'hearing' ? 'currentColor' : 'none'}
                    stroke="currentColor" strokeWidth="2"
                    style={{
                      // Pulses only while actually hearing speech, so the icon
                      // distinguishes "mic is on" from "you are being heard" —
                      // an open mic that looks identical either way is the
                      // reason people talk to a muted machine.
                      animation: micState === 'hearing' ? 'pulse 1.1s ease-in-out infinite' : undefined,
                      opacity: micState === 'recognizing' ? 0.55 : 1,
                      transition: 'opacity 0.15s',
                    }}>
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                    <line x1="12" y1="19" x2="12" y2="23"/>
                    <line x1="8" y1="23" x2="16" y2="23"/>
                  </svg>
                </ToolBtn>
              )}

              <SendBtn
                canSend={canSend}
                isStreaming={state.isStreaming}
                canSteer={canSteer}
                onSend={submit}
                onStop={onStop}
              />
            </div>
          </div>

          {/* Drag overlay */}
          {dragOver && (
            <div className="hb-glass" style={{
              position: 'absolute', inset: 0, zIndex: 5, pointerEvents: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem',
              background: 'rgba(var(--hb-accent-rgb),0.12)',
              backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)',
              border: '1px dashed var(--hb-cyan-bright)',
              color: 'var(--hb-cyan-bright)',
              fontFamily: "'Rajdhani',sans-serif",
              fontSize: '0.78rem', fontWeight: 700,
              letterSpacing: '0.2em', textTransform: 'uppercase',
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              {t.inputBar.dropToAttach}
            </div>
          )}
        </div>

        {/* ── Status strip ──────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: '0',
          marginTop: '0.4rem',
          fontFamily: "var(--font-mono)",
          fontSize: '0.62rem', letterSpacing: '0.06em',
          color: 'var(--hb-icon-dim)',
          userSelect: 'none',
        }}>
          {[
            t.inputBar.canMakeMistakes(profile?.name ?? 'AI'),
            t.inputBar.enterToSend,
            t.inputBar.shiftEnterNewline,
            t.inputBar.pasteOrDropImages,
          ].map((seg, i) => (
            <span key={i} data-brand-text={i === 0 ? '' : undefined} style={{ display: 'flex', alignItems: 'center' }}>
              {i > 0 && (
                <span style={{ margin: '0 0.55rem', color: '#162a33' }}>·</span>
              )}
              {seg}
            </span>
          ))}
        </div>
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.txt,.md,.csv,.json,.docx,.xlsx,.pptx"
        style={{ display: 'none' }}
        onChange={e => { addFiles(Array.from(e.target.files ?? [])); e.target.value = '' }}
      />
    </div>
  )
}
