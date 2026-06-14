import { useRef, useState, useCallback, useEffect } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { useIsMobile } from '../lib/useIsMobile'
import { fetchModels, fileToImageBlock, getBudgetMode, setBudgetMode } from '../lib/api'
import type { AppConfig, ModelInfo, ImageBlock } from '../lib/types'

interface AttachedFile {
  id: string
  file: File
  name: string
  url: string
  isImage: boolean
  size: number
}

interface Props {
  onSend: (message: string, opts?: { images?: ImageBlock[] }) => void
  onStop?: () => void
  config: AppConfig
}

function shortModelName(name: string): string {
  return name.replace(/^(anthropic|openai|gemini|ollama):/, '').replace(/^Claude\s+/i, '').toUpperCase()
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'ANTHROPIC',
  openai: 'OPENAI',
  gemini: 'GOOGLE GEMINI',
  ollama: 'DEAD ZONE PROTOCOL',
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
          ? 'rgba(54,171,202,0.18)'
          : hover
          ? 'rgba(255,255,255,0.08)'
          : 'var(--hb-holo-fill)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        color: danger
          ? (lit ? '#c84a3a' : '#3a5a65')
          : active
          ? '#5fcce6'
          : lit
          ? '#7ab8c8'
          : '#3a5a65',
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
function SendBtn({ canSend, isStreaming, onSend, onStop }: {
  canSend: boolean; isStreaming: boolean; onSend: () => void; onStop?: () => void
}) {
  const [press, setPress] = useState(false)
  if (isStreaming) {
    return (
      <button
        className="hb-glass-xs"
        title="Stop generating"
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
  return (
    <button
      className="hb-glass-xs"
      title="Send message"
      onClick={onSend}
      disabled={!canSend}
      onMouseDown={() => { if (canSend) setPress(true) }}
      onMouseUp={() => setPress(false)}
      style={{
        width: 32, height: 32,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: canSend ? '1px solid rgba(95,204,230,0.7)' : '1px solid var(--hb-edge)',
        background: canSend ? 'rgba(54,171,202,0.35)' : 'var(--hb-holo-fill)',
        backdropFilter: 'var(--hb-holo-blur)',
        WebkitBackdropFilter: 'var(--hb-holo-blur)',
        boxShadow: canSend
          ? 'inset 0 1px 0 0 rgba(255,255,255,0.3)'
          : 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
        color: canSend ? '#eafaff' : '#2e5260',
        cursor: canSend ? 'pointer' : 'default',
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
        width: '100%', padding: '0.45rem 0.7rem 0.45rem 0.8rem',
        display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
        border: 'none',
        borderLeft: selected
          ? '2px solid #36abca'
          : hover
          ? '2px solid rgba(95,165,188,0.3)'
          : '2px solid transparent',
        background: selected
          ? 'rgba(54,171,202,0.1)'
          : hover
          ? 'rgba(54,171,202,0.05)'
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
          color: selected ? '#5fcce6' : hover ? '#9bbac5' : '#5d7f8a',
        }}>
          {shortModelName(model.name)}
        </div>
        {model.description && (
          <div style={{
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '0.7rem', color: '#2e5260',
            marginTop: '0.1rem', lineHeight: 1.35,
          }}>
            {model.description}
          </div>
        )}
      </div>
      {selected && (
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#36abca"
          strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '0.2rem' }}>
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      )}
    </button>
  )
}

/* ── Model picker ─────────────────────────────────────────────────────────── */
function ModelPicker({ models, activeId, onSelect }: {
  models: ModelInfo[]; activeId: string; onSelect: (id: string) => void
}) {
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

  const active = models.find(m => m.id === activeId)
  const label  = active ? shortModelName(active.name) : shortModelName(activeId)
  const [hover, setHover] = useState(false)

  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button
        className="hb-glass-xs"
        title="Select model"
        onClick={() => setOpen(v => !v)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          height: 30, padding: '0 0.45rem',
          display: 'flex', alignItems: 'center', gap: '0.3rem',
          border: `1px solid ${open ? 'var(--hb-edge-bright)' : hover ? 'rgba(170,225,255,0.35)' : 'var(--hb-edge)'}`,
          background: open ? 'rgba(54,171,202,0.14)' : 'var(--hb-holo-fill)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
          color: open || hover ? '#7ab8c8' : '#3a6472',
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
          background: 'rgba(10, 20, 27, 0.94)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          border: '1px solid var(--hb-edge)',
          boxShadow: 'var(--hb-holo-shadow)',
          animation: 'dropDown 0.12s ease',
          zIndex: 100,
          width: 290,
          overflow: 'hidden',
        }}>
          {/* panel header */}
          <div style={{
            height: 22, padding: '0 0.6rem',
            display: 'flex', alignItems: 'center',
            background: 'linear-gradient(90deg, rgba(29,93,112,0.7), rgba(29,93,112,0.15) 60%, transparent)',
            borderBottom: '1px solid rgba(95,165,188,0.2)',
            fontFamily: "'Rajdhani', sans-serif",
            fontSize: '0.62rem', fontWeight: 700,
            letterSpacing: '0.2em', textTransform: 'uppercase',
            color: '#7a96a1',
          }}>
            SELECT MODEL
          </div>
          <div style={{ padding: '0.2rem 0', maxHeight: 420, overflowY: 'auto' }}>
            {Array.from(new Set(models.map(m => m.provider ?? 'anthropic'))).map(provider => (
              <div key={provider}>
                <div style={{
                  padding: '0.45rem 0.8rem 0.1rem',
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.6rem', fontWeight: 700,
                  letterSpacing: '0.2em', textTransform: 'uppercase',
                  color: '#3a6472',
                }}>
                  {PROVIDER_LABELS[provider] ?? provider}
                </div>
                {models.filter(m => (m.provider ?? 'anthropic') === provider).map(m => (
                  <ModelItem
                    key={m.id}
                    model={m}
                    selected={m.id === activeId}
                    onSelect={() => { onSelect(m.id); setOpen(false) }}
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
        background: hover ? 'rgba(54,171,202,0.1)' : 'transparent',
        border: 'none',
        borderLeft: hover ? '2px solid #36abca' : '2px solid transparent',
        color: hover ? '#cadbe2' : '#7a96a1',
        cursor: 'pointer',
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.76rem', fontWeight: 600,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        textAlign: 'left',
        transition: 'background 0.1s, color 0.1s, border-color 0.1s',
      }}
    >
      <span style={{ color: hover ? '#36abca' : '#3a6472', flexShrink: 0, display: 'flex' }}>{icon}</span>
      <span style={{ flex: 1 }}>{label}</span>
      {value && (
        <span style={{
          fontFamily: "'Share Tech Mono', monospace", fontSize: '0.62rem',
          letterSpacing: '0.1em', color: valueColor || 'var(--hb-text-faint)',
        }}>
          {value}
        </span>
      )}
    </button>
  )
}

function MobileToolsMenu({ budget, listening, isStreaming, onAttach, onToggleBudget, onVoice }: {
  budget: boolean; listening: boolean; isStreaming: boolean
  onAttach: () => void; onToggleBudget: () => void; onVoice: () => void
}) {
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
        title="More tools"
        onClick={() => setOpen(v => !v)}
        style={{
          width: 30, height: 30,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: `1px solid ${open ? 'var(--hb-edge-bright)' : 'var(--hb-edge)'}`,
          background: open ? 'rgba(54,171,202,0.18)' : 'var(--hb-holo-fill)',
          backdropFilter: 'var(--hb-holo-blur)',
          WebkitBackdropFilter: 'var(--hb-holo-blur)',
          boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.12)',
          color: open ? '#5fcce6' : '#3a5a65',
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
          background: 'rgba(10, 20, 27, 0.92)',
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
            background: 'linear-gradient(90deg, rgba(29,93,112,0.7), rgba(29,93,112,0.15) 60%, transparent)',
            borderBottom: '1px solid rgba(95,165,188,0.2)',
            fontFamily: "'Rajdhani', sans-serif",
            fontSize: '0.62rem', fontWeight: 700,
            letterSpacing: '0.2em', textTransform: 'uppercase',
            color: '#7a96a1',
          }}>
            TOOLS
          </div>
          <MenuRow
            icon={<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>}
            label="Attach files"
            onClick={() => { onAttach(); setOpen(false) }}
          />
          <MenuRow
            icon={<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.2a2.4 2.4 0 0 1 2.5-1.7c1.3 0 2.3.8 2.3 1.9 0 2.4-4.6 1.4-4.6 3.7 0 1.1 1 1.9 2.3 1.9a2.4 2.4 0 0 0 2.5-1.7"/></svg>}
            label="Budget mode"
            value={budget ? 'ON' : 'OFF'}
            valueColor={budget ? '#5fc78f' : '#d3a04a'}
            onClick={onToggleBudget}
          />
          {!isStreaming && (
            <MenuRow
              icon={<svg width="13" height="13" viewBox="0 0 24 24" fill={listening ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>}
              label={listening ? 'Stop listening' : 'Voice input'}
              onClick={() => { onVoice(); setOpen(false) }}
            />
          )}
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
      border: '1px solid rgba(95,165,188,0.3)',
      background: src ? `center/cover no-repeat url("${src}")` : 'rgba(54,171,202,0.06)',
    }} title={alt} />
  )
}

/* ── Main component ───────────────────────────────────────────────────────── */
export default function InputBar({ onSend, onStop, config }: Props) {
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
  const [budget, setBudget]         = useState(true)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  useEffect(() => { fetchModels(config).then(setModels).catch(() => {}) }, [config])
  // Load budget state on mount, and re-sync whenever a turn finishes (SPEDA can
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

  /* ── Submit ───────────────────────────────────────────────────────────── */
  const submit = async () => {
    const msg = value.trim()
    if ((!msg && attachments.length === 0) || state.isStreaming) return
    const imageFiles = attachments.filter(a => a.isImage).map(a => a.file)
    setValue(''); clearAttachments(); setTimeout(resize, 0)
    let blocks: ImageBlock[] = []
    if (imageFiles.length) {
      try { blocks = await Promise.all(imageFiles.map(fileToImageBlock)) } catch { blocks = [] }
    }
    onSend(msg, blocks.length ? { images: blocks } : undefined)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }

  const handleVoiceInput = () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) return
    if (listening) { setListening(false); return }
    const recognition = new SR()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.onresult = (e: { results: { [k: number]: { [k: number]: { transcript: string } } } }) => {
      setValue(prev => (prev ? prev + ' ' : '') + e.results[0][0].transcript); resize()
    }
    recognition.onend  = () => setListening(false)
    recognition.onerror = () => setListening(false)
    recognition.start(); setListening(true)
  }

  const canSend = (value.trim().length > 0 || attachments.length > 0) && !state.isStreaming

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
              borderBottom: '1px solid rgba(95,165,188,0.14)',
            }}>
              {attachments.map(a => (
                <div key={a.id} style={{ position: 'relative' }}>
                  {a.isImage ? (
                    <Thumb file={a.file} alt={a.name} />
                  ) : (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '0.45rem',
                      height: 56, padding: '0 0.65rem 0 0.5rem',
                      background: 'rgba(54,171,202,0.06)',
                      border: '1px solid rgba(95,165,188,0.22)',
                      maxWidth: 200,
                    }}>
                      <span style={{ color: '#36abca', flexShrink: 0 }}><FileIcon /></span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{
                          fontFamily: "'SamsungOne','Inter',sans-serif",
                          fontSize: '0.75rem', color: '#cadbe2',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{a.name}</div>
                        <div style={{
                          fontFamily: "'Share Tech Mono', monospace",
                          fontSize: '0.62rem', color: '#2e5260', marginTop: '1px',
                        }}>{formatSize(a.size)}</div>
                      </div>
                    </div>
                  )}
                  {a.isImage && (
                    <span style={{
                      position: 'absolute', bottom: 0, left: 0, right: 0,
                      padding: '1px 4px', fontSize: '0.56rem',
                      fontFamily: "'Share Tech Mono', monospace",
                      color: '#7a96a1', background: 'rgba(4,8,12,0.75)', textAlign: 'right',
                    }}>{formatSize(a.size)}</span>
                  )}
                  <button onClick={() => removeAttachment(a.id)} title="Remove"
                    style={{
                      position: 'absolute', top: -6, right: -6,
                      width: 16, height: 16,
                      background: '#050d12', border: '1px solid rgba(95,165,188,0.4)',
                      color: '#5d7f8a', cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '0.65rem', lineHeight: 1,
                    }}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Textarea */}
          <div style={{ padding: '1.05rem 1.05rem 0.5rem' }}>
            <textarea
              ref={textareaRef}
              rows={1}
              value={value}
              onChange={e => setValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={onPaste}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder="How can I help you today?"
              style={{
                width: '100%', background: 'transparent', border: 'none', outline: 'none',
                resize: 'none', color: '#cadbe2',
                fontSize: '0.9375rem', lineHeight: 1.65,
                fontFamily: "'SamsungOne','Inter',sans-serif",
                overflowY: 'hidden', maxHeight: 200,
                caretColor: '#36abca',
                userSelect: 'text',
              }}
            />
          </div>

          {/* ── Toolbar ────────────────────────────────────────────── */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.25rem 0.6rem 0.5rem',
            borderTop: '1px solid rgba(95,165,188,0.1)',
          }}>
            {/* Left controls — on mobile the secondary toggles collapse into
                a single "+" overflow menu; the model picker keeps its slot */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              {isMobile ? (
                <MobileToolsMenu
                  budget={budget}
                  listening={listening}
                  isStreaming={state.isStreaming}
                  onAttach={() => fileInputRef.current?.click()}
                  onToggleBudget={toggleBudget}
                  onVoice={handleVoiceInput}
                />
              ) : (<>
              {/* Attach */}
              <ToolBtn title="Attach files or images" onClick={() => fileInputRef.current?.click()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
              </ToolBtn>

              {/* Budget mode toggle — green when frugal, amber when unleashed */}
              <button
                className="hb-glass-xs"
                title={budget
                  ? 'Budget mode ON — concise answers, no sub-agents. Click to unleash.'
                  : 'Full power — deep research enabled. Click to go frugal.'}
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
                {budget ? 'Budget' : 'Full'}
              </button>
              </>)}
            </div>

            {/* Right controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              <ModelPicker
                models={models}
                activeId={settings.model}
                onSelect={id => update({ model: id })}
              />

              {!isMobile && !state.isStreaming && (
                <ToolBtn
                  title={listening ? 'Stop listening' : 'Voice input'}
                  onClick={handleVoiceInput}
                  active={listening}
                  danger={listening}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24"
                    fill={listening ? 'currentColor' : 'none'}
                    stroke="currentColor" strokeWidth="2">
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
              background: 'rgba(54,171,202,0.12)',
              backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)',
              border: '1px dashed #5fcce6',
              color: '#5fcce6',
              fontFamily: "'Rajdhani',sans-serif",
              fontSize: '0.78rem', fontWeight: 700,
              letterSpacing: '0.2em', textTransform: 'uppercase',
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              Drop to attach
            </div>
          )}
        </div>

        {/* ── Status strip ──────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: '0',
          marginTop: '0.4rem',
          fontFamily: "'Share Tech Mono', monospace",
          fontSize: '0.62rem', letterSpacing: '0.06em',
          color: '#1e3d4a',
          userSelect: 'none',
        }}>
          {[
            `${profile?.name ?? 'AI'} can make mistakes`,
            'Enter to send',
            'Shift+Enter for newline',
            'paste or drop images',
          ].map((seg, i) => (
            <span key={i} style={{ display: 'flex', alignItems: 'center' }}>
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
