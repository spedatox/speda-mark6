// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { FileMeta, SubagentRun } from '../lib/types'
import { useT } from '../lib/i18n'
import { newRequestId } from '../lib/api'
import { workerName } from '../lib/subagentFold'
import SubagentDetailView from './SubagentDetailView'
import type { PreviewFile } from './FilePreview'
import '../theme/side-pane.css'

const FilePreview = lazy(() => import('./FilePreview'))

type PaneTab =
  | { id: string; kind: 'worker'; title: string; run: SubagentRun; scope?: string; ticket?: number }
  | { id: string; kind: 'file'; title: string; file: PreviewFile }

interface PaneActions {
  openWorker: (run: SubagentRun, options?: { scope?: string; ticket?: number }) => void
  updateWorkers: (scope: string, runs: SubagentRun[] | undefined) => void
  openFile: (file: FileMeta) => void
  openImage: (src: string, title: string, id: string) => void
  openLocal: (file: File) => void
  pickFile: () => void
}

function workerTabId(run: SubagentRun, scope?: string) {
  return run.executionId ? `execution:${run.executionId}` : `worker:${scope ?? ''}:${run.id}`
}

const ActionsContext = createContext<PaneActions | null>(null)
export const useSidePane = () => useContext(ActionsContext)

export function SidePaneProvider({ children }: {
  children: (options: {
    pane: ReactNode; visible: boolean; restore?: () => void; width: number; setWidth: (width: number) => void
  }) => ReactNode
}) {
  const t = useT()
  const [tabs, setTabs] = useState<PaneTab[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [visible, setVisible] = useState(false)
  const [width, setWidth] = useState(40)
  const picker = useRef<HTMLInputElement>(null)
  const activeTabRef = useRef<HTMLDivElement>(null)
  const localIds = useRef(new WeakMap<File, string>())
  const autobotNumbers = useRef(new Map<string, number>())
  const open = useCallback((tab: PaneTab) => {
    setTabs(previous => {
      const existing = previous.find(item => item.id === tab.id)
      if (!existing) return [...previous, tab]
      if (existing.kind === 'file' || tab.kind !== 'worker' || !tab.scope) return previous
      return previous.map(item => item.id === tab.id ? tab : item)
    })
    setSelected(tab.id)
    setVisible(true)
  }, [])
  const workerTitle = useCallback((id: string, run: SubagentRun) => {
    const name = workerName(run)
    if (name !== 'Autobot') return name
    // Forge sends the role and execution ID. Keep its display number for this window.
    const numbers = autobotNumbers.current
    const number = numbers.get(id) ?? numbers.size + 1
    numbers.set(id, number)
    return `Autobot ${number}`
  }, [])
  const actions = useMemo<PaneActions>(() => ({
    openWorker(run, { scope, ticket } = {}) {
      const id = workerTabId(run, scope)
      open({ id, kind: 'worker', title: workerTitle(id, run) || run.label, run, scope, ticket })
    },
    updateWorkers(scope, runs) {
      if (!runs?.length) return
      // Register transcript workers in display order, even before a tab opens.
      const titles = new Map(runs.map(run => {
        const id = workerTabId(run, scope)
        return [id, workerTitle(id, run)]
      }))
      setTabs(previous => {
        let changed = false
        const next = previous.map(tab => {
          if (tab.kind !== 'worker' || tab.scope !== scope) return tab
          const identity = tab.run.executionId || tab.run.id
          const run = runs.find(item => (item.executionId || item.id) === identity)
          if (!run || run === tab.run) return tab
          changed = true
          return { ...tab, title: titles.get(tab.id) || tab.title, run }
        })
        return changed ? next : previous
      })
    },
    openFile(file) {
      open({ id: `file:${file.url}`, kind: 'file', title: file.title || file.name, file: { source: 'server', file } })
    },
    openImage(src, title, id) {
      open({ id: `image:${id}`, kind: 'file', title, file: { source: 'image', src, name: title } })
    },
    openLocal(file) {
      const id = localIds.current.get(file) ?? `local:${newRequestId()}`
      localIds.current.set(file, id)
      open({ id, kind: 'file', title: file.name, file: { source: 'local', file } })
    },
    pickFile() { picker.current?.click() },
  }), [open, workerTitle])

  const activeIndex = tabs.findIndex(tab => tab.id === selected)
  const active = tabs[activeIndex] ?? tabs[0]
  useEffect(() => {
    if (visible) activeTabRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [selected, visible])
  const close = (id: string) => {
    const index = tabs.findIndex(tab => tab.id === id)
    if (active?.id === id) setSelected((tabs[index + 1] ?? tabs[index - 1])?.id ?? null)
    setTabs(previous => previous.filter(tab => tab.id !== id))
  }
  const pane = active && (
    <aside className="hb-side-pane" aria-label={t.sidePane.title} hidden={!visible} onKeyDown={event => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.stopPropagation()
        close(active.id)
      }
    }}>
      <div className="hb-side-pane-bar">
        <div className="hb-side-pane-tabs" role="group" aria-label={t.sidePane.tabs}>
          {tabs.map(tab => (
            <div key={tab.id} className="hb-side-pane-tab" data-active={visible && tab.id === active.id}>
              <button type="button" aria-pressed={tab.id === active.id} title={tab.title}
                onClick={() => setSelected(tab.id)}>
                <PaneIcon kind={tab.kind} />
                <span>{tab.title}</span>
              </button>
              <button type="button" className="hb-side-pane-icon" title={`${t.sidePane.closeTab} · ${tab.title}`}
                aria-label={`${t.sidePane.closeTab} · ${tab.title}`} onClick={() => close(tab.id)}>
                <PaneIcon kind="close" />
              </button>
            </div>
          ))}
        </div>
        <button type="button" className="hb-side-pane-icon hb-side-pane-add" title={t.sidePane.openFile}
          aria-label={t.sidePane.openFile} onClick={actions.pickFile}><PaneIcon kind="add" /></button>
        <button type="button" className="hb-side-pane-icon" title={t.sidePane.closePane} aria-label={t.sidePane.closePane}
          onClick={() => setVisible(false)}><PaneIcon kind="pane" /></button>
      </div>
      {tabs.map(tab => (
        <section key={tab.id} className="hb-side-pane-content" aria-label={tab.title}
          hidden={tab.id !== active.id}>
          {tab.kind === 'worker'
            ? <SubagentDetailView name={tab.title} run={tab.run} legacyTicket={tab.ticket} active={visible && tab.id === active.id} />
            : <Suspense fallback={<div className="hb-preview-note" role="status">{t.sidePane.loading}</div>}>
                <FilePreview file={tab.file} active={visible && tab.id === active.id} />
              </Suspense>}
        </section>
      ))}
    </aside>
  )

  return (
    <ActionsContext.Provider value={actions}>
      {children({ pane, visible: !!active && visible, restore: active && !visible ? () => setVisible(true) : undefined, width, setWidth })}
      <input ref={picker} type="file" hidden onChange={event => {
        const file = event.currentTarget.files?.[0]
        if (file) actions.openLocal(file)
        event.currentTarget.value = ''
      }} />
    </ActionsContext.Provider>
  )
}

