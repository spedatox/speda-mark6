import { useRef, useState, useCallback, useEffect } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { fetchModels, fileToImageBlock } from '../lib/api'
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
  onSend: (message: string, images?: ImageBlock[]) => void
  onStop?: () => void
  config: AppConfig
}

function shortModelName(name: string): string {
  return name.replace(/^Claude\s+/i, '')
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><polyline points="13 2 13 9 20 9" />
    </svg>
  )
}

export default function InputBar({ onSend, onStop, config }: Props) {
  const { state } = useChatContext()
  const { settings, update } = useSettings()
  const profile = useProfile()
  const [value, setValue] = useState('')
  const [focused, setFocused] = useState(false)
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [webSearch, setWebSearch] = useState(false)
  const [listening, setListening] = useState(false)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  const dragDepth = useRef(0)

  useEffect(() => { fetchModels(config).then(setModels).catch(() => {}) }, [config])

  useEffect(() => {
    if (!modelMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) setModelMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [modelMenuOpen])

  const activeModel = models.find(m => m.id === settings.model)
  const activeModelLabel = activeModel ? shortModelName(activeModel.name) : shortModelName(settings.model)

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [])
  useEffect(() => { resize() }, [value, resize])

  // ── Attachments ──────────────────────────────────────────────────────────
  const addFiles = useCallback((files: File[]) => {
    if (!files.length) return
    const next = files.map(f => ({
      id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`,
      file: f,
      name: f.name || (f.type.startsWith('image/') ? 'pasted-image.png' : 'file'),
      url: URL.createObjectURL(f),
      isImage: f.type.startsWith('image/'),
      size: f.size,
    }))
    setAttachments(prev => [...prev, ...next])
  }, [])

  const removeAttachment = (id: string) => {
    setAttachments(prev => {
      const target = prev.find(a => a.id === id)
      if (target) URL.revokeObjectURL(target.url)
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

  const onDragOver = (e: React.DragEvent) => { e.preventDefault() }
  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    dragDepth.current += 1
    if (e.dataTransfer.types.includes('Files')) setDragOver(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    dragDepth.current -= 1
    if (dragDepth.current <= 0) { setDragOver(false); dragDepth.current = 0 }
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    dragDepth.current = 0
    setDragOver(false)
    addFiles(Array.from(e.dataTransfer.files))
  }

  const submit = async () => {
    const msg = value.trim()
    if ((!msg && attachments.length === 0) || state.isStreaming) return
    const imageFiles = attachments.filter(a => a.isImage).map(a => a.file)
    setValue('')
    clearAttachments()
    setTimeout(resize, 0)
    let blocks: ImageBlock[] = []
    if (imageFiles.length) {
      try { blocks = await Promise.all(imageFiles.map(fileToImageBlock)) } catch { blocks = [] }
    }
    onSend(msg, blocks.length ? blocks : undefined)
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
    recognition.onend = () => setListening(false)
    recognition.onerror = () => setListening(false)
    recognition.start()
    setListening(true)
  }

  const canSend = (value.trim().length > 0 || attachments.length > 0) && !state.isStreaming

  return (
    <div style={{ padding: '0.5rem 1rem 1rem', flexShrink: 0 }}>
      <div style={{ maxWidth: 768, margin: '0 auto' }}>

        {/* Composer — paste + drag-and-drop enabled */}
        <div
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          style={{
            position: 'relative',
            background: 'var(--bg-input)',
            border: `1px solid ${dragOver ? 'var(--hb-cyan)' : focused ? 'rgba(110,200,228,0.45)' : 'var(--hb-line)'}`,
            transition: 'border-color 0.15s',
          }}
        >
          {/* Attachment previews — inside the composer, above the textarea */}
          {attachments.length > 0 && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: '0.5rem',
              padding: '0.625rem 0.75rem',
              borderBottom: '1px solid var(--hb-line)',
            }}>
              {attachments.map(a => (
                <div key={a.id} className="hb-attach" style={{ position: 'relative' }}>
                  {a.isImage ? (
                    <img
                      src={a.url} alt={a.name}
                      style={{ width: 58, height: 58, objectFit: 'cover', display: 'block',
                               border: '1px solid var(--hb-line)' }}
                    />
                  ) : (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '0.5rem',
                      height: 58, padding: '0 0.7rem 0 0.55rem',
                      background: 'rgba(54,171,202,0.06)', border: '1px solid var(--hb-line)',
                      maxWidth: 220,
                    }}>
                      <span style={{ color: 'var(--hb-cyan)', flexShrink: 0 }}><FileIcon /></span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: '0.78rem', color: 'var(--hb-text)', overflow: 'hidden',
                                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</div>
                        <div style={{ fontSize: '0.66rem', color: 'var(--hb-text-faint)', fontFamily: 'var(--font-mono)' }}>
                          {formatSize(a.size)}
                        </div>
                      </div>
                    </div>
                  )}
                  {/* image size tag */}
                  {a.isImage && (
                    <span style={{
                      position: 'absolute', bottom: 0, left: 0, right: 0,
                      padding: '1px 3px', fontSize: '0.58rem', fontFamily: 'var(--font-mono)',
                      color: '#dff', background: 'rgba(4,8,10,0.7)', textAlign: 'right',
                    }}>{formatSize(a.size)}</span>
                  )}
                  <button
                    onClick={() => removeAttachment(a.id)}
                    title="Remove"
                    style={{
                      position: 'absolute', top: -7, right: -7,
                      width: 18, height: 18,
                      background: 'var(--hb-base)', border: '1px solid var(--hb-line)',
                      color: 'var(--hb-text)', cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '0.7rem', lineHeight: 1,
                    }}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Textarea row */}
          <div style={{ display: 'flex', alignItems: 'center', padding: '0.625rem 0.75rem 0.375rem 1rem', gap: '0.5rem' }}>
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
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                resize: 'none', color: 'var(--text-primary)',
                fontSize: '0.9375rem', lineHeight: 1.65, fontFamily: 'inherit',
                overflowY: 'hidden', maxHeight: 200, caretColor: 'var(--accent)',
                paddingTop: '0.1rem', userSelect: 'text',
              }}
            />
          </div>

          {/* Bottom toolbar */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.25rem 0.625rem 0.5rem' }}>
            {/* Left: attach + web */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <button
                title="Attach files or images"
                onClick={() => fileInputRef.current?.click()}
                style={{
                  width: 32, height: 32,
                  border: '1px solid var(--hb-line)', background: 'transparent',
                  color: 'var(--text-secondary)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s',
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hb-cyan)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--hb-cyan-bright)' }}
                onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hb-line)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              <button
                title={webSearch ? 'Disable web search' : 'Enable web search'}
                onClick={() => setWebSearch(v => !v)}
                style={{
                  height: 32, padding: '0 0.625rem',
                  border: `1px solid ${webSearch ? 'var(--hb-cyan)' : 'var(--hb-line)'}`,
                  background: webSearch ? 'rgba(54,171,202,0.12)' : 'transparent',
                  color: webSearch ? 'var(--accent)' : 'var(--text-muted)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.75rem', fontWeight: 500,
                  transition: 'all 0.15s',
                }}
                onMouseEnter={e => { if (!webSearch) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
                onMouseLeave={e => { if (!webSearch) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)' }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" />
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                </svg>
                Web
              </button>
            </div>

            {/* Right: model + mic + send/stop */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <div style={{ position: 'relative' }} ref={modelMenuRef}>
                <button
                  title="Select model"
                  onClick={() => setModelMenuOpen(v => !v)}
                  style={{
                    height: 32, padding: '0 0.5rem 0 0.625rem', border: '1px solid transparent',
                    background: modelMenuOpen ? 'var(--bg-hover)' : 'transparent',
                    color: 'var(--text-secondary)', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.78rem', fontWeight: 500,
                    transition: 'background 0.15s, color 0.15s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                  onMouseLeave={e => { if (!modelMenuOpen) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
                >
                  {activeModelLabel}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    style={{ transform: modelMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>

                {modelMenuOpen && (
                  <div style={{
                    position: 'absolute', bottom: 'calc(100% + 8px)', right: 0,
                    background: 'var(--bg-sidebar)', border: '1px solid var(--border)',
                    boxShadow: '0 8px 28px rgba(0,0,0,0.55)', animation: 'dropDown 0.12s ease', zIndex: 100,
                    width: 280, padding: '0.35rem',
                  }}>
                    {models.map(m => {
                      const selected = m.id === settings.model
                      return (
                        <button key={m.id}
                          onClick={() => { update({ model: m.id }); setModelMenuOpen(false) }}
                          style={{
                            width: '100%', padding: '0.55rem 0.625rem',
                            background: selected ? 'var(--bg-active)' : 'transparent', border: 'none',
                            cursor: 'pointer', textAlign: 'left', display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
                            transition: 'background 0.1s',
                          }}
                          onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)' }}
                          onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
                        >
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: '0.83rem', fontWeight: 500, color: selected ? 'var(--accent)' : 'var(--text-primary)' }}>
                              {shortModelName(m.name)}
                            </div>
                            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.1rem', lineHeight: 1.4 }}>
                              {m.description}
                            </div>
                          </div>
                          {selected && (
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '0.15rem' }}>
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>

              {!state.isStreaming && (
                <button
                  title={listening ? 'Stop listening' : 'Voice input'}
                  onClick={handleVoiceInput}
                  style={{
                    width: 32, height: 32,
                    border: listening ? '1px solid rgba(239,68,68,0.4)' : '1px solid transparent',
                    background: listening ? 'rgba(239,68,68,0.1)' : 'transparent',
                    color: listening ? '#ef4444' : 'var(--text-muted)', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { if (!listening) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                  onMouseLeave={e => { if (!listening) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)' }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill={listening ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" />
                  </svg>
                </button>
              )}

              {state.isStreaming ? (
                <button onClick={onStop} title="Stop generating"
                  style={{ width: 34, height: 34, border: 'none', background: 'var(--hb-cyan)', color: 'var(--hb-base)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'transform 0.1s' }}
                  onMouseDown={e => (e.currentTarget as HTMLButtonElement).style.transform = 'scale(0.9)'}
                  onMouseUp={e => (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)'}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" /></svg>
                </button>
              ) : (
                <button onClick={submit} disabled={!canSend} title="Send message"
                  style={{
                    width: 34, height: 34, border: 'none',
                    background: canSend ? 'var(--hb-cyan)' : 'rgba(255,255,255,0.08)',
                    color: canSend ? 'var(--hb-base)' : 'rgba(255,255,255,0.3)',
                    cursor: canSend ? 'pointer' : 'default',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'background 0.15s, transform 0.1s',
                  }}
                  onMouseDown={e => { if (canSend) (e.currentTarget as HTMLButtonElement).style.transform = 'scale(0.9)' }}
                  onMouseUp={e => { if (canSend) (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M12 19V5M5 12l7-7 7 7" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          {/* Drag-and-drop overlay */}
          {dragOver && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 5, pointerEvents: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
              background: 'rgba(54,171,202,0.10)',
              border: '1px dashed var(--hb-cyan)',
              color: 'var(--hb-cyan-bright)', fontSize: '0.8rem', letterSpacing: '0.06em',
              textTransform: 'uppercase', fontWeight: 600,
            }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              Drop to attach
            </div>
          )}
        </div>

        {/* Footer hint */}
        <p style={{ textAlign: 'center', marginTop: '0.5rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          {profile?.name ?? 'AI'} can make mistakes · paste or drop images · Enter to send · Shift+Enter for newline
        </p>
      </div>

      {/* Single hidden file input (images + files) */}
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
