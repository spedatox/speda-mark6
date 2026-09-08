// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react'
import { useChatContext } from '../store/chat'
import { useSettings } from '../store/settings'
import { fetchProjects } from '../lib/api'
import HisarBrowser from './HisarBrowser'
import type { AppConfig } from '../lib/types'
import { useT } from '../lib/i18n'

/**
 * THE DECK BAR — the chat column's title row, plus the floating icon rail.
 *
 * This replaced the old 40px header plate. In the deck the chrome does not sit
 * in a bar across the top: the session title reads inline over the transcript,
 * a single status pill states whether the agent is working, and the mode
 * switches float as glass tiles at the top-right — directly above the telemetry
 * column they sit over. Nothing here draws a background of its own; the bar is
 * transparent and the tiles are the only material.
 *
 * The readouts the old header carried (message count, token spend, QUERY
 * COMPLETE) moved to the telemetry column, which is where the rest of the
 * session's instrumentation now lives.
 */

/**
 * Workspace selector for anonymous Forge workers deployed through Legion.
 * Every Mark VI persona can delegate against the selected workspace.
 */
function ForgeLink({ config }: { config: AppConfig }) {
  const t = useT()
  const { settings, update } = useSettings()
  const [browserOpen, setBrowserOpen] = useState(false)
  const cwd = settings.forgeCwd
  // Show the trailing folder name (the meaningful part). Hisar paths are always
  // POSIX with forward slashes — the UI uses the vault path as-is.
  const folderName = cwd ? (cwd.replace(/\/+$/, '').split('/').pop() || cwd) : ''

  return (
    <>
      {browserOpen && (
        <HisarBrowser
          config={config}
          current={cwd}
          onSelect={(path) => {
            update({ forgeCwd: path || '' })
            setBrowserOpen(false)
          }}
          onClose={() => setBrowserOpen(false)}
        />
      )}
      <span className="hb-hide-sm" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span className="hb-query-box" style={{
          display: 'flex', alignItems: 'center', gap: 6, height: 24,
          maxWidth: 200, padding: '0 0.2rem 0 0.45rem',
        }}>
          <button
            onClick={() => setBrowserOpen(true)}
            title={cwd ? t.header.workspaceSet(cwd) : t.header.workspaceUnset}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, minWidth: 0,
              border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
              fontFamily: 'var(--font-mono)', fontSize: '0.66rem', letterSpacing: '0.02em',
              color: cwd ? 'var(--hb-text-dim)' : 'var(--hb-text-faint)',
            }}
          >
            {/* Hisar vault glyph — cloud-download, marking it as the network vault */}
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            </svg>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {folderName || t.header.setWorkspace}
            </span>
          </button>
          {cwd && (
            <button
              onClick={() => update({ forgeCwd: '' })}
              title={t.header.clearWorkspace}
              style={{
                display: 'flex', alignItems: 'center', flexShrink: 0,
                border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
                color: 'var(--hb-text-faint)',
              }}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </span>
      </span>
    </>
  )
}

/**
 * A rail tile. 44px glass square on the --r-tile corner. `active` is the ONLY
 * thing that brings colour in: an inactive rail is neutral glass, matching the
 * rule that the accent marks state rather than decorating chrome.
 */
function RailTile({ active, onClick, title, children }: {
  active?: boolean
  onClick: () => void
  title: string
  children: React.ReactNode
}) {
  return (
    <button
      className={active ? 'hb-btn hb-tile glass-active' : 'hb-btn hb-tile'}
      onClick={onClick}
      title={title}
      style={{
        width: 44, height: 44, flexShrink: 0,
        color: active ? 'var(--hb-cyan-bright)' : 'var(--hb-icon-bright)',
        ...(active ? { background: 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.26), rgba(var(--hb-accent-rgb),0.08))' } : {}),
      }}
    >
      {children}
    </button>
  )
}