export function SidePaneDivider({ width, setWidth }: { width: number; setWidth: (width: number) => void }) {
  const t = useT()
  const drag = useRef<{ x: number; width: number; total: number } | null>(null)
  const resize = (value: number) => setWidth(Math.min(60, Math.max(28, Math.round(value))))
  return (
    <div className="hb-side-pane-divider" role="separator" aria-orientation="vertical" tabIndex={0}
      aria-label={t.sidePane.resize} aria-valuemin={28} aria-valuemax={60} aria-valuenow={width}
      onPointerDown={event => {
        const total = event.currentTarget.parentElement?.clientWidth
        if (!total || event.button !== 0) return
        drag.current = { x: event.clientX, width, total }
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={event => {
        if (drag.current) resize(drag.current.width + (drag.current.x - event.clientX) * 100 / drag.current.total)
      }}
      onLostPointerCapture={() => { drag.current = null }}
      onKeyDown={event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        resize(width + (event.key === 'ArrowLeft' ? 2 : -2))
      }} />
  )
}

export function sidePaneStyle(width: number): CSSProperties {
  return { '--hb-side-pane-preferred-width': `${width}%` } as CSSProperties
}

function PaneIcon({ kind }: { kind: 'worker' | 'file' | 'close' | 'add' | 'pane' }) {
  const paths = {
    worker: 'M5 8h14v10H5zM12 8V5m-3 7h.01m6 0h.01M9 15h6M2 11v4m20-4v4',
    file: 'M7 3h7l4 4v14H7zM14 3v5h4M10 12h5m-5 4h5',
    close: 'm7 7 10 10M17 7 7 17',
    add: 'M12 5v14M5 12h14',
    pane: 'M3 5h18v14H3zM14 5v14',
  }
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[kind]} /></svg>
}
