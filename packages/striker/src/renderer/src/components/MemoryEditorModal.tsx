// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect, useRef, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { commitMemoryFile, fetchMemoryRevisions, restoreMemoryRevision } from '../lib/api'
import type { MemoryFileInfo, MemoryRevisionInfo } from '../lib/api'
import type { AppConfig } from '../lib/types'

export interface MemoryEditorModalProps {
  config: AppConfig
  file: MemoryFileInfo
  onClose: () => void
  onSave?: (updated: MemoryFileInfo) => void
}

const MEM_REMARK_PLUGINS = [remarkGfm]

export default function MemoryEditorModal({
  config,
  file,
  onClose,
  onSave,
}: MemoryEditorModalProps) {
  const [content, setContent] = useState(file.content)
  const [lastSavedContent, setLastSavedContent] = useState(file.content)
  const [saving, setSaving] = useState(false)
  const [, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error' | 'conflict'>('idle')
  const [statusMsg, setStatusMsg] = useState<string>('')
  const [viewMode, setViewMode] = useState<'code' | 'split' | 'preview'>('split')
  const [showHistory, setShowHistory] = useState(false)
  const [revs, setRevs] = useState<MemoryRevisionInfo[] | null>(null)
  const [loadingRevs, setLoadingRevs] = useState(false)
  const [cursorPos, setCursorPos] = useState({ line: 1, col: 1 })
  const [wordWrap, setWordWrap] = useState(true)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const lineGutterRef = useRef<HTMLDivElement>(null)

  const isDirty = content !== lastSavedContent

  // Calculate line numbers
  const lines = useMemo(() => {
    return content.split('\n')
  }, [content])

  const lineCount = lines.length

  // Track cursor position
  const handleSelect = () => {
    if (!textareaRef.current) return
    const selStart = textareaRef.current.selectionStart
    const textBefore = content.substring(0, selStart)
    const lineIndex = textBefore.split('\n').length
    const lastNewline = textBefore.lastIndexOf('\n')
    const colIndex = lastNewline === -1 ? selStart + 1 : selStart - lastNewline
    setCursorPos({ line: lineIndex, col: colIndex })
  }

  // Synchronize gutter scrolling with textarea
  const handleScroll = () => {
    if (textareaRef.current && lineGutterRef.current) {
      lineGutterRef.current.scrollTop = textareaRef.current.scrollTop
    }
  }

  // Handle Tab key in editor
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault()
      const ta = textareaRef.current
      if (!ta) return
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const newText = content.substring(0, start) + '  ' + content.substring(end)
      setContent(newText)
      setTimeout(() => {
        ta.selectionStart = ta.selectionEnd = start + 2
      }, 0)
    }
  }

  // Commit changes
  const handleSave = async () => {
    if (saving) return
    setSaving(true)
    setSaveStatus('saving')
    setStatusMsg('Kaydediliyor...')
    try {
      const res = await commitMemoryFile(config, file.path, content, file.updated_at)
      if ('conflict' in res) {
        setSaveStatus('conflict')
        setStatusMsg('Çakışma: Dosya sunucuda değişmiş! Lütfen kontrol edin.')
        if (res.current) {
          file.updated_at = res.current.updated_at
        }
      } else {
        setLastSavedContent(content)
        setSaveStatus('saved')
        setStatusMsg('Tüm değişiklikler kaydedildi.')
        if (onSave) onSave(res)
        setTimeout(() => {
          setSaveStatus('idle')
          setStatusMsg('')
        }, 3000)
      }
    } catch (err: unknown) {
      setSaveStatus('error')
      const msg = err instanceof Error ? err.message : 'Kaydetme başarısız oldu'
      setStatusMsg(msg)
    } finally {
      setSaving(false)
    }
  }

  // Open revisions
  const handleOpenHistory = async () => {
    if (showHistory) {
      setShowHistory(false)
      return
    }
    setLoadingRevs(true)
    setShowHistory(true)
    try {
      const r = await fetchMemoryRevisions(config, file.path)
      setRevs(r)
    } catch {
      setRevs([])
    } finally {
      setLoadingRevs(false)
    }
  }

  const handleRestore = async (id: number) => {
    try {
      setStatusMsg('Sürüm geri yükleniyor...')
      const restored = await restoreMemoryRevision(config, id)
      setContent(restored.content)
      setLastSavedContent(restored.content)
      setShowHistory(false)
      setSaveStatus('saved')
      setStatusMsg('Sürüm başarıyla geri yüklendi.')
      if (onSave) onSave(restored)
    } catch {
      setStatusMsg('Geri yükleme başarısız oldu.')
    }
  }

  // Global keyboard shortcuts (Ctrl+S / Esc)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault()
        handleSave()
      } else if (e.key === 'Escape') {
        if (showHistory) {
          setShowHistory(false)
        } else {
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [content, saving, showHistory, onClose]) // eslint-disable-line react-hooks/exhaustive-deps

  const filename = file.path.split('/').pop() || file.path
  const relativePath = file.path.replace(/^\/memories\//, '')

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: 'rgba(5, 7, 10, 0.82)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px', animation: 'hbFadeIn 0.2s ease',
      }}
    >
      {/* Editor Window Container */}
      <div
        style={{
          width: '94vw', maxWidth: 1200, height: '88vh', maxHeight: 880,
          background: '#14171c',
          border: '1px solid rgba(var(--hb-accent-rgb), 0.35)',
          borderRadius: 8,
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.75), 0 0 0 1px rgba(255,255,255,0.06)',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          fontFamily: 'var(--font-read), -apple-system, sans-serif',
          color: '#d1d7e0',
        }}
      >
        {/* VS Code / Notepad Title Bar */}
        <div
          style={{
            height: 38, flexShrink: 0,
            background: '#0d1014',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 12px', userSelect: 'none',
          }}
        >
          {/* Left: Window Title & App Icon */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#e6edf3', letterSpacing: '0.02em' }}>
              {filename} — Hafıza Düzenleyicisi
            </span>
            <span style={{ fontSize: '0.72rem', color: '#7d8590', fontFamily: 'monospace' }}>
              ({relativePath})
            </span>
          </div>

          {/* Right: Window Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={onClose}
              title="Kapat (Esc)"
              style={{
                background: 'transparent', border: 'none', color: '#8b949e',
                cursor: 'pointer', padding: '4px 8px', borderRadius: 4,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = '#e81123'
                e.currentTarget.style.color = '#fff'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = '#8b949e'
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        {/* Tab Bar & Action Ribbon */}
        <div
          style={{
            height: 42, flexShrink: 0,
            background: '#161a20',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 12px',
          }}
        >
          {/* Active Tab */}
          <div style={{ display: 'flex', alignItems: 'center', height: '100%' }}>
            <div
              style={{
                height: '100%', padding: '0 16px',
                background: '#1c2128',
                borderTop: '2px solid var(--hb-cyan)',
                borderRight: '1px solid rgba(255, 255, 255, 0.08)',
                display: 'flex', alignItems: 'center', gap: 8,
                fontSize: '0.8rem', color: '#f0f6fc', fontWeight: 500,
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              <span>{filename}</span>
              {isDirty && (
                <span
                  title="Kaydedilmemiş değişiklikler var"
                  style={{
                    width: 7, height: 7, borderRadius: '50%',
                    background: 'var(--hb-amber, #e3b341)',
                    display: 'inline-block',
                  }}
                />
              )}
            </div>
          </div>

          {/* Action Toolbar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* View Mode Switcher */}
            <div
              style={{
                display: 'flex', alignItems: 'center',
                background: '#0d1117', border: '1px solid rgba(255, 255, 255, 0.12)',
                borderRadius: 4, padding: 2,
              }}
            >
              <button
                onClick={() => setViewMode('code')}
                title="Yalnızca Kod Düzenleyici"
                style={{
                  background: viewMode === 'code' ? 'rgba(var(--hb-accent-rgb), 0.25)' : 'transparent',
                  color: viewMode === 'code' ? 'var(--hb-cyan-bright)' : '#8b949e',
                  border: 'none', padding: '4px 9px', borderRadius: 3,
                  cursor: 'pointer', fontSize: '0.75rem', fontWeight: 500,
                }}
              >
                Kod
              </button>
              <button
                onClick={() => setViewMode('split')}
                title="Bölünmüş Görünüm (Kod + Canlı Markdown)"
                style={{
                  background: viewMode === 'split' ? 'rgba(var(--hb-accent-rgb), 0.25)' : 'transparent',
                  color: viewMode === 'split' ? 'var(--hb-cyan-bright)' : '#8b949e',
                  border: 'none', padding: '4px 9px', borderRadius: 3,
                  cursor: 'pointer', fontSize: '0.75rem', fontWeight: 500,
                }}
              >
                İkili Bölme
              </button>
              <button
                onClick={() => setViewMode('preview')}
                title="Yalnızca Markdown Önizleme"
                style={{
                  background: viewMode === 'preview' ? 'rgba(var(--hb-accent-rgb), 0.25)' : 'transparent',
                  color: viewMode === 'preview' ? 'var(--hb-cyan-bright)' : '#8b949e',
                  border: 'none', padding: '4px 9px', borderRadius: 3,
                  cursor: 'pointer', fontSize: '0.75rem', fontWeight: 500,
                }}
              >
                Önizleme
              </button>
            </div>

            {/* Word wrap toggle */}
            <button
              onClick={() => setWordWrap(!wordWrap)}
              title={wordWrap ? 'Satır Kaydırmayı Kapat' : 'Satır Kaydırmayı Aç'}
              style={{
                background: wordWrap ? 'rgba(255,255,255,0.08)' : 'transparent',
                border: '1px solid rgba(255,255,255,0.1)',
                color: wordWrap ? '#f0f6fc' : '#8b949e',
                borderRadius: 4, padding: '4px 8px', fontSize: '0.74rem',
                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
              }}
            >
              ↩ Kaydır
            </button>

            {/* Revisions History Button */}
            <button
              onClick={handleOpenHistory}
              title="Sürüm Geçmişini Görüntüle"
              style={{
                background: showHistory ? 'rgba(var(--hb-accent-rgb), 0.2)' : 'transparent',
                border: '1px solid rgba(255,255,255,0.1)',
                color: showHistory ? 'var(--hb-cyan-bright)' : '#c9d1d9',
                borderRadius: 4, padding: '4px 10px', fontSize: '0.74rem',
                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5,
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              Geçmiş
            </button>

            {/* Save Button */}
            <button
              onClick={handleSave}
              disabled={saving || !isDirty}
              title="Kaydet (Ctrl+S)"
              style={{
                background: isDirty ? 'var(--hb-cyan, #36abca)' : 'rgba(255,255,255,0.06)',
                color: isDirty ? '#0a0d12' : '#6e7681',
                border: 'none', borderRadius: 4,
                padding: '4px 14px', fontSize: '0.76rem', fontWeight: 600,
                cursor: isDirty && !saving ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', gap: 6,
                boxShadow: isDirty ? '0 2px 8px rgba(54, 171, 202, 0.35)' : 'none',
                transition: 'all 0.15s',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              {saving ? 'KAYDEDİLİYOR…' : 'KAYDET'}
            </button>
          </div>
        </div>

        {/* Main Work Area */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0, position: 'relative', overflow: 'hidden' }}>
          {/* Revisions Drawer */}
          {showHistory && (
            <div
              style={{
                position: 'absolute', right: 0, top: 0, bottom: 0, width: 340, zIndex: 30,
                background: '#12161c', borderLeft: '1px solid rgba(var(--hb-accent-rgb), 0.3)',
                boxShadow: '-8px 0 24px rgba(0,0,0,0.5)',
                display: 'flex', flexDirection: 'column',
              }}
            >
              <div
                style={{
                  height: 38, padding: '0 14px',
                  borderBottom: '1px solid rgba(255,255,255,0.08)',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  fontWeight: 600, fontSize: '0.78rem', color: '#e6edf3',
                }}
              >
                <span>Sürüm Geçmişi (Revisions)</span>
                <button
                  onClick={() => setShowHistory(false)}
                  style={{ background: 'transparent', border: 'none', color: '#8b949e', cursor: 'pointer' }}
                >
                  ✕
                </button>
              </div>

              <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
                {loadingRevs && (
                  <p style={{ padding: '16px', fontSize: '0.75rem', color: '#8b949e', textAlign: 'center' }}>
                    Sürümler yükleniyor...
                  </p>
                )}
                {!loadingRevs && (!revs || revs.length === 0) && (
                  <p style={{ padding: '16px', fontSize: '0.75rem', color: '#8b949e', textAlign: 'center' }}>
                    Henüz kayıtlı bir geçmiş yok.
                  </p>
                )}
                {!loadingRevs && revs?.map(r => (
                  <div
                    key={r.id}
                    style={{
                      background: '#171c23', border: '1px solid rgba(255,255,255,0.06)',
                      borderRadius: 4, padding: '8px 10px', marginBottom: 8,
                      fontSize: '0.74rem',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ color: r.author === 'owner' ? '#f2b75c' : 'var(--hb-cyan)' }}>
                        {r.author.toUpperCase()}
                      </span>
                      <span style={{ color: '#8b949e', fontSize: '0.7rem' }}>
                        {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                      </span>
                    </div>
                    <div style={{ color: '#c9d1d9', marginBottom: 6, fontSize: '0.72rem' }}>
                      İşlem: {r.action}
                    </div>
                    <button
                      onClick={() => handleRestore(r.id)}
                      style={{
                        background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.14)',
                        color: '#f0f6fc', borderRadius: 3, padding: '3px 8px', fontSize: '0.7rem',
                        cursor: 'pointer', width: '100%',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'rgba(var(--hb-accent-rgb), 0.3)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
                    >
                      Bu Sürüme Geri Yükle
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Editor Side (Code / Split) */}
          {(viewMode === 'code' || viewMode === 'split') && (
            <div
              style={{
                flex: viewMode === 'split' ? 1 : '1 1 100%',
                display: 'flex', minHeight: 0,
                background: '#12151a',
                borderRight: viewMode === 'split' ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
                position: 'relative',
              }}
            >
              {/* Line Numbers Gutter */}
              <div
                ref={lineGutterRef}
                style={{
                  width: 48, flexShrink: 0,
                  background: '#0d1014',
                  borderRight: '1px solid rgba(255, 255, 255, 0.06)',
                  padding: '12px 6px 12px 0',
                  textAlign: 'right',
                  fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                  fontSize: '0.78rem', lineHeight: '1.5rem',
                  color: '#484f58', userSelect: 'none',
                  overflow: 'hidden',
                }}
              >
                {Array.from({ length: lineCount }).map((_, i) => (
                  <div
                    key={i}
                    style={{
                      height: '1.5rem',
                      color: cursorPos.line === i + 1 ? 'var(--hb-cyan, #36abca)' : '#484f58',
                      fontWeight: cursorPos.line === i + 1 ? 600 : 400,
                    }}
                  >
                    {i + 1}
                  </div>
                ))}
              </div>

              {/* Code Textarea */}
              <textarea
                ref={textareaRef}
                value={content}
                onChange={e => setContent(e.target.value)}
                onSelect={handleSelect}
                onKeyUp={handleSelect}
                onClick={handleSelect}
                onScroll={handleScroll}
                onKeyDown={handleKeyDown}
                spellCheck={false}
                placeholder="Hafıza notlarınızı buraya yazın..."
                style={{
                  flex: 1, height: '100%',
                  background: 'transparent', color: '#e6edf3',
                  border: 'none', outline: 'none', resize: 'none',
                  padding: '12px 16px',
                  fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                  fontSize: '0.78rem', lineHeight: '1.5rem',
                  whiteSpace: wordWrap ? 'pre-wrap' : 'pre',
                  overflowX: wordWrap ? 'hidden' : 'auto',
                  overflowY: 'auto',
                  tabSize: 2,
                }}
              />
            </div>
          )}

          {/* Markdown Preview Side (Preview / Split) */}
          {(viewMode === 'preview' || viewMode === 'split') && (
            <div
              style={{
                flex: viewMode === 'split' ? 1 : '1 1 100%',
                overflowY: 'auto', padding: '16px 24px',
                background: '#151921',
              }}
            >
              <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6, color: '#8b949e', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                <span>Dossier Markdown Görünümü</span>
              </div>
              <div className="hb-mem-md">
                <ReactMarkdown remarkPlugins={MEM_REMARK_PLUGINS}>
                  {content.trim() ? content : '*Dosya içeriği boş.*'}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </div>

        {/* Bottom Status Bar (VS Code Style) */}
        <div
          style={{
            height: 26, flexShrink: 0,
            background: 'var(--hb-cyan, #36abca)',
            color: '#080c10',
            fontSize: '0.72rem', fontWeight: 600,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 14px', userSelect: 'none',
          }}
        >
          {/* Left: Position & Stats */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <span>Satır {cursorPos.line}, Sütun {cursorPos.col}</span>
            <span>{content.length} karakter</span>
            <span>{lineCount} satır</span>
            {isDirty ? (
              <span style={{ color: '#4a1500', background: 'rgba(255,255,255,0.4)', padding: '0 5px', borderRadius: 2 }}>
                ● Kaydedilmedi
              </span>
            ) : (
              <span>✓ Güncel</span>
            )}
            {statusMsg && <span style={{ color: '#161a20', fontWeight: 700 }}>— {statusMsg}</span>}
          </div>

          {/* Right: Format Info */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <span>UTF-8</span>
            <span>Markdown</span>
            <span>Speda Memory Bank</span>
          </div>
        </div>
      </div>
    </div>
  )
}
