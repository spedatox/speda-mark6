// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  fetchMemoryFiles,
  deleteMemoryFile,
  renameMemoryFile,
  createMemoryFile,
} from '../lib/api'
import type { MemoryFileInfo } from '../lib/api'
import type { AppConfig } from '../lib/types'
import MemoryEditorModal from './MemoryEditorModal'

export interface MemoryExplorerModalProps {
  config: AppConfig
  initialPath?: string | null
  onClose: () => void
  onFilesChanged?: () => void
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(isoStr: string | null): string {
  if (!isoStr) return '--'
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('tr-TR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return isoStr
  }
}

export default function MemoryExplorerModal({
  config,
  initialPath,
  onClose,
  onFilesChanged,
}: MemoryExplorerModalProps) {
  const [files, setFiles] = useState<MemoryFileInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [currentDir, setCurrentDir] = useState<string>('')
  const [history, setHistory] = useState<string[]>([''])
  const [historyIdx, setHistoryIdx] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedItem, setSelectedItem] = useState<{ type: 'folder' | 'file'; path: string; name: string } | null>(null)
  const [viewMode, setViewMode] = useState<'details' | 'tiles'>('details')
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set(['']))

  // Sub-modal states
  const [editorFile, setEditorFile] = useState<MemoryFileInfo | null>(null)
  const [renameDialog, setRenameDialog] = useState<{ open: boolean; item: { type: 'folder' | 'file'; path: string; name: string } | null; newName: string; error?: string }>({ open: false, item: null, newName: '' })
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; item: { type: 'folder' | 'file'; path: string; name: string } | null; loading?: boolean; error?: string }>({ open: false, item: null })
  const [newFileDialog, setNewFileDialog] = useState<{ open: boolean; filename: string; error?: string }>({ open: false, filename: '' })
  const [newFolderDialog, setNewFolderDialog] = useState<{ open: boolean; foldername: string; error?: string }>({ open: false, foldername: '' })

  // Load files
  const loadFiles = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchMemoryFiles(config)
      setFiles(data)
    } catch {
      setFiles([])
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  // If initialPath is provided, navigate to its directory
  useEffect(() => {
    if (initialPath) {
      const rel = initialPath.replace(/^\/memories\//, '')
      const parts = rel.split('/')
      if (parts.length > 1) {
        const dir = parts.slice(0, -1).join('/')
        setCurrentDir(dir)
        setHistory(['', dir])
        setHistoryIdx(1)
        // expand all parent dirs
        let curr = ''
        const exp = new Set<string>([''])
        for (const p of parts.slice(0, -1)) {
          curr = curr ? `${curr}/${p}` : p
          exp.add(curr)
        }
        setExpandedDirs(exp)
      }
    }
  }, [initialPath])

  // Navigation handlers
  const navigateTo = (dir: string) => {
    if (dir === currentDir) return
    const newHist = history.slice(0, historyIdx + 1)
    newHist.push(dir)
    setHistory(newHist)
    setHistoryIdx(newHist.length - 1)
    setCurrentDir(dir)
    setSelectedItem(null)
    // Expand this dir and parents
    setExpandedDirs(prev => {
      const next = new Set(prev)
      let curr = ''
      next.add('')
      for (const p of dir.split('/')) {
        if (!p) continue
        curr = curr ? `${curr}/${p}` : p
        next.add(curr)
      }
      return next
    })
  }

  const goBack = () => {
    if (historyIdx > 0) {
      const newIdx = historyIdx - 1
      setHistoryIdx(newIdx)
      setCurrentDir(history[newIdx])
      setSelectedItem(null)
    }
  }

  const goForward = () => {
    if (historyIdx < history.length - 1) {
      const newIdx = historyIdx + 1
      setHistoryIdx(newIdx)
      setCurrentDir(history[newIdx])
      setSelectedItem(null)
    }
  }

  const goUp = () => {
    if (!currentDir) return
    const parts = currentDir.split('/')
    const parent = parts.slice(0, -1).join('/')
    navigateTo(parent)
  }

  // Toggle folder expansion in tree
  const toggleExpand = (dir: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setExpandedDirs(prev => {
      const next = new Set(prev)
      if (next.has(dir)) next.delete(dir)
      else next.add(dir)
      return next
    })
  }

  // Parse directories structure
  const { dirHierarchy } = useMemo(() => {
    const dirsSet = new Set<string>([''])
    for (const f of files) {
      const rel = f.path.replace(/^\/memories\//, '')
      const parts = rel.split('/')
      if (parts.length > 1) {
        let acc = ''
        for (let i = 0; i < parts.length - 1; i++) {
          acc = acc ? `${acc}/${parts[i]}` : parts[i]
          dirsSet.add(acc)
        }
      }
    }
    // Also include common declared folders if empty
    const declared = ['dossier', 'finance', 'finance/ledger', 'projects', 'wellness', 'academic', 'social', 'social/personal', 'social/professional', 'cybersec', 'ops']
    for (const d of declared) {
      dirsSet.add(d)
    }

    // Build hierarchy: map parent -> child folder names
    const tree = new Map<string, string[]>()
    for (const d of dirsSet) {
      tree.set(d, [])
    }
    for (const d of dirsSet) {
      if (!d) continue
      const parts = d.split('/')
      const parent = parts.slice(0, -1).join('/')
      const leaf = parts[parts.length - 1]
      if (!tree.has(parent)) tree.set(parent, [])
      if (!tree.get(parent)!.includes(leaf)) {
        tree.get(parent)!.push(leaf)
      }
    }
    for (const [, children] of tree) {
      children.sort()
    }
    return { dirHierarchy: tree }
  }, [files])

  // Current Directory Content
  const { currentFolders, currentFiles } = useMemo(() => {
    // Child folders
    const rawFolders = dirHierarchy.get(currentDir) || []
    let folders = rawFolders.map(name => {
      const fullPath = currentDir ? `${currentDir}/${name}` : name
      const count = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${fullPath}/`)).length
      return { name, fullPath, count }
    })

    // Child files in currentDir
    let cFiles = files.filter(f => {
      const rel = f.path.replace(/^\/memories\//, '')
      const parts = rel.split('/')
      const fileDir = parts.slice(0, -1).join('/')
      return fileDir === currentDir
    })

    // Apply search filter if any
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim()
      folders = folders.filter(fo => fo.name.toLowerCase().includes(q))
      cFiles = cFiles.filter(fi => fi.path.toLowerCase().includes(q) || fi.content.toLowerCase().includes(q))
    }

    // Sort
    folders.sort((a, b) => a.name.localeCompare(b.name))
    cFiles.sort((a, b) => {
      const nameA = a.path.split('/').pop() || ''
      const nameB = b.path.split('/').pop() || ''
      return nameA.localeCompare(nameB)
    })

    return { currentFolders: folders, currentFiles: cFiles }
  }, [files, currentDir, dirHierarchy, searchQuery])

  // File Operations
  const handleOpenFile = (f: MemoryFileInfo) => {
    setEditorFile(f)
  }

  const handleStartRename = (item: { type: 'folder' | 'file'; path: string; name: string }) => {
    const defaultName = item.type === 'file' ? item.name.replace(/\.md$/, '') : item.name
    setRenameDialog({ open: true, item, newName: defaultName, error: undefined })
  }

  const handleConfirmRename = async () => {
    if (!renameDialog.item) return
    const trimmed = renameDialog.newName.trim()
    if (!trimmed) {
      setRenameDialog(prev => ({ ...prev, error: 'İsim boş olamaz.' }))
      return
    }

    try {
      if (renameDialog.item.type === 'file') {
        const oldRel = renameDialog.item.path.replace(/^\/memories\//, '')
        const parts = oldRel.split('/')
        parts[parts.length - 1] = trimmed.endsWith('.md') ? trimmed : `${trimmed}.md`
        const newPath = `/memories/${parts.join('/')}`

        await renameMemoryFile(config, renameDialog.item.path, newPath)
      } else {
        const oldPrefix = renameDialog.item.path.replace(/^\/memories\//, '')
        const parts = oldPrefix.split('/')
        parts[parts.length - 1] = trimmed
        const newPrefix = parts.join('/')

        const matchingFiles = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${oldPrefix}/`))
        for (const f of matchingFiles) {
          const suffix = f.path.replace(/^\/memories\//, '').slice(oldPrefix.length)
          const newPath = `/memories/${newPrefix}${suffix}`
          await renameMemoryFile(config, f.path, newPath)
        }
      }

      setRenameDialog({ open: false, item: null, newName: '' })
      await loadFiles()
      if (onFilesChanged) onFilesChanged()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Yeniden adlandırma başarısız oldu'
      setRenameDialog(prev => ({ ...prev, error: msg }))
    }
  }

  const handleStartDelete = (item: { type: 'folder' | 'file'; path: string; name: string }) => {
    setDeleteDialog({ open: true, item, error: undefined })
  }

  const handleConfirmDelete = async () => {
    if (!deleteDialog.item) return
    setDeleteDialog(prev => ({ ...prev, loading: true, error: undefined }))

    try {
      if (deleteDialog.item.type === 'file') {
        await deleteMemoryFile(config, deleteDialog.item.path)
      } else {
        const prefix = deleteDialog.item.path.replace(/^\/memories\//, '')
        const matchingFiles = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${prefix}/`))
        for (const f of matchingFiles) {
          await deleteMemoryFile(config, f.path)
        }
      }

      setDeleteDialog({ open: false, item: null, loading: false })
      setSelectedItem(null)
      await loadFiles()
      if (onFilesChanged) onFilesChanged()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Silme işlemi başarısız oldu'
      setDeleteDialog(prev => ({ ...prev, loading: false, error: msg }))
    }
  }

  const handleCreateFile = async () => {
    const raw = newFileDialog.filename.trim()
    if (!raw) {
      setNewFileDialog(prev => ({ ...prev, error: 'Dosya adı boş olamaz.' }))
      return
    }
    const cleanName = raw.endsWith('.md') ? raw : `${raw}.md`
    const path = currentDir ? `/memories/${currentDir}/${cleanName}` : `/memories/${cleanName}`

    try {
      const title = cleanName.replace(/\.md$/, '').replace(/-/g, ' ').toUpperCase()
      const newFile = await createMemoryFile(config, path, `# ${title}\n\n`)
      setNewFileDialog({ open: false, filename: '' })
      await loadFiles()
      if (onFilesChanged) onFilesChanged()
      setEditorFile(newFile)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Dosya oluşturulamadı'
      setNewFileDialog(prev => ({ ...prev, error: msg }))
    }
  }

  const handleCreateFolder = async () => {
    const raw = newFolderDialog.foldername.trim().replace(/[^a-zA-Z0-9_-]/g, '')
    if (!raw) {
      setNewFolderDialog(prev => ({ ...prev, error: 'Geçerli bir klasör adı girin.' }))
      return
    }
    const folderPath = currentDir ? `${currentDir}/${raw}` : raw
    const placeholderPath = `/memories/${folderPath}/notes.md`

    try {
      await createMemoryFile(config, placeholderPath, `# ${raw.toUpperCase()}\n\n`)
      setNewFolderDialog({ open: false, foldername: '' })
      await loadFiles()
      navigateTo(folderPath)
      if (onFilesChanged) onFilesChanged()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Klasör oluşturulamadı'
      setNewFolderDialog(prev => ({ ...prev, error: msg }))
    }
  }

  // Recursive Tree Node Renderer for Left Navigation
  const renderTreeNode = (dirPath: string, depth = 0) => {
    const children = dirHierarchy.get(dirPath) || []
    const isExpanded = expandedDirs.has(dirPath)
    const isSelected = currentDir === dirPath
    const label = dirPath === '' ? 'Hafıza Bankası (Kök)' : dirPath.split('/').pop() || dirPath
    const hasChildren = children.length > 0

    return (
      <div key={dirPath} style={{ userSelect: 'none' }}>
        <div
          onClick={() => navigateTo(dirPath)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '5px 8px 5px 6px',
            paddingLeft: `${10 + depth * 16}px`,
            cursor: 'pointer',
            background: isSelected ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : 'transparent',
            borderLeft: isSelected ? '2px solid var(--hb-cyan, #36abca)' : '2px solid transparent',
            color: isSelected ? 'var(--hb-cyan-bright, #5fcce6)' : '#c9d1d9',
            fontSize: '0.78rem', fontWeight: isSelected ? 600 : 400,
            borderRadius: 3, transition: 'background 0.1s',
          }}
          onMouseEnter={e => {
            if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.05)'
          }}
          onMouseLeave={e => {
            if (!isSelected) e.currentTarget.style.background = 'transparent'
          }}
        >
          {hasChildren ? (
            <span
              onClick={e => toggleExpand(dirPath, e)}
              style={{
                width: 14, height: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#8b949e', fontSize: '0.65rem',
                transform: isExpanded ? 'rotate(90deg)' : 'none',
                transition: 'transform 0.15s ease',
              }}
            >
              ▶
            </span>
          ) : (
            <span style={{ width: 14 }} />
          )}

          <svg width="15" height="15" viewBox="0 0 24 24" fill={isSelected ? '#f2b75c' : '#d29922'} stroke="none" style={{ flexShrink: 0 }}>
            <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
          </svg>

          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {label}
          </span>
        </div>

        {hasChildren && isExpanded && (
          <div>
            {children.map(childLeaf => {
              const childPath = dirPath ? `${dirPath}/${childLeaf}` : childLeaf
              return renderTreeNode(childPath, depth + 1)
            })}
          </div>
        )}
      </div>
    )
  }

  // Breadcrumbs items
  const breadcrumbs = useMemo(() => {
    const list: { label: string; path: string }[] = [{ label: 'Hafıza Bankası', path: '' }]
    if (currentDir) {
      let acc = ''
      for (const seg of currentDir.split('/')) {
        acc = acc ? `${acc}/${seg}` : seg
        list.push({ label: seg, path: acc })
      }
    }
    return list
  }, [currentDir])

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 9998,
        background: 'rgba(4, 6, 9, 0.85)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '20px', animation: 'hbFadeIn 0.2s ease',
      }}
    >
      {/* Explorer Window Frame */}
      <div
        style={{
          width: '94vw', maxWidth: 1220, height: '88vh', maxHeight: 860,
          background: '#13161b',
          border: '1px solid rgba(var(--hb-accent-rgb), 0.35)',
          borderRadius: 8,
          boxShadow: '0 28px 72px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.06)',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          fontFamily: 'var(--font-read), -apple-system, sans-serif',
          color: '#e6edf3',
        }}
      >
        {/* Title Bar (Windows Explorer Style) */}
        <div
          style={{
            height: 38, flexShrink: 0,
            background: '#0e1116',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 12px', userSelect: 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="#d29922" stroke="none">
              <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
            </svg>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#f0f6fc' }}>
              Dosya Gezgini — Speda Hafıza Bankası
            </span>
          </div>

          <button
            onClick={onClose}
            title="Kapat (Esc)"
            style={{
              background: 'transparent', border: 'none', color: '#8b949e',
              cursor: 'pointer', padding: '4px 8px', borderRadius: 4,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
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

        {/* Navigation Bar */}
        <div
          style={{
            height: 44, flexShrink: 0,
            background: '#161a20',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '0 12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <button
              onClick={goBack}
              disabled={historyIdx <= 0}
              title="Geri"
              style={{
                width: 28, height: 28, borderRadius: 4,
                background: 'transparent', border: '1px solid transparent',
                color: historyIdx > 0 ? '#c9d1d9' : '#484f58',
                cursor: historyIdx > 0 ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
              onMouseEnter={e => {
                if (historyIdx > 0) e.currentTarget.style.background = 'rgba(255,255,255,0.08)'
              }}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              ←
            </button>
            <button
              onClick={goForward}
              disabled={historyIdx >= history.length - 1}
              title="İleri"
              style={{
                width: 28, height: 28, borderRadius: 4,
                background: 'transparent', border: '1px solid transparent',
                color: historyIdx < history.length - 1 ? '#c9d1d9' : '#484f58',
                cursor: historyIdx < history.length - 1 ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
              onMouseEnter={e => {
                if (historyIdx < history.length - 1) e.currentTarget.style.background = 'rgba(255,255,255,0.08)'
              }}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              →
            </button>
            <button
              onClick={goUp}
              disabled={!currentDir}
              title="Üst Dizine Çık"
              style={{
                width: 28, height: 28, borderRadius: 4,
                background: 'transparent', border: '1px solid transparent',
                color: currentDir ? '#c9d1d9' : '#484f58',
                cursor: currentDir ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
              onMouseEnter={e => {
                if (currentDir) e.currentTarget.style.background = 'rgba(255,255,255,0.08)'
              }}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              ↑
            </button>
          </div>

          <div
            style={{
              flex: 1, height: 30,
              background: '#0d1015',
              border: '1px solid rgba(255, 255, 255, 0.12)',
              borderRadius: 4,
              display: 'flex', alignItems: 'center',
              padding: '0 8px', overflow: 'hidden',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="#d29922" stroke="none" style={{ marginRight: 6, flexShrink: 0 }}>
              <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
            </svg>

            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', overflowX: 'auto' }}>
              {breadcrumbs.map((b, idx) => (
                <React.Fragment key={b.path}>
                  {idx > 0 && <span style={{ color: '#484f58', fontSize: '0.7rem' }}>›</span>}
                  <span
                    onClick={() => navigateTo(b.path)}
                    style={{
                      color: idx === breadcrumbs.length - 1 ? 'var(--hb-cyan-bright, #5fcce6)' : '#c9d1d9',
                      cursor: 'pointer', padding: '2px 4px', borderRadius: 3,
                      fontWeight: idx === breadcrumbs.length - 1 ? 600 : 400,
                      whiteSpace: 'nowrap',
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    {b.label}
                  </span>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div
            style={{
              width: 220, height: 30,
              background: '#0d1015',
              border: '1px solid rgba(255, 255, 255, 0.12)',
              borderRadius: 4,
              display: 'flex', alignItems: 'center',
              padding: '0 8px', gap: 6,
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8b949e" strokeWidth="2.5">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              placeholder="Hafızada ara..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                width: '100%', background: 'transparent', border: 'none', outline: 'none',
                color: '#f0f6fc', fontSize: '0.76rem',
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                style={{ background: 'transparent', border: 'none', color: '#8b949e', cursor: 'pointer', fontSize: '0.75rem', padding: 0 }}
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Action Ribbon */}
        <div
          style={{
            height: 38, flexShrink: 0,
            background: '#111419',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button
              onClick={() => setNewFileDialog({ open: true, filename: '' })}
              style={{
                background: 'rgba(var(--hb-accent-rgb), 0.15)',
                border: '1px solid rgba(var(--hb-accent-rgb), 0.35)',
                color: 'var(--hb-cyan-bright)', borderRadius: 4,
                padding: '4px 10px', fontSize: '0.75rem', fontWeight: 500,
                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              Yeni Dosya
            </button>

            <button
              onClick={() => setNewFolderDialog({ open: true, foldername: '' })}
              style={{
                background: 'transparent',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                color: '#c9d1d9', borderRadius: 4,
                padding: '4px 10px', fontSize: '0.75rem', fontWeight: 500,
                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="#d29922" stroke="none">
                <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
              </svg>
              Yeni Klasör
            </button>

            <span style={{ width: 1, height: 18, background: 'rgba(255,255,255,0.1)', margin: '0 4px' }} />

            <button
              onClick={() => selectedItem && handleStartRename(selectedItem)}
              disabled={!selectedItem}
              title="Yeniden Adlandır (F2)"
              style={{
                background: 'transparent',
                border: '1px solid transparent',
                color: selectedItem ? '#c9d1d9' : '#484f58',
                borderRadius: 4, padding: '4px 9px', fontSize: '0.75rem',
                cursor: selectedItem ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', gap: 5,
              }}
              onMouseEnter={e => {
                if (selectedItem) e.currentTarget.style.background = 'rgba(255,255,255,0.06)'
              }}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
              Yeniden Adlandır
            </button>

            <button
              onClick={() => selectedItem && handleStartDelete(selectedItem)}
              disabled={!selectedItem}
              title="Sil (Del)"
              style={{
                background: 'transparent',
                border: '1px solid transparent',
                color: selectedItem ? '#f85149' : '#484f58',
                borderRadius: 4, padding: '4px 9px', fontSize: '0.75rem',
                cursor: selectedItem ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', gap: 5,
              }}
              onMouseEnter={e => {
                if (selectedItem) e.currentTarget.style.background = 'rgba(248, 81, 73, 0.1)'
              }}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
              Sil
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button
              onClick={loadFiles}
              title="Yenile"
              style={{
                background: 'transparent', border: 'none', color: '#8b949e',
                cursor: 'pointer', padding: '4px 6px', borderRadius: 4,
                display: 'flex', alignItems: 'center',
              }}
              onMouseEnter={e => e.currentTarget.style.color = '#f0f6fc'}
              onMouseLeave={e => e.currentTarget.style.color = '#8b949e'}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
            </button>

            <div style={{ display: 'flex', background: '#0d1015', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 4, padding: 2 }}>
              <button
                onClick={() => setViewMode('details')}
                title="Ayrıntılar Görünümü"
                style={{
                  background: viewMode === 'details' ? 'rgba(var(--hb-accent-rgb), 0.25)' : 'transparent',
                  color: viewMode === 'details' ? 'var(--hb-cyan-bright)' : '#8b949e',
                  border: 'none', padding: '3px 7px', borderRadius: 3, cursor: 'pointer',
                }}
              >
                Liste
              </button>
              <button
                onClick={() => setViewMode('tiles')}
                title="Kutular Görünümü"
                style={{
                  background: viewMode === 'tiles' ? 'rgba(var(--hb-accent-rgb), 0.25)' : 'transparent',
                  color: viewMode === 'tiles' ? 'var(--hb-cyan-bright)' : '#8b949e',
                  border: 'none', padding: '3px 7px', borderRadius: 3, cursor: 'pointer',
                }}
              >
                Kutular
              </button>
            </div>
          </div>
        </div>

        {/* Main Split Body */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
          {/* Left Navigation Pane */}
          <div
            style={{
              width: 240, flexShrink: 0,
              background: '#0e1116',
              borderRight: '1px solid rgba(255, 255, 255, 0.08)',
              overflowY: 'auto', padding: '8px 0',
            }}
          >
            <div style={{ padding: '4px 14px', fontSize: '0.68rem', fontWeight: 700, color: '#6e7681', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Hızlı Erişim
            </div>

            <div style={{ padding: '0 6px', marginBottom: 12 }}>
              <div
                onClick={() => navigateTo('')}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px',
                  borderRadius: 4, cursor: 'pointer', fontSize: '0.78rem',
                  color: currentDir === '' ? 'var(--hb-cyan-bright)' : '#c9d1d9',
                  background: currentDir === '' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : 'transparent',
                }}
              >
                <span>🏠</span>
                <span>Tüm Hafıza (Kök)</span>
              </div>
              <div
                onClick={() => navigateTo('finance')}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px',
                  borderRadius: 4, cursor: 'pointer', fontSize: '0.78rem',
                  color: currentDir.startsWith('finance') ? 'var(--hb-cyan-bright)' : '#c9d1d9',
                  background: currentDir === 'finance' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : 'transparent',
                }}
              >
                <span>💰</span>
                <span>Finans & Muhasebe</span>
              </div>
              <div
                onClick={() => navigateTo('wellness')}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px',
                  borderRadius: 4, cursor: 'pointer', fontSize: '0.78rem',
                  color: currentDir === 'wellness' ? 'var(--hb-cyan-bright)' : '#c9d1d9',
                  background: currentDir === 'wellness' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : 'transparent',
                }}
              >
                <span>🏃</span>
                <span>Sağlık (Atomix)</span>
              </div>
              <div
                onClick={() => navigateTo('projects')}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px',
                  borderRadius: 4, cursor: 'pointer', fontSize: '0.78rem',
                  color: currentDir === 'projects' ? 'var(--hb-cyan-bright)' : '#c9d1d9',
                  background: currentDir === 'projects' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : 'transparent',
                }}
              >
                <span>💼</span>
                <span>Projeler</span>
              </div>
            </div>

            <div style={{ padding: '4px 14px', fontSize: '0.68rem', fontWeight: 700, color: '#6e7681', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Klasör Ağacı
            </div>

            <div style={{ padding: '0 4px' }}>
              {renderTreeNode('', 0)}
            </div>
          </div>

          {/* Right Contents Pane */}
          <div
            style={{
              flex: 1, display: 'flex', flexDirection: 'column',
              background: '#13161b', overflow: 'hidden',
            }}
            onClick={() => setSelectedItem(null)}
          >
            {loading ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8b949e', fontSize: '0.85rem' }}>
                Hafıza dosyaları taranıyor...
              </div>
            ) : currentFolders.length === 0 && currentFiles.length === 0 ? (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#6e7681' }}>
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" style={{ marginBottom: 12, opacity: 0.5 }}>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
                <span style={{ fontSize: '0.85rem', marginBottom: 6 }}>Bu klasör boş</span>
                <span style={{ fontSize: '0.74rem', color: '#484f58' }}>Yukarıdaki "Yeni Dosya" butonuyla yeni bir hafıza kaydı oluşturabilirsiniz.</span>
              </div>
            ) : viewMode === 'details' ? (
              <div style={{ flex: 1, overflowY: 'auto' }}>
                <div
                  style={{
                    height: 30, display: 'grid',
                    gridTemplateColumns: 'minmax(200px, 3fr) 140px 100px 100px 80px',
                    alignItems: 'center', padding: '0 16px',
                    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
                    fontSize: '0.72rem', fontWeight: 600, color: '#8b949e',
                    userSelect: 'none', position: 'sticky', top: 0, background: '#13161b', zIndex: 5,
                  }}
                >
                  <span>Ad</span>
                  <span>Son Değiştirilme</span>
                  <span>Tür</span>
                  <span>Boyut</span>
                  <span style={{ textAlign: 'right' }}>İşlem</span>
                </div>

                {currentFolders.map(fo => {
                  const isSel = selectedItem?.path === (currentDir ? `/memories/${fo.fullPath}` : `/memories/${fo.name}`)
                  return (
                    <div
                      key={fo.name}
                      onClick={e => {
                        e.stopPropagation()
                        setSelectedItem({ type: 'folder', path: `/memories/${fo.fullPath}`, name: fo.name })
                      }}
                      onDoubleClick={e => {
                        e.stopPropagation()
                        navigateTo(fo.fullPath)
                      }}
                      style={{
                        height: 36, display: 'grid',
                        gridTemplateColumns: 'minmax(200px, 3fr) 140px 100px 100px 80px',
                        alignItems: 'center', padding: '0 16px',
                        borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
                        fontSize: '0.78rem', cursor: 'pointer',
                        background: isSel ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.15)' : 'transparent',
                        color: isSel ? 'var(--hb-cyan-bright)' : '#e6edf3',
                        userSelect: 'none',
                      }}
                      onMouseEnter={e => {
                        if (!isSel) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'
                      }}
                      onMouseLeave={e => {
                        if (!isSel) e.currentTarget.style.background = 'transparent'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
                        <svg width="17" height="17" viewBox="0 0 24 24" fill="#d29922" stroke="none" style={{ flexShrink: 0 }}>
                          <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
                        </svg>
                        <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {fo.name}
                        </span>
                      </div>

                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>--</span>
                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>Dosya klasörü</span>
                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>{fo.count} dosya</span>

                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            navigateTo(fo.fullPath)
                          }}
                          title="Klasörü Aç"
                          style={{
                            background: 'transparent', border: 'none', color: '#c9d1d9',
                            cursor: 'pointer', padding: 2, fontSize: '0.72rem',
                          }}
                        >
                          Aç →
                        </button>
                      </div>
                    </div>
                  )
                })}

                {currentFiles.map(fi => {
                  const filename = fi.path.split('/').pop() || fi.path
                  const isSel = selectedItem?.path === fi.path
                  return (
                    <div
                      key={fi.path}
                      onClick={e => {
                        e.stopPropagation()
                        setSelectedItem({ type: 'file', path: fi.path, name: filename })
                      }}
                      onDoubleClick={e => {
                        e.stopPropagation()
                        handleOpenFile(fi)
                      }}
                      style={{
                        height: 36, display: 'grid',
                        gridTemplateColumns: 'minmax(200px, 3fr) 140px 100px 100px 80px',
                        alignItems: 'center', padding: '0 16px',
                        borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
                        fontSize: '0.78rem', cursor: 'pointer',
                        background: isSel ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.15)' : 'transparent',
                        color: isSel ? 'var(--hb-cyan-bright)' : '#e6edf3',
                        userSelect: 'none',
                      }}
                      onMouseEnter={e => {
                        if (!isSel) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'
                      }}
                      onMouseLeave={e => {
                        if (!isSel) e.currentTarget.style.background = 'transparent'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)" strokeWidth="1.8" style={{ flexShrink: 0 }}>
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                          <line x1="16" y1="13" x2="8" y2="13" />
                          <line x1="16" y1="17" x2="8" y2="17" />
                        </svg>
                        <span style={{ fontWeight: isSel ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {filename}
                        </span>
                      </div>

                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>{formatDate(fi.updated_at)}</span>
                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>Markdown</span>
                      <span style={{ color: '#8b949e', fontSize: '0.74rem' }}>{formatBytes(fi.content.length)}</span>

                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            handleOpenFile(fi)
                          }}
                          title="Editörde Aç"
                          style={{
                            background: 'rgba(var(--hb-accent-rgb), 0.18)', border: '1px solid rgba(var(--hb-accent-rgb), 0.3)',
                            color: 'var(--hb-cyan-bright)', borderRadius: 3,
                            padding: '2px 8px', fontSize: '0.7rem', cursor: 'pointer',
                          }}
                        >
                          Düzenle
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexWrap: 'wrap', gap: 12, alignContent: 'flex-start' }}>
                {currentFolders.map(fo => {
                  const isSel = selectedItem?.path === (currentDir ? `/memories/${fo.fullPath}` : `/memories/${fo.name}`)
                  return (
                    <div
                      key={fo.name}
                      onClick={e => {
                        e.stopPropagation()
                        setSelectedItem({ type: 'folder', path: `/memories/${fo.fullPath}`, name: fo.name })
                      }}
                      onDoubleClick={e => {
                        e.stopPropagation()
                        navigateTo(fo.fullPath)
                      }}
                      style={{
                        width: 140, height: 110,
                        background: isSel ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : '#171c23',
                        border: `1px solid ${isSel ? 'var(--hb-cyan)' : 'rgba(255,255,255,0.08)'}`,
                        borderRadius: 6, padding: '12px 8px',
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                        gap: 8, cursor: 'pointer', textAlign: 'center', userSelect: 'none',
                      }}
                    >
                      <svg width="36" height="36" viewBox="0 0 24 24" fill="#d29922" stroke="none">
                        <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
                      </svg>
                      <span style={{ fontSize: '0.78rem', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', width: '100%' }}>
                        {fo.name}
                      </span>
                      <span style={{ fontSize: '0.68rem', color: '#8b949e' }}>{fo.count} dosya</span>
                    </div>
                  )
                })}

                {currentFiles.map(fi => {
                  const filename = fi.path.split('/').pop() || fi.path
                  const isSel = selectedItem?.path === fi.path
                  return (
                    <div
                      key={fi.path}
                      onClick={e => {
                        e.stopPropagation()
                        setSelectedItem({ type: 'file', path: fi.path, name: filename })
                      }}
                      onDoubleClick={e => {
                        e.stopPropagation()
                        handleOpenFile(fi)
                      }}
                      style={{
                        width: 140, height: 110,
                        background: isSel ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16)' : '#171c23',
                        border: `1px solid ${isSel ? 'var(--hb-cyan)' : 'rgba(255,255,255,0.08)'}`,
                        borderRadius: 6, padding: '12px 8px',
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                        gap: 8, cursor: 'pointer', textAlign: 'center', userSelect: 'none',
                      }}
                    >
                      <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan)" strokeWidth="1.6">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <polyline points="14 2 14 8 20 8" />
                        <line x1="16" y1="13" x2="8" y2="13" />
                        <line x1="16" y1="17" x2="8" y2="17" />
                      </svg>
                      <span style={{ fontSize: '0.78rem', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', width: '100%' }}>
                        {filename}
                      </span>
                      <span style={{ fontSize: '0.68rem', color: '#8b949e' }}>{formatBytes(fi.content.length)}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Bottom Status Bar */}
        <div
          style={{
            height: 26, flexShrink: 0,
            background: '#0d1014',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 14px', fontSize: '0.72rem', color: '#8b949e', userSelect: 'none',
          }}
        >
          <div>
            <span>{currentFolders.length + currentFiles.length} öğe</span>
            {selectedItem && (
              <span style={{ marginLeft: 16, color: 'var(--hb-cyan-bright)' }}>
                Seçili: {selectedItem.name} ({selectedItem.type === 'folder' ? 'Klasör' : 'Dosya'})
              </span>
            )}
          </div>

          <div>
            <span>Dizin: {currentDir ? `/memories/${currentDir}` : '/memories (Kök)'}</span>
          </div>
        </div>
      </div>

      {/* Editor Modal Popup */}
      {editorFile && (
        <MemoryEditorModal
          config={config}
          file={editorFile}
          onClose={() => setEditorFile(null)}
          onSave={updated => {
            setFiles(prev => prev.map(x => (x.path === updated.path ? { ...x, ...updated } : x)))
            if (onFilesChanged) onFilesChanged()
          }}
        />
      )}

      {/* Rename Dialog */}
      {renameDialog.open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: 380, background: '#161a22', border: '1px solid rgba(var(--hb-accent-rgb), 0.4)',
              borderRadius: 8, padding: '20px', boxShadow: '0 20px 48px rgba(0,0,0,0.8)',
            }}
          >
            <h4 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#f0f6fc' }}>
              Yeniden Adlandır
            </h4>
            <p style={{ margin: '0 0 12px', fontSize: '0.76rem', color: '#8b949e' }}>
              "{renameDialog.item?.name}" için yeni isim belirleyin:
            </p>
            <input
              type="text"
              value={renameDialog.newName}
              onChange={e => setRenameDialog(prev => ({ ...prev, newName: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleConfirmRename()
                if (e.key === 'Escape') setRenameDialog({ open: false, item: null, newName: '' })
              }}
              autoFocus
              style={{
                width: '100%', padding: '8px 10px', background: '#0d1015',
                border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4,
                color: '#f0f6fc', fontSize: '0.8rem', outline: 'none', marginBottom: 8,
              }}
            />
            {renameDialog.error && (
              <p style={{ color: '#f85149', fontSize: '0.72rem', margin: '0 0 10px' }}>
                {renameDialog.error}
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
              <button
                onClick={() => setRenameDialog({ open: false, item: null, newName: '' })}
                style={{
                  background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#c9d1d9', borderRadius: 4, padding: '6px 14px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleConfirmRename}
                style={{
                  background: 'var(--hb-cyan)', border: 'none',
                  color: '#080c10', fontWeight: 600, borderRadius: 4, padding: '6px 16px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                Kaydet
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      {deleteDialog.open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: 400, background: '#161a22', border: '1px solid rgba(248, 81, 73, 0.4)',
              borderRadius: 8, padding: '20px', boxShadow: '0 20px 48px rgba(0,0,0,0.8)',
            }}
          >
            <h4 style={{ margin: '0 0 10px', fontSize: '0.9rem', color: '#f85149', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>⚠️</span> Silme Onayı
            </h4>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: '#c9d1d9', lineHeight: 1.5 }}>
              <strong>"{deleteDialog.item?.name}"</strong> öğesini silmek istediğinizden emin misiniz?
            </p>
            <p style={{ margin: '0 0 14px', fontSize: '0.72rem', color: '#8b949e', lineHeight: 1.4 }}>
              Bu işlem güvenlidir. Dosya içeriği revizyon geçmişinde saklanır ve gerektiğinde tek tıkla geri yüklenebilir.
            </p>
            {deleteDialog.error && (
              <p style={{ color: '#f85149', fontSize: '0.72rem', margin: '0 0 10px' }}>
                {deleteDialog.error}
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                onClick={() => setDeleteDialog({ open: false, item: null })}
                disabled={deleteDialog.loading}
                style={{
                  background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#c9d1d9', borderRadius: 4, padding: '6px 14px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={deleteDialog.loading}
                style={{
                  background: '#da3633', border: 'none',
                  color: '#fff', fontWeight: 600, borderRadius: 4, padding: '6px 16px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                {deleteDialog.loading ? 'Siliniyor...' : 'Evet, Sil'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New File Dialog */}
      {newFileDialog.open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: 380, background: '#161a22', border: '1px solid rgba(var(--hb-accent-rgb), 0.4)',
              borderRadius: 8, padding: '20px', boxShadow: '0 20px 48px rgba(0,0,0,0.8)',
            }}
          >
            <h4 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#f0f6fc' }}>
              Yeni Hafıza Dosyası Oluştur
            </h4>
            <p style={{ margin: '0 0 10px', fontSize: '0.74rem', color: '#8b949e' }}>
              Konum: {currentDir ? `/memories/${currentDir}/` : '/memories/'}
            </p>
            <input
              type="text"
              placeholder="ör. notlar.md veya hedef-2026"
              value={newFileDialog.filename}
              onChange={e => setNewFileDialog(prev => ({ ...prev, filename: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleCreateFile()
                if (e.key === 'Escape') setNewFileDialog({ open: false, filename: '' })
              }}
              autoFocus
              style={{
                width: '100%', padding: '8px 10px', background: '#0d1015',
                border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4,
                color: '#f0f6fc', fontSize: '0.8rem', outline: 'none', marginBottom: 8,
              }}
            />
            {newFileDialog.error && (
              <p style={{ color: '#f85149', fontSize: '0.72rem', margin: '0 0 10px' }}>
                {newFileDialog.error}
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
              <button
                onClick={() => setNewFileDialog({ open: false, filename: '' })}
                style={{
                  background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#c9d1d9', borderRadius: 4, padding: '6px 14px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleCreateFile}
                style={{
                  background: 'var(--hb-cyan)', border: 'none',
                  color: '#080c10', fontWeight: 600, borderRadius: 4, padding: '6px 16px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Folder Dialog */}
      {newFolderDialog.open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: 380, background: '#161a22', border: '1px solid rgba(var(--hb-accent-rgb), 0.4)',
              borderRadius: 8, padding: '20px', boxShadow: '0 20px 48px rgba(0,0,0,0.8)',
            }}
          >
            <h4 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#f0f6fc' }}>
              Yeni Klasör Oluştur
            </h4>
            <p style={{ margin: '0 0 10px', fontSize: '0.74rem', color: '#8b949e' }}>
              Konum: {currentDir ? `/memories/${currentDir}/` : '/memories/'}
            </p>
            <input
              type="text"
              placeholder="Klasör adı (ör. arsiv, hedefler, belgeler)"
              value={newFolderDialog.foldername}
              onChange={e => setNewFolderDialog(prev => ({ ...prev, foldername: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleCreateFolder()
                if (e.key === 'Escape') setNewFolderDialog({ open: false, foldername: '' })
              }}
              autoFocus
              style={{
                width: '100%', padding: '8px 10px', background: '#0d1015',
                border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4,
                color: '#f0f6fc', fontSize: '0.8rem', outline: 'none', marginBottom: 8,
              }}
            />
            {newFolderDialog.error && (
              <p style={{ color: '#f85149', fontSize: '0.72rem', margin: '0 0 10px' }}>
                {newFolderDialog.error}
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
              <button
                onClick={() => setNewFolderDialog({ open: false, foldername: '' })}
                style={{
                  background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#c9d1d9', borderRadius: 4, padding: '6px 14px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleCreateFolder}
                style={{
                  background: 'var(--hb-cyan)', border: 'none',
                  color: '#080c10', fontWeight: 600, borderRadius: 4, padding: '6px 16px', fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                Klasör Oluştur
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
