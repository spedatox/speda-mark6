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
import GlassFolderIcon from './GlassFolderIcon'

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

/** Resolves domain badge icon and neon glow color based on folder name */
function resolveFolderTheme(name: string): {
  color: string
  badge: 'dossier' | 'finance' | 'wellness' | 'projects' | 'social' | 'academic' | 'cybersec' | 'ops' | 'folder'
} {
  const n = name.toLowerCase()
  if (n.includes('dossier') || n.includes('owner') || n.includes('kimlik') || n.includes('profile')) {
    return { color: '#5fcce6', badge: 'dossier' }
  }
  if (n.includes('finance') || n.includes('ledger') || n.includes('finans') || n.includes('bütçe')) {
    return { color: '#f2b75c', badge: 'finance' }
  }
  if (n.includes('wellness') || n.includes('sağlık') || n.includes('health') || n.includes('spor') || n.includes('athlete')) {
    return { color: '#51cf66', badge: 'wellness' }
  }
  if (n.includes('project') || n.includes('proje') || n.includes('code') || n.includes('dev')) {
    return { color: '#4dabf7', badge: 'projects' }
  }
  if (n.includes('social') || n.includes('sosyal') || n.includes('contact') || n.includes('network')) {
    return { color: '#e599f7', badge: 'social' }
  }
  if (n.includes('academic') || n.includes('kpss') || n.includes('study') || n.includes('akademi')) {
    return { color: '#ffd43b', badge: 'academic' }
  }
  if (n.includes('cyber') || n.includes('sec') || n.includes('güvenlik') || n.includes('audit')) {
    return { color: '#ff6b6b', badge: 'cybersec' }
  }
  if (n.includes('ops') || n.includes('sistem') || n.includes('telemetri') || n.includes('log')) {
    return { color: '#20c997', badge: 'ops' }
  }
  return { color: 'var(--hb-cyan-bright, #5fcce6)', badge: 'folder' }
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
  const [viewMode, setViewMode] = useState<'details' | 'tiles'>('tiles')
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set(['']))

  // Sub-modal states
  const [editorFile, setEditorFile] = useState<MemoryFileInfo | null>(null)
  const [renameDialog, setRenameDialog] = useState<{
    open: boolean
    item: { type: 'folder' | 'file'; path: string; name: string } | null
    newName: string
    error?: string
  }>({ open: false, item: null, newName: '' })
  const [deleteDialog, setDeleteDialog] = useState<{
    open: boolean
    item: { type: 'folder' | 'file'; path: string; name: string } | null
    loading?: boolean
    error?: string
  }>({ open: false, item: null })
  const [newFileDialog, setNewFileDialog] = useState<{ open: boolean; filename: string; error?: string }>({
    open: false,
    filename: '',
  })
  const [newFolderDialog, setNewFolderDialog] = useState<{ open: boolean; foldername: string; error?: string }>({
    open: false,
    foldername: '',
  })

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
    if (initialPath !== undefined && initialPath !== null) {
      const rel = initialPath.replace(/^\/memories\/?/, '').replace(/\/$/, '')
      if (rel) {
        const parts = rel.split('/')
        const isFile = rel.endsWith('.md')
        const dir = isFile ? parts.slice(0, -1).join('/') : rel
        setCurrentDir(dir)
        setHistory(['', dir])
        setHistoryIdx(1)
        // expand all parent dirs
        let curr = ''
        const exp = new Set<string>([''])
        for (const p of parts.slice(0, isFile ? -1 : undefined)) {
          curr = curr ? `${curr}/${p}` : p
          exp.add(curr)
        }
        setExpandedDirs(exp)
      } else {
        setCurrentDir('')
        setHistory([''])
        setHistoryIdx(0)
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
    if (parts.length <= 1) {
      navigateTo('')
    } else {
      navigateTo(parts.slice(0, -1).join('/'))
    }
  }

  // Build hierarchical directory tree from all memory files
  const dirHierarchy = useMemo(() => {
    const map = new Map<string, Set<string>>()
    map.set('', new Set())

    for (const f of files) {
      const rel = f.path.replace(/^\/memories\//, '')
      const parts = rel.split('/')
      let parent = ''
      for (let i = 0; i < parts.length - 1; i++) {
        const seg = parts[i]
        if (!map.has(parent)) {
          map.set(parent, new Set())
        }
        map.get(parent)!.add(seg)
        parent = parent ? `${parent}/${seg}` : seg
      }
      if (!map.has(parent)) {
        map.set(parent, new Set())
      }
    }

    const res = new Map<string, string[]>()
    for (const [k, v] of map.entries()) {
      res.set(k, Array.from(v).sort())
    }
    return res
  }, [files])

  // Get current folders and files
  const { currentFolders, currentFiles } = useMemo(() => {
    const rawFolders = dirHierarchy.get(currentDir) || []
    let folders = rawFolders.map(name => {
      const fullPath = currentDir ? `${currentDir}/${name}` : name
      const count = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${fullPath}/`)).length
      return { name, fullPath, count }
    })

    let filteredFiles = files.filter(f => {
      const rel = f.path.replace(/^\/memories\//, '')
      const parts = rel.split('/')
      if (currentDir === '') {
        return parts.length === 1
      }
      const dirOfFile = parts.slice(0, -1).join('/')
      return dirOfFile === currentDir
    })

    // Search filtering
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      folders = folders.filter(fo => fo.name.toLowerCase().includes(q))
      filteredFiles = filteredFiles.filter(fi => {
        const filename = fi.path.split('/').pop() || fi.path
        return filename.toLowerCase().includes(q) || fi.content.toLowerCase().includes(q)
      })
    }

    folders.sort((a, b) => a.name.localeCompare(b.name))
    filteredFiles.sort((a, b) => {
      const nameA = a.path.split('/').pop() || a.path
      const nameB = b.path.split('/').pop() || b.path
      return nameA.localeCompare(nameB)
    })

    return { currentFolders: folders, currentFiles: filteredFiles }
  }, [files, dirHierarchy, currentDir, searchQuery])

  // Breadcrumbs
  const breadcrumbs = useMemo(() => {
    const crumbs: { label: string; path: string }[] = [{ label: 'Hafıza (Kök)', path: '' }]
    if (!currentDir) return crumbs

    const parts = currentDir.split('/')
    let pathAcc = ''
    for (const p of parts) {
      pathAcc = pathAcc ? `${pathAcc}/${p}` : p
      crumbs.push({ label: p, path: pathAcc })
    }
    return crumbs
  }, [currentDir])

  // Actions
  const handleOpenFile = (file: MemoryFileInfo) => {
    setEditorFile(file)
  }

  const handleStartRename = (item: { type: 'folder' | 'file'; path: string; name: string }) => {
    setRenameDialog({
      open: true,
      item,
      newName: item.name,
      error: undefined,
    })
  }

  const handleConfirmRename = async () => {
    if (!renameDialog.item) return
    const trimmed = renameDialog.newName.trim()
    if (!trimmed) {
      setRenameDialog(d => ({ ...d, error: 'İsim boş bırakılamaz.' }))
      return
    }
    if (trimmed === renameDialog.item.name) {
      setRenameDialog({ open: false, item: null, newName: '' })
      return
    }

    try {
      if (renameDialog.item.type === 'file') {
        const parts = renameDialog.item.path.split('/')
        parts[parts.length - 1] = trimmed.endsWith('.md') ? trimmed : `${trimmed}.md`
        const newPath = parts.join('/')

        await renameMemoryFile(config, renameDialog.item.path, newPath)
      } else {
        const oldPrefix = renameDialog.item.path.replace(/^\/memories\//, '')
        const parts = oldPrefix.split('/')
        parts[parts.length - 1] = trimmed
        const newPrefix = parts.join('/')

        const affected = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${oldPrefix}/`))
        for (const f of affected) {
          const rel = f.path.replace(/^\/memories\//, '')
          const sub = rel.substring(oldPrefix.length)
          const newPath = `/memories/${newPrefix}${sub}`
          await renameMemoryFile(config, f.path, newPath)
        }
      }

      setRenameDialog({ open: false, item: null, newName: '' })
      await loadFiles()
      onFilesChanged?.()
    } catch (err: unknown) {
      setRenameDialog(d => ({ ...d, error: err instanceof Error ? err.message : 'Yeniden adlandırılamadı.' }))
    }
  }

  const handleStartDelete = (item: { type: 'folder' | 'file'; path: string; name: string }) => {
    setDeleteDialog({
      open: true,
      item,
      loading: false,
      error: undefined,
    })
  }

  const handleConfirmDelete = async () => {
    if (!deleteDialog.item) return
    setDeleteDialog(d => ({ ...d, loading: true, error: undefined }))

    try {
      if (deleteDialog.item.type === 'file') {
        await deleteMemoryFile(config, deleteDialog.item.path)
      } else {
        const folderPrefix = deleteDialog.item.path.replace(/^\/memories\//, '')
        const affected = files.filter(f => f.path.replace(/^\/memories\//, '').startsWith(`${folderPrefix}/`))
        for (const f of affected) {
          await deleteMemoryFile(config, f.path)
        }
      }

      setDeleteDialog({ open: false, item: null, loading: false })
      setSelectedItem(null)
      await loadFiles()
      onFilesChanged?.()
    } catch (err: unknown) {
      setDeleteDialog(d => ({ ...d, loading: false, error: err instanceof Error ? err.message : 'Silinemedi.' }))
    }
  }

  const handleCreateFile = async () => {
    const raw = newFileDialog.filename.trim()
    if (!raw) {
      setNewFileDialog(d => ({ ...d, error: 'Dosya adı boş bırakılamaz.' }))
      return
    }
    const cleanName = raw.endsWith('.md') ? raw : `${raw}.md`
    const path = currentDir ? `/memories/${currentDir}/${cleanName}` : `/memories/${cleanName}`

    if (files.some(f => f.path === path)) {
      setNewFileDialog(d => ({ ...d, error: 'Bu isimde bir dosya zaten mevcut.' }))
      return
    }

    try {
      const initialHeader = `# ${cleanName.replace(/\.md$/, '').toUpperCase()}\n\n`
      await createMemoryFile(config, path, initialHeader)
      setNewFileDialog({ open: false, filename: '' })
      await loadFiles()
      onFilesChanged?.()
      const newFileObj = {
        path,
        content: initialHeader,
        updated_at: new Date().toISOString(),
        editable: true,
      }
      setEditorFile(newFileObj)
    } catch (err: unknown) {
      setNewFileDialog(d => ({ ...d, error: err instanceof Error ? err.message : 'Dosya oluşturulamadı.' }))
    }
  }

  const handleCreateFolder = async () => {
    const raw = newFolderDialog.foldername.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
    if (!raw) {
      setNewFolderDialog(d => ({ ...d, error: 'Geçersiz klasör adı.' }))
      return
    }
    const path = currentDir ? `/memories/${currentDir}/${raw}/.gitkeep.md` : `/memories/${raw}/.gitkeep.md`

    try {
      await createMemoryFile(config, path, `# ${raw.toUpperCase()}\n\n// Klasör dizin kaydı.\n`)
      setNewFolderDialog({ open: false, foldername: '' })
      await loadFiles()
      onFilesChanged?.()
    } catch (err: unknown) {
      setNewFolderDialog(d => ({ ...d, error: err instanceof Error ? err.message : 'Klasör oluşturulamadı.' }))
    }
  }

  // Toggle tree node expansion
  const toggleExpand = (dir: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setExpandedDirs(prev => {
      const next = new Set(prev)
      if (next.has(dir)) next.delete(dir)
      else next.add(dir)
      return next
    })
  }

  // Recursive Tree Node Renderer for the Left Sidebar
  const renderTreeNode = (dirPath: string, depth = 0): React.ReactNode => {
    const children = dirHierarchy.get(dirPath) || []
    const isExpanded = expandedDirs.has(dirPath)
    const isSelected = currentDir === dirPath

    const label = dirPath === '' ? 'Tüm Hafıza (Kök)' : dirPath.split('/').pop() || dirPath
    const hasChildren = children.length > 0
    const theme = resolveFolderTheme(label)

    return (
      <div key={dirPath || '__root__'}>
        <div
          onClick={() => navigateTo(dirPath)}
          className={isSelected ? 'glass glass-active' : 'glass-interactive'}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 8px',
            paddingLeft: depth * 14 + 8,
            cursor: 'pointer',
            fontSize: '0.76rem',
            userSelect: 'none',
            borderRadius: 6,
            marginBottom: 2,
            border: isSelected ? `1px solid ${theme.color}` : '1px solid transparent',
            background: isSelected ? `${theme.color}18` : 'transparent',
            color: isSelected ? '#fff' : 'var(--hb-text-dim, #9aa0a6)',
            transition: 'all 0.15s ease',
          }}
        >
          {hasChildren ? (
            <span
              onClick={e => toggleExpand(dirPath, e)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 14,
                height: 14,
                cursor: 'pointer',
                transform: isExpanded ? 'rotate(90deg)' : 'none',
                transition: 'transform 0.15s',
                color: theme.color,
                opacity: 0.8,
              }}
            >
              ▸
            </span>
          ) : (
            <span style={{ width: 14 }} />
          )}

          <GlassFolderIcon size={18} color={theme.color} badgeIcon={theme.badge} glow={false} />

          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {label}
          </span>
        </div>

        {hasChildren && isExpanded && (
          <div>
            {children.map(childLeaf => {
              const nextPath = dirPath ? `${dirPath}/${childLeaf}` : childLeaf
              return renderTreeNode(nextPath, depth + 1)
            })}
          </div>
        )}
      </div>
    )
  }

  // Keyboard shortcut for Esc
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (renameDialog.open) setRenameDialog({ open: false, item: null, newName: '' })
        else if (deleteDialog.open) setDeleteDialog({ open: false, item: null })
        else if (newFileDialog.open) setNewFileDialog({ open: false, filename: '' })
        else if (newFolderDialog.open) setNewFolderDialog({ open: false, foldername: '' })
        else if (editorFile) setEditorFile(null)
        else onClose()
      } else if (e.key === 'F2' && selectedItem) {
        handleStartRename(selectedItem)
      } else if ((e.key === 'Delete' || e.key === 'Del') && selectedItem) {
        handleStartDelete(selectedItem)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [renameDialog, deleteDialog, newFileDialog, newFolderDialog, editorFile, selectedItem, onClose])

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        background: 'rgba(3, 6, 10, 0.78)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px',
        animation: 'hbFadeIn 0.22s ease',
      }}
    >
      {/* Explorer Window Frame — Authentic Stark Fluid Glass */}
      <div
        className="glass"
        style={{
          width: '95vw',
          maxWidth: 1260,
          height: '90vh',
          maxHeight: 890,
          borderRadius: 16,
          background: 'linear-gradient(145deg, rgba(20, 24, 33, 0.75) 0%, rgba(10, 13, 19, 0.90) 100%), var(--glass-fill)',
          backdropFilter: 'blur(28px) saturate(140%)',
          WebkitBackdropFilter: 'blur(28px) saturate(140%)',
          border: '1px solid rgba(255, 255, 255, 0.16)',
          boxShadow: '0 28px 72px rgba(0, 0, 0, 0.8), inset 0 1px 0 0 rgba(255, 255, 255, 0.28), inset 0 -1px 0 0 rgba(255, 255, 255, 0.06), 0 0 40px rgba(95, 204, 230, 0.12)',
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
            background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.06) 0%, rgba(0, 0, 0, 0.2) 100%)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
            userSelect: 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: 'var(--hb-cyan-bright, #5fcce6)',
                boxShadow: '0 0 10px var(--hb-cyan-bright, #5fcce6)',
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
              SPEDA_MK_VI // FLUID_GLASS // DATA_BANKS
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono), monospace',
                fontSize: '0.62rem',
                color: 'var(--hb-cyan-bright, #5fcce6)',
                background: 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.12)',
                border: '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.3)',
                padding: '1px 6px',
                borderRadius: 4,
              }}
            >
              VIRTUAL_FS
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span
              style={{
                fontFamily: 'var(--font-mono), monospace',
                fontSize: '0.62rem',
                color: 'var(--hb-text-faint, #5f6368)',
                letterSpacing: '0.08em',
              }}
            >
              REST_API · R/W · ATOMIX_ORION_ROUTED
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
                e.currentTarget.style.borderColor = '#c84a3a'
                e.currentTarget.style.color = '#fff'
                e.currentTarget.style.background = 'rgba(200, 74, 58, 0.25)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.12)'
                e.currentTarget.style.color = '#9aa0a6'
                e.currentTarget.style.background = 'transparent'
              }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Navigation Bar (Back, Forward, Up, Capsule Breadcrumbs, Search) */}
        <div
          style={{
            height: 48,
            flexShrink: 0,
            background: 'rgba(12, 15, 22, 0.65)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '0 14px',
          }}
        >
          {/* Back, Forward, Up Pill Buttons */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              onClick={goBack}
              disabled={historyIdx <= 0}
              title="Geri"
              className="glass glass-interactive glass-round"
              style={{
                width: 30,
                height: 30,
                color: historyIdx > 0 ? '#fff' : '#4a4e52',
                cursor: historyIdx > 0 ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.85rem',
                border: '1px solid rgba(255, 255, 255, 0.12)',
              }}
            >
              ←
            </button>
            <button
              onClick={goForward}
              disabled={historyIdx >= history.length - 1}
              title="İleri"
              className="glass glass-interactive glass-round"
              style={{
                width: 30,
                height: 30,
                color: historyIdx < history.length - 1 ? '#fff' : '#4a4e52',
                cursor: historyIdx < history.length - 1 ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.85rem',
                border: '1px solid rgba(255, 255, 255, 0.12)',
              }}
            >
              →
            </button>
            <button
              onClick={goUp}
              disabled={!currentDir}
              title="Üst Dizine Çık"
              className="glass glass-interactive glass-round"
              style={{
                width: 30,
                height: 30,
                color: currentDir ? '#fff' : '#4a4e52',
                cursor: currentDir ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.85rem',
                border: '1px solid rgba(255, 255, 255, 0.12)',
              }}
            >
              ↑
            </button>
          </div>

          {/* Breadcrumb Fluid Glass Capsule Bar */}
          <div
            className="glass"
            style={{
              flex: 1,
              height: 32,
              borderRadius: 20,
              background: 'rgba(8, 11, 16, 0.55)',
              border: '1px solid rgba(255, 255, 255, 0.12)',
              display: 'flex',
              alignItems: 'center',
              padding: '0 10px',
              overflow: 'hidden',
            }}
          >
            <GlassFolderIcon size={20} color="#5fcce6" badgeIcon="folder" glow={false} />

            <div
              style={{
                marginLeft: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                fontSize: '0.78rem',
                overflowX: 'auto',
              }}
            >
              {breadcrumbs.map((b, idx) => {
                const isLast = idx === breadcrumbs.length - 1
                return (
                  <React.Fragment key={b.path}>
                    {idx > 0 && <span style={{ color: 'rgba(255,255,255,0.25)', fontSize: '0.7rem' }}>/</span>}
                    <span
                      onClick={() => navigateTo(b.path)}
                      style={{
                        color: isLast ? 'var(--hb-cyan-bright, #5fcce6)' : 'var(--hb-text-dim, #9aa0a6)',
                        cursor: 'pointer',
                        padding: '2px 8px',
                        borderRadius: 12,
                        fontWeight: isLast ? 600 : 400,
                        whiteSpace: 'nowrap',
                        background: isLast ? 'rgba(95, 204, 230, 0.12)' : 'transparent',
                        border: isLast ? '1px solid rgba(95, 204, 230, 0.25)' : '1px solid transparent',
                        transition: 'all 0.15s ease',
                      }}
                      onMouseEnter={e => {
                        if (!isLast) e.currentTarget.style.background = 'rgba(255, 255, 255, 0.08)'
                      }}
                      onMouseLeave={e => {
                        if (!isLast) e.currentTarget.style.background = 'transparent'
                      }}
                    >
                      {b.label}
                    </span>
                  </React.Fragment>
                )
              })}
            </div>
          </div>

          {/* Search Glass Pill */}
          <div
            className="glass"
            style={{
              width: 230,
              height: 32,
              borderRadius: 20,
              background: 'rgba(8, 11, 16, 0.55)',
              border: '1px solid rgba(255, 255, 255, 0.12)',
              display: 'flex',
              alignItems: 'center',
              padding: '0 10px',
              gap: 8,
              transition: 'border-color 0.2s',
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan-bright, #5fcce6)" strokeWidth="2.2">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              placeholder="Dizinlerde filtrele..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                width: '100%',
                background: 'transparent',
                border: 'none',
                outline: 'none',
                color: '#fff',
                fontSize: '0.76rem',
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: '#8b949e',
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  padding: 0,
                }}
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Action Ribbon / Toolbar */}
        <div
          style={{
            height: 42,
            flexShrink: 0,
            background: 'rgba(15, 18, 26, 0.5)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
          }}
        >
          {/* Left Actions */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={() => setNewFileDialog({ open: true, filename: '' })}
              className="glass glass-interactive"
              style={{
                color: 'var(--hb-cyan-bright, #5fcce6)',
                borderRadius: 8,
                padding: '4px 12px',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                border: '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.35)',
                background: 'linear-gradient(180deg, rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.16) 0%, rgba(var(--hb-accent-rgb, 54, 171, 202), 0.05) 100%), var(--glass-fill)',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              <span>+ YENİ DOSYA</span>
            </button>

            <button
              onClick={() => setNewFolderDialog({ open: true, foldername: '' })}
              className="glass glass-interactive"
              style={{
                color: 'var(--hb-amber-bright, #f2b75c)',
                borderRadius: 8,
                padding: '4px 12px',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                border: '1px solid rgba(242, 183, 92, 0.35)',
                background: 'linear-gradient(180deg, rgba(242, 183, 92, 0.14) 0%, rgba(217, 156, 68, 0.04) 100%), var(--glass-fill)',
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
              </svg>
              <span>+ YENİ KLASÖR</span>
            </button>

            <span style={{ width: 1, height: 20, background: 'rgba(255,255,255,0.12)', margin: '0 4px' }} />

            {/* Rename button */}
            <button
              onClick={() => selectedItem && handleStartRename(selectedItem)}
              disabled={!selectedItem}
              title="Yeniden Adlandır (F2)"
              className="glass glass-interactive"
              style={{
                color: selectedItem ? '#fff' : '#4a4e52',
                borderRadius: 8,
                padding: '4px 10px',
                fontSize: '0.75rem',
                cursor: selectedItem ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                border: '1px solid rgba(255, 255, 255, 0.1)',
                opacity: selectedItem ? 1 : 0.45,
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
              <span>Yeniden Adlandır</span>
            </button>

            {/* Delete button */}
            <button
              onClick={() => selectedItem && handleStartDelete(selectedItem)}
              disabled={!selectedItem}
              title="Sil (Del)"
              className="glass glass-interactive"
              style={{
                color: selectedItem ? '#ff6b6b' : '#4a4e52',
                borderRadius: 8,
                padding: '4px 10px',
                fontSize: '0.75rem',
                cursor: selectedItem ? 'pointer' : 'default',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                border: '1px solid rgba(255, 107, 107, 0.25)',
                opacity: selectedItem ? 1 : 0.45,
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
              <span>Sil</span>
            </button>
          </div>

          {/* Right Actions: View mode toggle & Refresh */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              onClick={loadFiles}
              title="Yenile"
              className="glass glass-interactive glass-round"
              style={{
                width: 30,
                height: 30,
                color: 'var(--hb-cyan-bright, #5fcce6)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: '1px solid rgba(255, 255, 255, 0.12)',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
            </button>

            {/* View Mode Pill Switch */}
            <div
              className="glass"
              style={{
                display: 'flex',
                borderRadius: 20,
                padding: 2,
                border: '1px solid rgba(255, 255, 255, 0.12)',
                background: 'rgba(8, 11, 16, 0.65)',
              }}
            >
              <button
                onClick={() => setViewMode('tiles')}
                title="Büyük Karolar Görünümü"
                style={{
                  background: viewMode === 'tiles' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.22)' : 'transparent',
                  color: viewMode === 'tiles' ? 'var(--hb-cyan-bright, #5fcce6)' : 'var(--hb-text-dim, #9aa0a6)',
                  border: 'none',
                  padding: '3px 10px',
                  borderRadius: 16,
                  cursor: 'pointer',
                  fontSize: '0.72rem',
                  fontWeight: 600,
                  transition: 'all 0.15s ease',
                }}
              >
                KAROLAR
              </button>
              <button
                onClick={() => setViewMode('details')}
                title="Liste Ayrıntıları Görünümü"
                style={{
                  background: viewMode === 'details' ? 'rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.22)' : 'transparent',
                  color: viewMode === 'details' ? 'var(--hb-cyan-bright, #5fcce6)' : 'var(--hb-text-dim, #9aa0a6)',
                  border: 'none',
                  padding: '3px 10px',
                  borderRadius: 16,
                  cursor: 'pointer',
                  fontSize: '0.72rem',
                  fontWeight: 600,
                  transition: 'all 0.15s ease',
                }}
              >
                LİSTE
              </button>
            </div>
          </div>
        </div>

        {/* Main Split Body: Left Tree | Right File Area */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
          {/* Left Navigation Pane (Quick Access & Folder Tree) */}
          <div
            style={{
              width: 250,
              flexShrink: 0,
              background: 'rgba(10, 13, 18, 0.55)',
              borderRight: '1px solid rgba(255, 255, 255, 0.08)',
              overflowY: 'auto',
              padding: '12px 8px',
            }}
          >
            {/* Quick Access Header */}
            <div
              style={{
                padding: '4px 10px',
                fontFamily: "'Rajdhani', sans-serif",
                fontSize: '0.72rem',
                fontWeight: 700,
                color: 'var(--hb-cyan-bright, #5fcce6)',
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <span>// HIZLI ERİŞİM</span>
              <span style={{ fontSize: '0.62rem', color: 'var(--hb-text-faint)' }}>MK_VI</span>
            </div>

            <div style={{ marginBottom: 14 }}>
              {[
                { label: 'Tüm Hafıza (Kök)', path: '', icon: 'folder', color: '#5fcce6' },
                { label: 'Finans & Ledger', path: 'finance', icon: 'finance', color: '#f2b75c' },
                { label: 'Sağlık & Athlete', path: 'wellness', icon: 'wellness', color: '#51cf66' },
                { label: 'Projeler & Kod', path: 'projects', icon: 'projects', color: '#4dabf7' },
                { label: 'Sosyal Ağ', path: 'social', icon: 'social', color: '#e599f7' },
                { label: 'Akademik & KPSS', path: 'academic', icon: 'academic', color: '#ffd43b' },
                { label: 'Siber Güvenlik', path: 'cybersec', icon: 'cybersec', color: '#ff6b6b' },
                { label: 'Sistem & Ops', path: 'ops', icon: 'ops', color: '#20c997' },
              ].map(item => {
                const isActive = currentDir === item.path
                return (
                  <div
                    key={item.path}
                    onClick={() => navigateTo(item.path)}
                    className={isActive ? 'glass glass-active' : 'glass-interactive'}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '6px 10px',
                      borderRadius: 8,
                      cursor: 'pointer',
                      fontSize: '0.78rem',
                      marginBottom: 2,
                      color: isActive ? '#fff' : 'var(--hb-text-dim, #9aa0a6)',
                      background: isActive ? `${item.color}22` : 'transparent',
                      border: isActive ? `1px solid ${item.color}66` : '1px solid transparent',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <GlassFolderIcon
                      size={22}
                      color={item.color}
                      badgeIcon={item.icon as 'folder' | 'finance' | 'wellness' | 'projects' | 'social' | 'academic' | 'cybersec' | 'ops'}
                      glow={isActive}
                    />
                    <span style={{ fontWeight: isActive ? 600 : 400 }}>{item.label}</span>
                  </div>
                )
              })}
            </div>

            {/* Hierarchical Folder Tree Header */}
            <div
              style={{
                padding: '4px 10px',
                fontFamily: "'Rajdhani', sans-serif",
                fontSize: '0.72rem',
                fontWeight: 700,
                color: 'var(--hb-amber-bright, #f2b75c)',
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                borderTop: '1px solid rgba(255, 255, 255, 0.08)',
                paddingTop: 10,
                marginBottom: 4,
              }}
            >
              // DİZİN HİYERARŞİSİ
            </div>

            <div>{renderTreeNode('', 0)}</div>
          </div>

          {/* Right Contents Pane (Files and Subfolders) */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              background: 'rgba(10, 13, 19, 0.4)',
              overflow: 'hidden',
            }}
            onClick={() => setSelectedItem(null)}
          >
            {loading ? (
              <div
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 12,
                  color: 'var(--hb-cyan-bright, #5fcce6)',
                }}
              >
                <span
                  style={{
                    width: 28,
                    height: 28,
                    border: '2px solid rgba(95, 204, 230, 0.2)',
                    borderTopColor: 'var(--hb-cyan-bright, #5fcce6)',
                    borderRadius: '50%',
                    animation: 'spin 0.8s linear infinite',
                  }}
                />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem', letterSpacing: '0.08em' }}>
                  HAFIZA VERİ TABANI TARANIYOR...
                </span>
              </div>
            ) : currentFolders.length === 0 && currentFiles.length === 0 ? (
              <div
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#6e7681',
                }}
              >
                <GlassFolderIcon size={64} color="var(--hb-cyan-bright, #5fcce6)" badgeIcon="folder" glow={false} />
                <span
                  style={{
                    fontFamily: "'Rajdhani', sans-serif",
                    fontSize: '1.05rem',
                    fontWeight: 700,
                    color: '#fff',
                    marginTop: 14,
                    letterSpacing: '0.06em',
                  }}
                >
                  BU DİZİN BOŞ
                </span>
                <span style={{ fontSize: '0.76rem', color: '#8b949e', marginTop: 4 }}>
                  Yukarıdaki "+ Yeni Dosya" veya "+ Yeni Klasör" butonuyla yeni bir kayıt oluşturabilirsiniz.
                </span>
              </div>
            ) : viewMode === 'details' ? (
              /* Details List View — Stark Fluid Glass Table */
              <div style={{ flex: 1, overflowY: 'auto' }}>
                {/* Header Row */}
                <div
                  style={{
                    height: 34,
                    display: 'grid',
                    gridTemplateColumns: 'minmax(220px, 3fr) 150px 100px 100px 90px',
                    alignItems: 'center',
                    padding: '0 16px',
                    borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
                    fontFamily: "'Rajdhani', sans-serif",
                    fontSize: '0.76rem',
                    fontWeight: 700,
                    letterSpacing: '0.08em',
                    color: 'var(--hb-cyan-bright, #5fcce6)',
                    userSelect: 'none',
                    position: 'sticky',
                    top: 0,
                    background: 'rgba(14, 18, 26, 0.95)',
                    backdropFilter: 'blur(10px)',
                    zIndex: 5,
                  }}
                >
                  <span>BAŞLIK / İSİM</span>
                  <span>SON DEĞİŞİKLİK</span>
                  <span>BİÇİM</span>
                  <span>BOYUT</span>
                  <span style={{ textAlign: 'right' }}>İŞLEM</span>
                </div>

                {/* Subfolders rows */}
                {currentFolders.map(fo => {
                  const theme = resolveFolderTheme(fo.name)
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
                      className={isSel ? 'glass glass-active' : 'glass-interactive'}
                      style={{
                        height: 40,
                        display: 'grid',
                        gridTemplateColumns: 'minmax(220px, 3fr) 150px 100px 100px 90px',
                        alignItems: 'center',
                        padding: '0 16px',
                        borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
                        fontSize: '0.8rem',
                        cursor: 'pointer',
                        background: isSel ? `${theme.color}20` : 'transparent',
                        color: isSel ? '#fff' : '#e6edf3',
                        borderLeft: isSel ? `3px solid ${theme.color}` : '3px solid transparent',
                        userSelect: 'none',
                        transition: 'all 0.15s ease',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
                        <GlassFolderIcon size={24} color={theme.color} badgeIcon={theme.badge} glow={isSel} />
                        <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {fo.name}
                        </span>
                      </div>

                      <span style={{ color: 'var(--hb-text-faint, #5f6368)', fontSize: '0.74rem' }}>--</span>
                      <span style={{ color: theme.color, fontSize: '0.72rem', fontWeight: 600 }}>DİZİN</span>
                      <span style={{ color: 'var(--hb-text-dim, #9aa0a6)', fontSize: '0.74rem' }}>{fo.count} dosya</span>

                      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            navigateTo(fo.fullPath)
                          }}
                          className="glass glass-interactive"
                          style={{
                            padding: '3px 8px',
                            borderRadius: 4,
                            fontSize: '0.68rem',
                            color: theme.color,
                            border: `1px solid ${theme.color}44`,
                            background: 'transparent',
                            cursor: 'pointer',
                          }}
                        >
                          AÇ ↗
                        </button>
                      </div>
                    </div>
                  )
                })}

                {/* Files rows */}
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
                      className={isSel ? 'glass glass-active' : 'glass-interactive'}
                      style={{
                        height: 40,
                        display: 'grid',
                        gridTemplateColumns: 'minmax(220px, 3fr) 150px 100px 100px 90px',
                        alignItems: 'center',
                        padding: '0 16px',
                        borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
                        fontSize: '0.8rem',
                        cursor: 'pointer',
                        background: isSel ? 'rgba(95, 204, 230, 0.16)' : 'transparent',
                        color: isSel ? '#fff' : '#e6edf3',
                        borderLeft: isSel ? '3px solid var(--hb-cyan-bright, #5fcce6)' : '3px solid transparent',
                        userSelect: 'none',
                        transition: 'all 0.15s ease',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--hb-cyan-bright, #5fcce6)" strokeWidth="1.8">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                          <line x1="16" y1="13" x2="8" y2="13" />
                          <line x1="16" y1="17" x2="8" y2="17" />
                        </svg>
                        <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {filename}
                        </span>
                      </div>

                      <span style={{ color: 'var(--hb-text-dim, #9aa0a6)', fontSize: '0.74rem' }}>{formatDate(fi.updated_at)}</span>
                      <span style={{ color: 'var(--hb-cyan-bright, #5fcce6)', fontSize: '0.72rem' }}>Markdown</span>
                      <span style={{ color: 'var(--hb-text-dim, #9aa0a6)', fontSize: '0.74rem' }}>{formatBytes(fi.content.length)}</span>

                      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            handleOpenFile(fi)
                          }}
                          className="glass glass-interactive"
                          style={{
                            padding: '3px 8px',
                            borderRadius: 4,
                            fontSize: '0.68rem',
                            color: 'var(--hb-cyan-bright, #5fcce6)',
                            border: '1px solid rgba(var(--hb-cyan-bright-rgb, 95, 204, 230), 0.35)',
                            background: 'transparent',
                            cursor: 'pointer',
                          }}
                        >
                          DÜZENLE
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              /* Tiles / Grid View — Big Fluid Glass Cards */
              <div
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  padding: '18px',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
                  gap: 14,
                  alignContent: 'flex-start',
                }}
              >
                {/* Folder Cards */}
                {currentFolders.map(fo => {
                  const theme = resolveFolderTheme(fo.name)
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
                      className="glass glass-interactive"
                      style={{
                        height: 140,
                        background: isSel
                          ? `linear-gradient(135deg, ${theme.color}25 0%, rgba(12, 16, 24, 0.8) 100%), var(--glass-fill)`
                          : 'linear-gradient(135deg, rgba(255, 255, 255, 0.04) 0%, rgba(12, 16, 24, 0.65) 100%), var(--glass-fill)',
                        border: `1px solid ${isSel ? theme.color : 'rgba(255,255,255,0.12)'}`,
                        borderRadius: 12,
                        padding: '12px 8px',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 8,
                        cursor: 'pointer',
                        textAlign: 'center',
                        userSelect: 'none',
                        boxShadow: isSel ? `0 8px 24px ${theme.color}33` : 'var(--glass-shadow)',
                        transition: 'all 0.22s cubic-bezier(0.2, 0.9, 0.3, 1)',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = theme.color
                        e.currentTarget.style.transform = 'translateY(-3px)'
                        e.currentTarget.style.boxShadow = `0 10px 28px ${theme.color}28, inset 0 1px 0 rgba(255,255,255,0.22)`
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = isSel ? theme.color : 'rgba(255,255,255,0.12)'
                        e.currentTarget.style.transform = 'translateY(0)'
                        e.currentTarget.style.boxShadow = isSel ? `0 8px 24px ${theme.color}33` : 'var(--glass-shadow)'
                      }}
                    >
                      <GlassFolderIcon size={56} color={theme.color} badgeIcon={theme.badge} glow={isSel} />
                      <span
                        style={{
                          fontSize: '0.82rem',
                          fontWeight: 600,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          width: '100%',
                          color: '#fff',
                        }}
                      >
                        {fo.name}
                      </span>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono), monospace',
                          fontSize: '0.64rem',
                          color: theme.color,
                          background: `${theme.color}18`,
                          border: `1px solid ${theme.color}33`,
                          padding: '1px 6px',
                          borderRadius: 4,
                        }}
                      >
                        {fo.count} dosya
                      </span>
                    </div>
                  )
                })}

                {/* File Cards */}
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
                      className="glass glass-interactive"
                      style={{
                        height: 140,
                        background: isSel
                          ? 'linear-gradient(135deg, rgba(95, 204, 230, 0.2) 0%, rgba(12, 16, 24, 0.8) 100%), var(--glass-fill)'
                          : 'linear-gradient(135deg, rgba(255, 255, 255, 0.04) 0%, rgba(12, 16, 24, 0.65) 100%), var(--glass-fill)',
                        border: `1px solid ${isSel ? 'var(--hb-cyan-bright, #5fcce6)' : 'rgba(255,255,255,0.12)'}`,
                        borderRadius: 12,
                        padding: '12px 8px',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 8,
                        cursor: 'pointer',
                        textAlign: 'center',
                        userSelect: 'none',
                        boxShadow: isSel ? '0 8px 24px rgba(95, 204, 230, 0.28)' : 'var(--glass-shadow)',
                        transition: 'all 0.22s cubic-bezier(0.2, 0.9, 0.3, 1)',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = 'var(--hb-cyan-bright, #5fcce6)'
                        e.currentTarget.style.transform = 'translateY(-3px)'
                        e.currentTarget.style.boxShadow = '0 10px 28px rgba(95, 204, 230, 0.22), inset 0 1px 0 rgba(255,255,255,0.22)'
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = isSel ? 'var(--hb-cyan-bright, #5fcce6)' : 'rgba(255,255,255,0.12)'
                        e.currentTarget.style.transform = 'translateY(0)'
                        e.currentTarget.style.boxShadow = isSel ? '0 8px 24px rgba(95, 204, 230, 0.28)' : 'var(--glass-shadow)'
                      }}
                    >
                      {/* Fluid Glass Document Graphic */}
                      <svg width="44" height="44" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"
                          fill="rgba(95, 204, 230, 0.12)"
                          stroke="var(--hb-cyan-bright, #5fcce6)"
                          strokeWidth="1.5"
                        />
                        <polyline points="14 2 14 8 20 8" stroke="var(--hb-cyan-bright, #5fcce6)" strokeWidth="1.5" />
                        <line x1="8" y1="13" x2="16" y2="13" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" strokeLinecap="round" />
                        <line x1="8" y1="17" x2="13" y2="17" stroke="rgba(255,255,255,0.3)" strokeWidth="1.5" strokeLinecap="round" />
                      </svg>
                      <span
                        style={{
                          fontSize: '0.8rem',
                          fontWeight: 500,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          width: '100%',
                          color: '#fff',
                        }}
                      >
                        {filename}
                      </span>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono), monospace',
                          fontSize: '0.64rem',
                          color: 'var(--hb-text-dim, #9aa0a6)',
                        }}
                      >
                        {formatBytes(fi.content.length)}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Bottom Status Bar — Stark Fluid Glass Telemetry */}
        <div
          style={{
            height: 32,
            flexShrink: 0,
            background: 'rgba(8, 11, 16, 0.85)',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 16px',
            fontSize: '0.74rem',
            color: 'var(--hb-text-dim, #9aa0a6)',
            userSelect: 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <span>
              {currentFolders.length} DİZİN · {currentFiles.length} DOSYA
            </span>
            {selectedItem && (
              <span style={{ color: 'var(--hb-cyan-bright, #5fcce6)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--hb-cyan-bright, #5fcce6)' }} />
                SEÇİLİ: {selectedItem.name} ({selectedItem.type === 'folder' ? 'Dizin' : 'Dosya'})
              </span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span
              style={{
                fontFamily: 'var(--font-mono), monospace',
                fontSize: '0.64rem',
                color: 'var(--hb-text-faint, #5f6368)',
              }}
            >
              UTF-8 // MARKDOWN // SYSTEM_VAULT
            </span>
          </div>
        </div>
      </div>

      {/* Sub-Modal: Memory Editor */}
      {editorFile && (
        <MemoryEditorModal
          config={config}
          file={editorFile}
          onClose={() => setEditorFile(null)}
          onSave={updated => {
            setFiles(prev => prev.map(f => (f.path === updated.path ? updated : f)))
            onFilesChanged?.()
          }}
        />
      )}

      {/* Sub-Modal: Rename Dialog — Stark Fluid Glass */}
      {renameDialog.open && renameDialog.item && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10000,
            background: 'rgba(2, 4, 8, 0.75)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            className="glass"
            style={{
              width: 380,
              borderRadius: 14,
              padding: '20px 22px',
              border: '1px solid rgba(255, 255, 255, 0.16)',
              background: 'linear-gradient(145deg, rgba(22, 26, 36, 0.88) 0%, rgba(12, 15, 22, 0.95) 100%), var(--glass-fill)',
              boxShadow: '0 24px 60px rgba(0,0,0,0.8), 0 0 30px rgba(95, 204, 230, 0.15)',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <GlassFolderIcon size={28} color="#5fcce6" badgeIcon="folder" glow={false} />
              <span
                style={{
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.98rem',
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  color: '#fff',
                }}
              >
                YENİDEN ADLANDIR // RENAME
              </span>
            </div>

            <p style={{ fontSize: '0.78rem', color: 'var(--hb-text-dim, #9aa0a6)' }}>
              {renameDialog.item.name} için yeni bir isim girin:
            </p>

            <input
              type="text"
              autoFocus
              value={renameDialog.newName}
              onChange={e => setRenameDialog(d => ({ ...d, newName: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleConfirmRename()
              }}
              className="glass"
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 8,
                background: 'rgba(8, 11, 16, 0.65)',
                border: '1px solid rgba(95, 204, 230, 0.4)',
                color: '#fff',
                fontSize: '0.85rem',
                outline: 'none',
              }}
            />

            {renameDialog.error && (
              <span style={{ fontSize: '0.74rem', color: '#ff6b6b' }}>{renameDialog.error}</span>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setRenameDialog({ open: false, item: null, newName: '' })}
                className="glass glass-interactive"
                style={{
                  padding: '6px 14px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  color: 'var(--hb-text-dim, #9aa0a6)',
                  cursor: 'pointer',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleConfirmRename}
                className="glass glass-interactive"
                style={{
                  padding: '6px 16px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: '#fff',
                  cursor: 'pointer',
                  background: 'linear-gradient(180deg, rgba(95, 204, 230, 0.3) 0%, rgba(54, 171, 202, 0.15) 100%), var(--glass-fill)',
                  border: '1px solid var(--hb-cyan-bright, #5fcce6)',
                }}
              >
                Kaydet
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sub-Modal: Delete Dialog — Stark Fluid Glass */}
      {deleteDialog.open && deleteDialog.item && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10000,
            background: 'rgba(2, 4, 8, 0.75)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            className="glass"
            style={{
              width: 380,
              borderRadius: 14,
              padding: '20px 22px',
              border: '1px solid rgba(255, 107, 107, 0.35)',
              background: 'linear-gradient(145deg, rgba(28, 20, 22, 0.9) 0%, rgba(14, 10, 12, 0.95) 100%), var(--glass-fill)',
              boxShadow: '0 24px 60px rgba(0,0,0,0.8), 0 0 30px rgba(255, 107, 107, 0.2)',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: '1.2rem', color: '#ff6b6b' }}>⚠</span>
              <span
                style={{
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.98rem',
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  color: '#ff6b6b',
                }}
              >
                SİLME ONAYI // DELETE CONFIRM
              </span>
            </div>

            <p style={{ fontSize: '0.78rem', color: 'var(--hb-text-dim, #9aa0a6)', lineHeight: 1.5 }}>
              <strong style={{ color: '#fff' }}>"{deleteDialog.item.name}"</strong> öğesini silmek istediğinizden emin misiniz?
              {deleteDialog.item.type === 'folder' && (
                <span style={{ display: 'block', marginTop: 4, color: '#f2b75c' }}>
                  Klasörün içerisindeki tüm alt dosyalar da arşivden silinecektir.
                </span>
              )}
            </p>

            {deleteDialog.error && (
              <span style={{ fontSize: '0.74rem', color: '#ff6b6b' }}>{deleteDialog.error}</span>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setDeleteDialog({ open: false, item: null })}
                disabled={deleteDialog.loading}
                className="glass glass-interactive"
                style={{
                  padding: '6px 14px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  color: 'var(--hb-text-dim, #9aa0a6)',
                  cursor: 'pointer',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={deleteDialog.loading}
                className="glass glass-interactive"
                style={{
                  padding: '6px 16px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: '#fff',
                  cursor: 'pointer',
                  background: 'linear-gradient(180deg, rgba(200, 74, 58, 0.35) 0%, rgba(160, 50, 40, 0.2) 100%), var(--glass-fill)',
                  border: '1px solid #ff6b6b',
                }}
              >
                {deleteDialog.loading ? 'Siliniyor...' : 'Evet, Sil'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sub-Modal: New File Dialog — Stark Fluid Glass */}
      {newFileDialog.open && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10000,
            background: 'rgba(2, 4, 8, 0.75)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            className="glass"
            style={{
              width: 380,
              borderRadius: 14,
              padding: '20px 22px',
              border: '1px solid rgba(255, 255, 255, 0.16)',
              background: 'linear-gradient(145deg, rgba(22, 26, 36, 0.88) 0%, rgba(12, 15, 22, 0.95) 100%), var(--glass-fill)',
              boxShadow: '0 24px 60px rgba(0,0,0,0.8), 0 0 30px rgba(95, 204, 230, 0.15)',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: '1.2rem', color: 'var(--hb-cyan-bright, #5fcce6)' }}>+</span>
              <span
                style={{
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.98rem',
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  color: '#fff',
                }}
              >
                YENİ HAFIZA DOSYASI // NEW FILE
              </span>
            </div>

            <p style={{ fontSize: '0.78rem', color: 'var(--hb-text-dim, #9aa0a6)' }}>
              Hedef: <code>{currentDir ? `/memories/${currentDir}/` : '/memories/'}</code>
            </p>

            <input
              type="text"
              autoFocus
              placeholder="örnek: note, research, audit"
              value={newFileDialog.filename}
              onChange={e => setNewFileDialog(d => ({ ...d, filename: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleCreateFile()
              }}
              className="glass"
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 8,
                background: 'rgba(8, 11, 16, 0.65)',
                border: '1px solid rgba(95, 204, 230, 0.4)',
                color: '#fff',
                fontSize: '0.85rem',
                outline: 'none',
              }}
            />

            {newFileDialog.error && (
              <span style={{ fontSize: '0.74rem', color: '#ff6b6b' }}>{newFileDialog.error}</span>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setNewFileDialog({ open: false, filename: '' })}
                className="glass glass-interactive"
                style={{
                  padding: '6px 14px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  color: 'var(--hb-text-dim, #9aa0a6)',
                  cursor: 'pointer',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleCreateFile}
                className="glass glass-interactive"
                style={{
                  padding: '6px 16px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: '#fff',
                  cursor: 'pointer',
                  background: 'linear-gradient(180deg, rgba(95, 204, 230, 0.3) 0%, rgba(54, 171, 202, 0.15) 100%), var(--glass-fill)',
                  border: '1px solid var(--hb-cyan-bright, #5fcce6)',
                }}
              >
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sub-Modal: New Folder Dialog — Stark Fluid Glass */}
      {newFolderDialog.open && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10000,
            background: 'rgba(2, 4, 8, 0.75)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            className="glass"
            style={{
              width: 380,
              borderRadius: 14,
              padding: '20px 22px',
              border: '1px solid rgba(255, 255, 255, 0.16)',
              background: 'linear-gradient(145deg, rgba(22, 26, 36, 0.88) 0%, rgba(12, 15, 22, 0.95) 100%), var(--glass-fill)',
              boxShadow: '0 24px 60px rgba(0,0,0,0.8), 0 0 30px rgba(242, 183, 92, 0.15)',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <GlassFolderIcon size={28} color="#f2b75c" badgeIcon="folder" glow={false} />
              <span
                style={{
                  fontFamily: "'Rajdhani', sans-serif",
                  fontSize: '0.98rem',
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  color: '#fff',
                }}
              >
                YENİ KLASÖR // NEW FOLDER
              </span>
            </div>

            <p style={{ fontSize: '0.78rem', color: 'var(--hb-text-dim, #9aa0a6)' }}>
              Hedef: <code>{currentDir ? `/memories/${currentDir}/` : '/memories/'}</code>
            </p>

            <input
              type="text"
              autoFocus
              placeholder="örnek: archive, notes, research"
              value={newFolderDialog.foldername}
              onChange={e => setNewFolderDialog(d => ({ ...d, foldername: e.target.value, error: undefined }))}
              onKeyDown={e => {
                if (e.key === 'Enter') handleCreateFolder()
              }}
              className="glass"
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 8,
                background: 'rgba(8, 11, 16, 0.65)',
                border: '1px solid rgba(242, 183, 92, 0.4)',
                color: '#fff',
                fontSize: '0.85rem',
                outline: 'none',
              }}
            />

            {newFolderDialog.error && (
              <span style={{ fontSize: '0.74rem', color: '#ff6b6b' }}>{newFolderDialog.error}</span>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
              <button
                onClick={() => setNewFolderDialog({ open: false, foldername: '' })}
                className="glass glass-interactive"
                style={{
                  padding: '6px 14px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  color: 'var(--hb-text-dim, #9aa0a6)',
                  cursor: 'pointer',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                }}
              >
                İptal
              </button>
              <button
                onClick={handleCreateFolder}
                className="glass glass-interactive"
                style={{
                  padding: '6px 16px',
                  borderRadius: 8,
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: '#fff',
                  cursor: 'pointer',
                  background: 'linear-gradient(180deg, rgba(242, 183, 92, 0.3) 0%, rgba(217, 156, 68, 0.15) 100%), var(--glass-fill)',
                  border: '1px solid var(--hb-amber-bright, #f2b75c)',
                }}
              >
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
