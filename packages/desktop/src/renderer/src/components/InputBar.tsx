import { useRef, useState, useCallback, useEffect } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { useProfile } from './Sidebar'
import { fetchModels } from '../lib/api'
import type { AppConfig, ModelInfo } from '../lib/types'

interface AttachedFile {
  name: string
  url: string
  isImage: boolean
}

interface Props {
  onSend: (message: string) => void
  onStop?: () => void
  config: AppConfig
}

// "Claude Sonnet 4.6" → "Sonnet 4.6", "ollama:llama3.1:8b" → "llama3.1:8b"
function shortModelName(name: string): string {
  return name.replace(/^(anthropic|openai|gemini|ollama):/, '').replace(/^Claude\s+/i, '')
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  gemini: 'Google Gemini',
  ollama: 'Ollama — Local',
}

export default function InputBar({ onSend, onStop, config }: Props) {
  const { state } = useChatContext()
  const { settings, update } = useSettings()
  const profile = useProfile()
  const [value, setValue] = useState('')
  const [focused, setFocused] = useState(false)
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const [webSearch, setWebSearch] = useState(false)
  const [listening, setListening] = useState(false)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)
  const attachMenuRef = useRef<HTMLDivElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)

  // Load available models once
  useEffect(() => {
    fetchModels(config).then(setModels).catch(() => {})
  }, [config])

  // Close model menu on outside click
  useEffect(() => {
    if (!modelMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setModelMenuOpen(false)
      }
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

  useEffect(() => {
    if (!attachMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target as Node)) {
        setAttachMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [attachMenuOpen])

  const submit = () => {
    const msg = value.trim()
    if (!msg || state.isStreaming) return
    setValue('')
    setAttachments([])
    setTimeout(resize, 0)
    onSend(msg)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>, isImage: boolean) => {
    const files = Array.from(e.target.files ?? [])
    const newAttachments: AttachedFile[] = files.map(f => ({
      name: f.name,
      url: URL.createObjectURL(f),
      isImage: isImage || f.type.startsWith('image/'),
    }))
    setAttachments(prev => [...prev, ...newAttachments])
    setAttachMenuOpen(false)
    e.target.value = ''
  }

  const removeAttachment = (i: number) => {
    setAttachments(prev => {
      URL.revokeObjectURL(prev[i].url)
      return prev.filter((_, idx) => idx !== i)
    })
  }

  const handleVoiceInput = () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) return
    if (listening) { setListening(false); return }
    const recognition = new SR()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.onresult = (e: { results: { [key: number]: { [key: number]: { transcript: string } } } }) => {
      const transcript = e.results[0][0].transcript
      setValue(prev => prev ? prev + ' ' + transcript : transcript)
      resize()
    }
    recognition.onend = () => setListening(false)
    recognition.onerror = () => setListening(false)
    recognition.start()
    setListening(true)
  }

  const canSend = value.trim().length > 0 && !state.isStreaming

  return (
    <div style={{ padding: '0.5rem 1rem 1rem', flexShrink: 0 }}>
      <div style={{ maxWidth: 768, margin: '0 auto' }}>

        {/* File attachment strip */}
        {attachments.length > 0 && (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '0.5rem',
            padding: '0.5rem 0.75rem 0.25rem',
            background: 'var(--bg-input)',
            borderRadius: '1rem 1rem 0 0',
            border: '1px solid rgba(255,255,255,0.08)',
            borderBottom: 'none',
          }}>
            {attachments.map((f, i) => (
              <div key={i} style={{ position: 'relative' }}>
                {f.isImage ? (
                  <img src={f.url} alt={f.name} style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: '0.5rem', border: '1px solid rgba(255,255,255,0.1)' }} />
                ) : (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '0.375rem',
                    background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '0.5rem', padding: '0.375rem 0.625rem',
                    fontSize: '0.78rem', color: 'var(--text-secondary)',
                    maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                    {f.name}
                  </div>
                )}
                <button
                  onClick={() => removeAttachment(i)}
                  style={{
                    position: 'absolute', top: -6, right: -6,
                    width: 18, height: 18, borderRadius: '50%',
                    background: 'var(--bg-hover)', border: '1.5px solid var(--bg-input)',
                    color: 'var(--text-primary)', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '0.65rem', lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Main input container */}
        <div style={{
          background: 'var(--bg-input)',
          border: `1px solid ${focused ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.08)'}`,
          borderRadius: attachments.length > 0 ? '0 0 1rem 1rem' : '1rem',
          transition: 'border-color 0.2s',
        }}>

          {/* Textarea row */}
          <div style={{ display: 'flex', alignItems: 'center', padding: '0.625rem 0.75rem 0.375rem 1rem', gap: '0.5rem' }}>
            <textarea
              ref={textareaRef}
              rows={1}
              value={value}
              onChange={e => setValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder="How can I help you today?"
              style={{
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                resize: 'none', color: 'var(--text-primary)',
                fontSize: '0.9375rem', lineHeight: 1.65, fontFamily: 'inherit',
                overflowY: 'hidden', maxHeight: 200,
                caretColor: 'var(--accent)',
                paddingTop: '0.1rem',
                userSelect: 'text',
              }}
            />
          </div>

          {/* Bottom toolbar */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.25rem 0.625rem 0.5rem',
          }}>
            {/* Left: attach + feature toggles */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', position: 'relative' }} ref={attachMenuRef}>
              {/* Attach button */}
              <button
                title="Attach files"
                onClick={() => setAttachMenuOpen(v => !v)}
                style={{
                  width: 32, height: 32, borderRadius: '0.5rem',
                  border: `1px solid ${attachMenuOpen ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.1)'}`,
                  background: attachMenuOpen ? 'var(--bg-hover)' : 'transparent',
                  color: 'var(--text-secondary)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                onMouseLeave={e => { if (!attachMenuOpen) { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' } }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M12 5v14M5 12h14"/>
                </svg>
              </button>

              {/* Attach dropdown */}
              {attachMenuOpen && (
                <div style={{
                  position: 'absolute', bottom: 'calc(100% + 8px)', left: 0,
                  background: 'var(--bg-sidebar)', border: '1px solid var(--border)',
                  borderRadius: '0.75rem', overflow: 'hidden',
                  boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
                  animation: 'fadeIn 0.1s ease', zIndex: 100,
                  minWidth: 180,
                }}>
                  {[
                    { label: 'Upload Image', icon: '🖼', isImage: true },
                    { label: 'Upload File', icon: '📄', isImage: false },
                  ].map(({ label, icon, isImage }) => (
                    <button
                      key={label}
                      onClick={() => { if (isImage) imageInputRef.current?.click(); else fileInputRef.current?.click() }}
                      style={{
                        width: '100%', padding: '0.6rem 0.875rem',
                        background: 'transparent', border: 'none',
                        color: 'var(--text-secondary)', cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: '0.625rem',
                        fontSize: '0.875rem', textAlign: 'left',
                        transition: 'background 0.1s, color 0.1s',
                      }}
                      onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
                    >
                      <span>{icon}</span>
                      {label}
                    </button>
                  ))}
                </div>
              )}

              {/* Web Search toggle */}
              <button
                title={webSearch ? 'Disable web search' : 'Enable web search'}
                onClick={() => setWebSearch(v => !v)}
                style={{
                  height: 32, padding: '0 0.625rem',
                  borderRadius: '0.5rem',
                  border: `1px solid ${webSearch ? 'rgba(59,130,246,0.4)' : 'rgba(255,255,255,0.1)'}`,
                  background: webSearch ? 'rgba(59,130,246,0.12)' : 'transparent',
                  color: webSearch ? 'var(--accent)' : 'var(--text-muted)',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: '0.375rem',
                  fontSize: '0.75rem', fontWeight: 500,
                  transition: 'all 0.15s',
                }}
                onMouseEnter={e => { if (!webSearch) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
                onMouseLeave={e => { if (!webSearch) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)' }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"/>
                  <line x1="2" y1="12" x2="22" y2="12"/>
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                </svg>
                Web
              </button>
            </div>

            {/* Right: model selector + mic + send/stop */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>

              {/* Model selector */}
              <div style={{ position: 'relative' }} ref={modelMenuRef}>
                <button
                  title="Select model"
                  onClick={() => setModelMenuOpen(v => !v)}
                  style={{
                    height: 32, padding: '0 0.5rem 0 0.625rem',
                    borderRadius: '0.5rem',
                    border: '1px solid transparent',
                    background: modelMenuOpen ? 'var(--bg-hover)' : 'transparent',
                    color: 'var(--text-secondary)', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: '0.3rem',
                    fontSize: '0.78rem', fontWeight: 500,
                    transition: 'background 0.15s, color 0.15s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                  onMouseLeave={e => { if (!modelMenuOpen) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)' }}
                >
                  {activeModelLabel}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    style={{ transform: modelMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
                    <polyline points="6 9 12 15 18 9"/>
                  </svg>
                </button>

                {/* Model dropdown */}
                {modelMenuOpen && (
                  <div style={{
                    position: 'absolute', bottom: 'calc(100% + 8px)', right: 0,
                    background: 'var(--bg-sidebar)', border: '1px solid var(--border)',
                    borderRadius: '0.75rem', overflow: 'hidden',
                    boxShadow: '0 8px 28px rgba(0,0,0,0.55)',
                    animation: 'dropDown 0.12s ease', zIndex: 100,
                    width: 280, padding: '0.35rem',
                    maxHeight: 420, overflowY: 'auto',
                  }}>
                    {Array.from(new Set(models.map(m => m.provider ?? 'anthropic'))).map(provider => (
                      <div key={provider}>
                        <div style={{
                          padding: '0.5rem 0.625rem 0.25rem',
                          fontSize: '0.66rem', fontWeight: 600,
                          letterSpacing: '0.08em', textTransform: 'uppercase',
                          color: 'var(--text-muted)',
                        }}>
                          {PROVIDER_LABELS[provider] ?? provider}
                        </div>
                        {models.filter(m => (m.provider ?? 'anthropic') === provider).map(m => {
                          const selected = m.id === settings.model
                          return (
                            <button
                              key={m.id}
                              onClick={() => { update({ model: m.id }); setModelMenuOpen(false) }}
                              style={{
                                width: '100%', padding: '0.55rem 0.625rem',
                                background: selected ? 'var(--bg-active)' : 'transparent',
                                border: 'none', borderRadius: '0.5rem',
                                cursor: 'pointer', textAlign: 'left',
                                display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
                                transition: 'background 0.1s',
                              }}
                              onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)' }}
                              onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
                            >
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{
                                  fontSize: '0.83rem', fontWeight: 500,
                                  color: selected ? 'var(--accent)' : 'var(--text-primary)',
                                }}>
                                  {shortModelName(m.name)}
                                </div>
                                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.1rem', lineHeight: 1.4 }}>
                                  {m.description}
                                </div>
                              </div>
                              {selected && (
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '0.15rem' }}>
                                  <polyline points="20 6 9 17 4 12"/>
                                </svg>
                              )}
                            </button>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {!state.isStreaming && (
                <button
                  title={listening ? 'Stop listening' : 'Voice input'}
                  onClick={handleVoiceInput}
                  style={{
                    width: 32, height: 32, borderRadius: '50%',
                    border: listening ? '1px solid rgba(239,68,68,0.4)' : 'none',
                    background: listening ? 'rgba(239,68,68,0.1)' : 'transparent',
                    color: listening ? '#ef4444' : 'var(--text-muted)',
                    cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { if (!listening) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                  onMouseLeave={e => { if (!listening) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)' }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill={listening ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                    <line x1="12" y1="19" x2="12" y2="23"/>
                    <line x1="8" y1="23" x2="16" y2="23"/>
                  </svg>
                </button>
              )}

              {state.isStreaming ? (
                <button
                  onClick={onStop}
                  title="Stop generating"
                  style={{
                    width: 34, height: 34, borderRadius: '50%',
                    border: 'none', background: 'var(--text-primary)',
                    color: '#1a1a1a', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'transform 0.1s',
                  }}
                  onMouseDown={e => (e.currentTarget as HTMLButtonElement).style.transform = 'scale(0.9)'}
                  onMouseUp={e => (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)'}
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="4" y="4" width="16" height="16" rx="2"/>
                  </svg>
                </button>
              ) : (
                <button
                  onClick={submit}
                  disabled={!canSend}
                  title="Send message"
                  style={{
                    width: 34, height: 34, borderRadius: '50%',
                    border: 'none',
                    background: canSend ? 'var(--text-primary)' : 'rgba(255,255,255,0.1)',
                    color: canSend ? '#1a1a1a' : 'rgba(255,255,255,0.3)',
                    cursor: canSend ? 'pointer' : 'default',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'background 0.15s, transform 0.1s',
                  }}
                  onMouseDown={e => { if (canSend) (e.currentTarget as HTMLButtonElement).style.transform = 'scale(0.9)' }}
                  onMouseUp={e => { if (canSend) (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)' }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M12 19V5M5 12l7-7 7 7"/>
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Footer hint */}
        <p style={{ textAlign: 'center', marginTop: '0.5rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          {profile?.name ?? 'AI'} can make mistakes. Enter to send · Shift+Enter for new line
        </p>
      </div>

      {/* Hidden file inputs */}
      <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={e => handleFileSelect(e, false)} />
      <input ref={imageInputRef} type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={e => handleFileSelect(e, true)} />
    </div>
  )
}