export interface RailProps {
  boardOpen?: boolean
  onToggleBoard?: () => void
  commsOpen?: boolean
  onToggleComms?: () => void
  voiceOpen?: boolean
  onToggleVoice?: () => void
  /** The right telemetry column — permanent by default, collapsible. */
  telemetryOpen?: boolean
  onToggleTelemetry?: () => void
  /** True when the app IS the war room (standby or engaged takeover). While
   *  true the roster strip carries the exit control, so the rail's war-room
   *  tile hides. */
  inWarRoom?: boolean
  onOpenWarRoom?: () => void
}

/**
 * THE RAIL — the deck's mode switches, floating at the window's top-right.
 *
 * Rendered by Layout as a direct child of the deck root, NOT inside the chat
 * column: the column clips its overflow, which would pin the rail to the
 * column's right edge instead of the window's. Floating it here is what puts
 * it directly above the telemetry column, and what keeps it reachable when
 * that column is folded away — it holds the control that unfolds it.
 */
export function DeckRail({
  boardOpen, onToggleBoard, commsOpen, onToggleComms,
  voiceOpen, onToggleVoice, telemetryOpen, onToggleTelemetry,
  inWarRoom, onOpenWarRoom,
}: RailProps) {
  const t = useT()
  return (
    <div style={{
      position: 'absolute', top: 20, right: 20, zIndex: 30,
      display: 'flex', gap: 8,
    }}>
      {!inWarRoom && onOpenWarRoom && (
        <RailTile
          onClick={onOpenWarRoom}
          title={t.header.enterWarRoom}
        >
          {/* Command table — the roster converging on a centre point */}
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
            <circle cx="12" cy="12" r="3" />
            <circle cx="12" cy="3.5" r="1.6" /><circle cx="19.5" cy="16.5" r="1.6" /><circle cx="4.5" cy="16.5" r="1.6" />
            <line x1="12" y1="5.1" x2="12" y2="9" />
            <line x1="18.1" y1="15.6" x2="14.6" y2="13.5" />
            <line x1="5.9" y1="15.6" x2="9.4" y2="13.5" />
          </svg>
        </RailTile>
      )}

      {onToggleComms && (
        <RailTile
          active={commsOpen}
          onClick={onToggleComms}
          title={commsOpen ? t.header.closeComms : t.header.openComms}
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
            <circle cx="5" cy="12" r="2.4"/><circle cx="19" cy="5" r="2.4"/><circle cx="19" cy="19" r="2.4"/>
            <line x1="7.2" y1="11" x2="16.8" y2="5.9"/><line x1="7.2" y1="13" x2="16.8" y2="18.1"/>
          </svg>
        </RailTile>
      )}

      {onToggleTelemetry && (
        <RailTile
          active={telemetryOpen}
          onClick={onToggleTelemetry}
          title={telemetryOpen ? t.header.hideTelemetry : t.header.showTelemetry}
        >
          {/* Pulse trace, not the bar meter. The four-bar glyph is an audio
              equaliser to anyone who has used a media player, so it belongs to
              voice; a running trace is what telemetry actually looks like. */}
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M4 12h4l2.5 6 3-12 2.5 6h4"/>
          </svg>
        </RailTile>
      )}

      {onToggleBoard && (
        <RailTile
          active={boardOpen}
          onClick={onToggleBoard}
          title={boardOpen ? t.header.closeBoard : t.header.openBoard}
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>
            <rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>
          </svg>
        </RailTile>
      )}

      {onToggleVoice && (
        <RailTile
          active={voiceOpen}
          onClick={onToggleVoice}
          title={voiceOpen ? t.header.leaveVoice : t.header.openVoice}
        >
          {/* The equaliser bars — this is the one people read as "audio". */}
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M4 15V9M9 18V6M14 14v-4M19 16v-8"/>
          </svg>
        </RailTile>
      )}
    </div>
  )
}

interface Props {
  config: AppConfig
  sidebarOpen?: boolean
  onToggleSidebar?: () => void
  /** Width the title row must keep clear on its right so the floating rail,
   *  which is not in this flow, never lands on top of a long session title. */
  railClearance?: number
}

