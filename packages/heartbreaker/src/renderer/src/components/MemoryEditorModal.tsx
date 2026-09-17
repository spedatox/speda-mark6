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
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error' | 'conflict'>('idle')
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
    setStatusMsg('Kayıt yapılıyor...')
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
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        background: 'rgba(3, 6, 10, 0.82)',
        backdropFilter: 'blur(22px)',
        WebkitBackdropFilter: 'blur(22px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px',
        animation: 'hbFadeIn 0.22s ease',
      }}
    >
      {/* Editor Window Container — Authentic Stark Fluid Glass */}
      <div
        className="glass"
        style={{
          width: '95vw',
          maxWidth: 1260,
          height: '90vh',
          maxHeight: 900,
          borderRadius: 16,
          background: 'linear-gradient(145deg, rgba(20, 24, 33, 0.82) 0%, rgba(10, 13, 19, 0.95) 100%), var(--glass-fill)',
          backdropFilter: 'blur(28px) saturate(140%)',
          WebkitBackdropFilter: 'blur(28px) saturate(140%)',
          border: '1px solid rgba(255, 255, 255, 0.16)',
          boxShadow: '0 28px 72px rgba(0, 0, 0, 0.85), inset 0 1px 0 0 rgba(255, 255, 255, 0.28), inset 0 -1px 0 0 rgba(255, 255, 255, 0.06), 0 0 40px rgba(95, 204, 230, 0.12)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          fontFamily: 'var(--font-read), -apple-system, sans-serif',
          color: '#e6edf3',
          position: 'relative',
        }}
      >
        {/* Title Bar (Stark Fluid Glass HUD Style) */}
        <div
          style={{
            height: 42,
            flexShrink: 0,
            background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.06) 0%, rgba(0, 0, 0, 0.22) 100%)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
            userSelect: 'none',
          }}
        >
          {/* Left: Window Title & Breadcrumb HUD */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: isDirty ? 'var(--hb-amber, #f2b75c)' : 'var(--hb-cyan-bright, #5fcce6)',
                boxShadow: isDirty ? '0 0 10px #f2b75c' : '0 0 10px var(--hb-cyan-bright, #5fcce6)',
                transition: 'all 0.2s',
              }}
            />
            <span
              style={{
                fontFamily: "'Rajdhani', sans-serif",
                fontSize: '0.85rem',
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#fff',
                textTransform: 'uppercase',
              }}
            >
              SPEDA_MK_VI // FLUID_EDITOR // {filename.toUpperCase()}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono), monospace',
                fontSize: '0.64rem',
                color: 'var(--hb-cyan-bright, #5fcce6)',
                background: 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.12)',
                border: '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.3)',
                padding: '1px 8px',
                borderRadius: 4,
              }}
            >
              {relativePath}
            </span>
          </div>

          {/* Right: Window Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span
              style={{
                fontFamily: 'var(--font-mono), monospace',
                fontSize: '0.62rem',
                color: 'var(--hb-text-faint, #5f6368)',
                letterSpacing: '0.08em',
              }}
            >
              MARKDOWN_SYNTHESIS · LIVE_BUFFER
            </span>
            <button
              onClick={onClose}
              title="Kapat (Esc)"
              className="glass glass-interactive"
              style={{
                width: 28,
                height: 28,
                borderRadius: 6,
                background: 'transparent',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                color: '#9aa0a6',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'rgba(232, 17, 35, 0.35)'
                e.currentTarget.style.borderColor = 'rgba(232, 17, 35, 0.8)'
                e.currentTarget.style.color = '#fff'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.12)'
                e.currentTarget.style.color = '#9aa0a6'
              }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Action Ribbon / Glass Tab Bar */}
        <div
          style={{
            height: 46,
            flexShrink: 0,
            background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.03) 0%, rgba(0, 0, 0, 0.12) 100%)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
          }}
        >
          {/* Active Stark Tab */}
          <div style={{ display: 'flex', alignItems: 'center', height: '100%' }}>
            <div
              className="glass"
              style={{
                height: '100%',
                padding: '0 18px',
                background: 'rgba(255, 255, 255, 0.05)',
                borderTop: '2px solid var(--hb-cyan-bright, #5fcce6)',
                borderRight: '1px solid rgba(255, 255, 255, 0.08)',
                borderLeft: '1px solid rgba(255, 255, 255, 0.08)',
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                fontSize: '0.78rem',
                color: '#fff',
                fontWeight: 600,
                letterSpacing: '0.02em',
                boxShadow: 'inset 0 1px 0 0 rgba(255, 255, 255, 0.15)',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan-bright, #5fcce6)" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10 9 9 9 8 9" />
              </svg>
              <span>{filename}</span>
              {isDirty && (
                <span
                  title="Kaydedilmemiş değişiklikler mevcut"
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: 'var(--hb-amber, #f2b75c)',
                    boxShadow: '0 0 8px #f2b75c',
                    display: 'inline-block',
                  }}
                />
              )}
            </div>
          </div>

          {/* Action Toolbar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* View Mode Switcher */}
            <div
              className="glass"
              style={{
                display: 'flex',
                alignItems: 'center',
                background: 'rgba(0, 0, 0, 0.45)',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                borderRadius: 8,
                padding: 3,
              }}
            >
              <button
                onClick={() => setViewMode('code')}
                title="Yalnızca Kod Düzenleyici"
                style={{
                  background: viewMode === 'code' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.22)' : 'transparent',
                  color: viewMode === 'code' ? 'var(--hb-cyan-bright, #5fcce6)' : '#9aa0a6',
                  border: viewMode === 'code' ? '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.4)' : '1px solid transparent',
                  padding: '4px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: '0.74rem',
                  fontWeight: 600,
                  transition: 'all 0.15s ease',
                }}
              >
                KOD
              </button>
              <button
                onClick={() => setViewMode('split')}
                title="İkili Bölünmüş Görünüm (Kod + Canlı Markdown)"
                style={{
                  background: viewMode === 'split' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.22)' : 'transparent',
                  color: viewMode === 'split' ? 'var(--hb-cyan-bright, #5fcce6)' : '#9aa0a6',
                  border: viewMode === 'split' ? '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.4)' : '1px solid transparent',
                  padding: '4px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: '0.74rem',
                  fontWeight: 600,
                  transition: 'all 0.15s ease',
                }}
              >
                İKİLİ BÖLME
              </button>
              <button
                onClick={() => setViewMode('preview')}
                title="Yalnızca Markdown Önizleme"
                style={{
                  background: viewMode === 'preview' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.22)' : 'transparent',
                  color: viewMode === 'preview' ? 'var(--hb-cyan-bright, #5fcce6)' : '#9aa0a6',
                  border: viewMode === 'preview' ? '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.4)' : '1px solid transparent',
                  padding: '4px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: '0.74rem',
                  fontWeight: 600,
                  transition: 'all 0.15s ease',
                }}
              >
                ÖNİZLEME
              </button>
            </div>

            {/* Word wrap toggle */}
            <button
              onClick={() => setWordWrap(!wordWrap)}
              title={wordWrap ? 'Satır Kaydırmayı Kapat' : 'Satır Kaydırmayı Aç'}
              className="glass glass-interactive"
              style={{
                background: wordWrap ? 'rgba(255, 255, 255, 0.08)' : 'rgba(255, 255, 255, 0.03)',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                color: wordWrap ? '#fff' : '#8b949e',
                borderRadius: 7,
                padding: '5px 10px',
                fontSize: '0.74rem',
                fontWeight: 500,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                transition: 'all 0.15s ease',
              }}
            >
              <span>↩</span> Kaydır
            </button>

            {/* Revisions History Button */}
            <button
              onClick={handleOpenHistory}
              title="Sürüm Geçmişini Görüntüle"
              className="glass glass-interactive"
              style={{
                background: showHistory ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.18)' : 'rgba(255, 255, 255, 0.03)',
                border: showHistory ? '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.45)' : '1px solid rgba(255, 255, 255, 0.12)',
                color: showHistory ? 'var(--hb-cyan-bright, #5fcce6)' : '#c9d1d9',
                borderRadius: 7,
                padding: '5px 12px',
                fontSize: '0.74rem',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                transition: 'all 0.15s ease',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <span>Geçmiş</span>
            </button>

            {/* Save Button */}
            <button
              onClick={handleSave}
              disabled={saving || !isDirty}
              title="Değişiklikleri Kaydet (Ctrl+S)"
              style={{
                background: isDirty
                  ? 'linear-gradient(135deg, rgba(95, 204, 230, 0.95) 0%, rgba(54, 171, 202, 0.95) 100%)'
                  : 'rgba(255, 255, 255, 0.05)',
                color: isDirty ? '#05080c' : '#5f6368',
                border: isDirty ? '1px solid rgba(255, 255, 255, 0.3)' : '1px solid rgba(255, 255, 255, 0.08)',
                borderRadius: 7,
                padding: '5px 16px',
                fontSize: '0.76rem',
                fontWeight: 700,
                letterSpacing: '0.04em',
                cursor: isDirty && !saving ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                boxShadow: isDirty ? '0 0 16px rgba(95, 204, 230, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.4)' : 'none',
                transition: 'all 0.2s cubic-bezier(0.2, 0.8, 0.2, 1)',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              <span>{saving ? 'KAYDEDİLİYOR…' : 'KAYDET'}</span>
            </button>
          </div>
        </div>

        {/* Main Work Area */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0, position: 'relative', overflow: 'hidden' }}>
          {/* Revisions Drawer (Frosted Stark Glass) */}
          {showHistory && (
            <div
              className="glass"
              style={{
                position: 'absolute',
                right: 0,
                top: 0,
                bottom: 0,
                width: 360,
                zIndex: 35,
                background: 'linear-gradient(180deg, rgba(16, 20, 28, 0.94) 0%, rgba(10, 13, 18, 0.97) 100%)',
                backdropFilter: 'blur(28px)',
                WebkitBackdropFilter: 'blur(28px)',
                borderLeft: '1px solid rgba(95, 204, 230, 0.3)',
                boxShadow: '-12px 0 36px rgba(0, 0, 0, 0.75)',
                display: 'flex',
                flexDirection: 'column',
                animation: 'hbFadeIn 0.2s ease',
              }}
            >
              <div
                style={{
                  height: 42,
                  padding: '0 16px',
                  borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  background: 'rgba(255, 255, 255, 0.02)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      background: 'var(--hb-cyan-bright, #5fcce6)',
                      boxShadow: '0 0 8px var(--hb-cyan-bright, #5fcce6)',
                    }}
                  />
                  <span
                    style={{
                      fontFamily: "'Rajdhani', sans-serif",
                      fontWeight: 700,
                      fontSize: '0.82rem',
                      letterSpacing: '0.08em',
                      color: '#fff',
                      textTransform: 'uppercase',
                    }}
                  >
                    Sürüm Geçmişi (Revisions)
                  </span>
                </div>
                <button
                  onClick={() => setShowHistory(false)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#8b949e',
                    cursor: 'pointer',
                    fontSize: '0.85rem',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.color = '#fff')}
                  onMouseLeave={e => (e.currentTarget.style.color = '#8b949e')}
                >
                  ✕
                </button>
              </div>

              <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
                {loadingRevs && (
                  <p style={{ padding: '24px', fontSize: '0.75rem', color: '#8b949e', textAlign: 'center' }}>
                    Sürümler yükleniyor...
                  </p>
                )}
                {!loadingRevs && (!revs || revs.length === 0) && (
                  <p style={{ padding: '24px', fontSize: '0.75rem', color: '#8b949e', textAlign: 'center' }}>
                    Henüz kayıtlı bir sürüm geçmişi bulunmuyor.
                  </p>
                )}
                {!loadingRevs &&
                  revs?.map(r => (
                    <div
                      key={r.id}
                      className="glass"
                      style={{
                        background: 'rgba(255, 255, 255, 0.03)',
                        border: '1px solid rgba(255, 255, 255, 0.08)',
                        borderRadius: 8,
                        padding: '10px 12px',
                        marginBottom: 10,
                        fontSize: '0.74rem',
                        transition: 'all 0.15s ease',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span
                          style={{
                            fontWeight: 700,
                            letterSpacing: '0.05em',
                            color: r.author === 'owner' ? '#f2b75c' : 'var(--hb-cyan-bright, #5fcce6)',
                          }}
                        >
                          {r.author.toUpperCase()}
                        </span>
                        <span style={{ color: '#8b949e', fontSize: '0.68rem', fontFamily: 'monospace' }}>
                          {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                        </span>
                      </div>
                      <div style={{ color: '#c9d1d9', marginBottom: 8, fontSize: '0.72rem' }}>
                        <span style={{ color: '#8b949e' }}>İşlem: </span>
                        {r.action}
                      </div>
                      <button
                        onClick={() => handleRestore(r.id)}
                        className="glass glass-interactive"
                        style={{
                          background: 'rgba(255, 255, 255, 0.06)',
                          border: '1px solid rgba(255, 255, 255, 0.14)',
                          color: '#fff',
                          borderRadius: 6,
                          padding: '5px 10px',
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                          width: '100%',
                          transition: 'all 0.15s ease',
                        }}
                        onMouseEnter={e => {
                          e.currentTarget.style.background = 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.25)'
                          e.currentTarget.style.borderColor = 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.5)'
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.background = 'rgba(255, 255, 255, 0.06)'
                          e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.14)'
                        }}
                      >
                        Bu Sürüme Geri Yükle ↺
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
                display: 'flex',
                minHeight: 0,
                background: 'rgba(7, 10, 15, 0.45)',
                borderRight: viewMode === 'split' ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
                position: 'relative',
              }}
            >
              {/* Line Numbers Gutter */}
              <div
                ref={lineGutterRef}
                style={{
                  width: 50,
                  flexShrink: 0,
                  background: 'rgba(0, 0, 0, 0.35)',
                  borderRight: '1px solid rgba(255, 255, 255, 0.06)',
                  padding: '14px 8px 14px 0',
                  textAlign: 'right',
                  fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                  fontSize: '0.76rem',
                  lineHeight: '1.5rem',
                  color: 'rgba(255, 255, 255, 0.2)',
                  userSelect: 'none',
                  overflow: 'hidden',
                }}
              >
                {Array.from({ length: lineCount }).map((_, i) => (
                  <div
                    key={i}
                    style={{
                      height: '1.5rem',
                      color: cursorPos.line === i + 1 ? 'var(--hb-cyan-bright, #5fcce6)' : 'rgba(255, 255, 255, 0.22)',
                      fontWeight: cursorPos.line === i + 1 ? 700 : 400,
                      textShadow: cursorPos.line === i + 1 ? '0 0 8px rgba(95, 204, 230, 0.6)' : 'none',
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
                  flex: 1,
                  height: '100%',
                  background: 'transparent',
                  color: '#e6edf3',
                  caretColor: 'var(--hb-cyan-bright, #5fcce6)',
                  border: 'none',
                  outline: 'none',
                  resize: 'none',
                  padding: '14px 18px',
                  fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                  fontSize: '0.78rem',
                  lineHeight: '1.5rem',
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
                overflowY: 'auto',
                padding: '18px 26px',
                background: 'rgba(12, 16, 22, 0.55)',
                backdropFilter: 'blur(16px)',
                WebkitBackdropFilter: 'blur(16px)',
              }}
            >
              <div
                style={{
                  marginBottom: 14,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  color: 'var(--hb-text-dim, #9aa0a6)',
                  fontSize: '0.72rem',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  fontFamily: "'Rajdhani', sans-serif",
                  fontWeight: 600,
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: 'var(--hb-cyan-bright, #5fcce6)',
                    boxShadow: '0 0 6px var(--hb-cyan-bright, #5fcce6)',
                  }}
                />
                <span>DOSSIER MARKDOWN CANLI ÖNİZLEME</span>
              </div>
              <div className="hb-mem-md">
                <ReactMarkdown remarkPlugins={MEM_REMARK_PLUGINS}>
                  {content.trim() ? content : '*Dosya içeriği boş.*'}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </div>

        {/* Bottom Status Bar (Stark Fluid Glass HUD Style) */}
        <div
          style={{
            height: 30,
            flexShrink: 0,
            background: 'linear-gradient(90deg, rgba(14, 18, 25, 0.95) 0%, rgba(20, 26, 36, 0.95) 100%)',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            color: 'var(--hb-text-dim, #9aa0a6)',
            fontSize: '0.72rem',
            fontWeight: 500,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
            userSelect: 'none',
          }}
        >
          {/* Left: Position & Stats */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <span style={{ fontFamily: 'var(--font-mono), monospace' }}>
              Satır {cursorPos.line}, Sütun {cursorPos.col}
            </span>
            <span style={{ fontFamily: 'var(--font-mono), monospace' }}>
              {content.length} karakter
            </span>
            <span style={{ fontFamily: 'var(--font-mono), monospace' }}>
              {lineCount} satır
            </span>
            {isDirty ? (
              <span
                style={{
                  color: '#f2b75c',
                  background: 'rgba(242, 183, 92, 0.15)',
                  border: '1px solid rgba(242, 183, 92, 0.3)',
                  padding: '1px 7px',
                  borderRadius: 4,
                  fontWeight: 600,
                }}
              >
                ● Kaydedilmedi
              </span>
            ) : (
              <span
                style={{
                  color: 'var(--hb-cyan-bright, #5fcce6)',
                  background: 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.12)',
                  border: '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.25)',
                  padding: '1px 7px',
                  borderRadius: 4,
                  fontWeight: 600,
                }}
              >
                ✓ Güncel
              </span>
            )}
            {saveStatus === 'saving' && (
              <span style={{ color: 'var(--hb-cyan-bright, #5fcce6)', fontWeight: 600 }}>
                {statusMsg || 'Kaydediliyor...'}
              </span>
            )}
            {saveStatus === 'error' && (
              <span style={{ color: '#f87171', fontWeight: 600 }}>
                {statusMsg || 'Kaydetme hatası!'}
              </span>
            )}
            {saveStatus === 'conflict' && (
              <span style={{ color: '#fb923c', fontWeight: 600 }}>
                {statusMsg}
              </span>
            )}
            {saveStatus === 'saved' && statusMsg && (
              <span style={{ color: '#4ade80', fontWeight: 600 }}>
                {statusMsg}
              </span>
            )}
          </div>

          {/* Right: Format Info */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontFamily: 'var(--font-mono), monospace', fontSize: '0.66rem' }}>
            <span>UTF-8</span>
            <span>MARKDOWN</span>
            <span style={{ color: 'var(--hb-cyan-bright, #5fcce6)' }}>SPEDA // FLUID_VAULT</span>
          </div>
        </div>
      </div>
    </div>
  )
}
