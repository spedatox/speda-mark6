// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Past firings of one automation — "did it actually run, and what did it
 * say". Rendered inline in place of the automations list, same convention
 * AutomationBuilder uses (see its own header comment for why: a second
 * overlay above the Settings overlay fights it for the backdrop/Escape key).
 *
 * A run's content is flat text, not a multi-step trace, so this stays a
 * simple list with per-row expand/collapse for the report rather than a
 * second full-screen detail view (contrast SubagentDetailView, which earns
 * its own overlay because a delegation trace has real structure to show).
 */

import { useEffect, useState } from 'react'
import { useT } from '../lib/i18n'
import { PillBtn, SettingsSection } from './settingsUI'
import { SkeletonList } from './Skeleton'
import { getAutomationRuns } from '../lib/api'
import type { AppConfig } from '../lib/types'
import type { AutomationInfo, AutomationRun } from '../lib/api'

const REPORT_CLAMP = 220

function statusColor(status: string): string {
  if (status === 'ok') return 'var(--hb-green)'
  if (status === 'failed') return '#e5897c'
  return 'var(--hb-text-faint)'
}

function RunRow({ run, t }: { run: AutomationRun; t: ReturnType<typeof useT> }) {
  const a = t.settingsAutomations
  const [open, setOpen] = useState(false)
  const statusLabel = { ok: a.runStatusOk, failed: a.runStatusFailed, cancelled: a.runStatusCancelled }
    [run.status as 'ok' | 'failed' | 'cancelled'] ?? run.status
  const report = run.report?.trim() ?? ''
  const clipped = report.length > REPORT_CLAMP
  const shown = open || !clipped ? report : `${report.slice(0, REPORT_CLAMP)}…`

  return (
    <div className="hb-tile" style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '12px 16px',
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.07)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
          background: statusColor(run.status),
        }} />
        <span style={{ fontSize: '0.875rem', color: 'var(--hb-text)' }}>
          {run.fired_at ? new Date(run.fired_at).toLocaleString() : ''}
        </span>
        <span style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)' }}>
          {statusLabel}
          {run.channel === 'voice' && ' · 🔊'}
        </span>
      </div>
      {run.status === 'ok' && run.channel !== 'silent' && !run.delivered && (
        <div style={{ fontSize: '0.78125rem', color: 'var(--hb-amber-bright)' }}>
          {a.runNotDelivered}
        </div>
      )}
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
        {report ? shown : a.runNoReport}
      </div>
      {clipped && (
        <button
          onClick={() => setOpen(o => !o)}
          style={{
            alignSelf: 'flex-start', background: 'transparent', border: 'none',
            color: 'var(--hb-cyan-bright)', fontSize: '0.78125rem', cursor: 'pointer', padding: 0,
          }}
        >
          {open ? a.runLess : a.runMore}
        </button>
      )}
    </div>
  )
}

export function AutomationRunHistory({ automation, config, onClose }: {
  automation: AutomationInfo
  config: AppConfig
  onClose: () => void
}) {
  const t = useT()
  const a = t.settingsAutomations
  const [runs, setRuns] = useState<AutomationRun[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoaded(false)
    getAutomationRuns(config, automation.id).then(r => {
      if (!cancelled) { setRuns(r); setLoaded(true) }
    })
    return () => { cancelled = true }
  }, [config, automation.id])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 720 }}>
      <SettingsSection title={`${a.historyTitle} · ${automation.name}`} first />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: -8 }}>
        {!loaded ? (
          <SkeletonList rows={3} mark={false} />
        ) : runs.length === 0 ? (
          <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)' }}>{a.historyEmpty}</p>
        ) : (
          runs.map(r => <RunRow key={r.id} run={r} t={t} />)
        )}
      </div>
      <div>
        <PillBtn onClick={onClose}>{t.common.close}</PillBtn>
      </div>
    </div>
  )
}