export default function Header({
  config, sidebarOpen, onToggleSidebar, railClearance = 0,
}: Props) {
  const t = useT()
  const { state } = useChatContext()
  const activeSession = state.sessions.find(s => s.id === state.activeSessionId)
  // The project badge. Read from the open session's own row where there is one;
  // on a chat that does not exist yet (New chat inside a project) the session
  // list has nothing to read, so fall back to the store's activeProjectId and
  // resolve the name off the pinned list the sidebar already fetched.
  const [projectNames, setProjectNames] = useState<Record<number, string>>({})
  useEffect(() => {
    if (!state.activeProjectId) return
    let alive = true
    fetchProjects(config).then(list => {
      if (alive) setProjectNames(Object.fromEntries(list.map(p => [p.id, p.name])))
    })
    return () => { alive = false }
  }, [config, state.activeProjectId])
  const projectName =
    activeSession?.project_name ||
    (state.activeProjectId ? projectNames[state.activeProjectId] : '') ||
    ''

  return (
    <div style={{
      height: 64, flexShrink: 0,
      display: 'flex', alignItems: 'center', gap: 14,
      padding: '0 4px 0 30px',
      position: 'relative', zIndex: 10,
    }}>
      {!sidebarOpen && onToggleSidebar && (
        <button
          className="hb-btn hb-tile"
          onClick={onToggleSidebar}
          title={t.header.openPanel}
          style={{ width: 36, height: 36, flexShrink: 0 }}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </button>
      )}

      {/* Session title — reads inline over the deck, no plate behind it */}
      <span
        title={activeSession?.title || undefined}
        style={{
          fontFamily: 'var(--font-ui)', fontSize: 22, fontWeight: 600,
          letterSpacing: '0.02em', color: 'var(--hb-text)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          maxWidth: '46%', flexShrink: 1, minWidth: 0,
        }}
      >
        {activeSession?.title || t.header.newConversation}
      </span>

      {/* Which workspace this conversation belongs to. It sits beside the title
          rather than inside it because the project is the CONTEXT the answers
          are being written under — standing instructions and a knowledge base
          the transcript never shows — and the owner has to be able to see that
          without opening the project. On a brand-new chat started from a
          project it appears before the first word is typed, which is the point:
          you know which workspace you are about to write into. */}
      {projectName && (
        <span
          title={`${t.projects.inProject}: ${projectName}`}
          className="glass-round"
          style={{
            display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
            maxWidth: 220, height: 24, padding: '0 10px',
            border: '1px solid rgba(var(--hb-accent-rgb),0.28)',
            fontFamily: 'var(--font-mono)', fontSize: '0.64rem',
            letterSpacing: '0.06em', color: 'var(--hb-cyan)',
            overflow: 'hidden',
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          </svg>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {projectName}
          </span>
        </span>
      )}

      {/* Live state — the pill appears only while the agent is actually working,
          so a settled deck stays quiet. */}
      {state.isStreaming && (
        <span className="glass-round" style={{
          display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0,
          height: 28, padding: '0 12px 0 10px',
          background: 'linear-gradient(160deg, rgba(var(--hb-accent-rgb),0.18), rgba(var(--hb-accent-rgb),0.05))',
          border: '1px solid rgba(var(--hb-accent-rgb),0.3)',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.16)',
          fontSize: 13, color: 'var(--hb-cyan-bright)',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--hb-cyan)', boxShadow: '0 0 8px var(--hb-cyan)',
            animation: 'hbPulse 1.4s ease-in-out infinite',
          }} />
          {t.header.responding}
        </span>
      )}

      <div style={{ flex: 1, minWidth: 0 }} />

      {/* Forge link — Optimus engine state + workspace (Optimus only) */}
      <ForgeLink config={config} />

      {/* The rail is NOT here — it floats above the telemetry column, out of
          this column's clipped overflow. Layout renders <DeckRail/>. This
          keeps the space it occupies clear so a long title never runs under it. */}
      <div style={{ width: railClearance, flexShrink: 0 }} />
    </div>
  )
}
