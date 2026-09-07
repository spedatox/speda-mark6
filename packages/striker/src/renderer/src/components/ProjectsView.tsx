// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Projects — the workspace surface.
 *
 * A project owns three things a loose chat has none of: its own chat list, its
 * own standing instructions, and its own knowledge base. This view is both
 * halves of that: a grid of project cards, and the detail pane for one project.
 *
 * Isolation is inherited, not implemented here. Every call goes through
 * lib/api's project functions, which stamp `config.agentId` on the request, and
 * the backend refuses a cross-agent read — so switching agents switches the
 * whole project set exactly the way it already switches chat history. This
 * component never sees another agent's project and has no code path that could.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AppConfig, Project, ProjectFile, Session } from '../lib/types'
import {
  createProject, deleteProject, deleteProjectFile, fetchProjectFiles,
  fetchProjects, fetchProjectSessions, updateProject, uploadProjectFile,
} from '../lib/api'
import { useT } from '../lib/i18n'
import { SkeletonList } from './Skeleton'

/* Striker is the single-agent build — there is no roster and no per-agent
 * accent to read off a profile, so a project with no colour of its own falls
 * back to the one theme accent. */
const ACCENT = 'var(--hb-cyan)'

/* ── Shared micro-styles ──────────────────────────────────────────────────── */
const label: React.CSSProperties = {
  fontFamily: "'Rajdhani',sans-serif",
  fontSize: '0.72rem', fontWeight: 700,
  letterSpacing: '0.16em', textTransform: 'uppercase',
  color: 'var(--hb-icon-dim)',
}
const body: React.CSSProperties = {
  fontFamily: "'SamsungOne','Inter',sans-serif",
  fontSize: '0.855rem', lineHeight: 1.5, color: 'var(--hb-text-dim)',
}
const fieldBox: React.CSSProperties = {
  width: '100%', padding: '0.55rem 0.7rem',
  fontFamily: "'SamsungOne','Inter',sans-serif",
  fontSize: '0.87rem', lineHeight: 1.5,
  userSelect: 'text', resize: 'vertical',
}

function fmtBytes(n: number): string {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** Date only — a project card is about "what am I working on", not the minute. */
function fmtDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleDateString(locale === 'tr' ? 'tr-TR' : 'en-US', {
      month: 'short', day: 'numeric',
    })
  } catch { return '' }
}

/* ── Icons ────────────────────────────────────────────────────────────────── */
const Ico = {
  plus: <path d="M12 5v14M5 12h14" />,
  search: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></>,
  back: <path d="M19 12H5M12 19l-7-7 7-7" />,
  pin: <path d="M12 17v5M9 2h6l-1 7 4 3v2H6v-2l4-3-1-7z" />,
  archive: <><rect x="3" y="4" width="18" height="4" /><path d="M5 8v12h14V8M10 12h4" /></>,
  trash: <><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></>,
  chat: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  folder: <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />,
  x: <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>,
}

function Glyph({ d, size = 14, width = 2 }: { d: React.ReactNode; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={width} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {d}
    </svg>
  )
}

/* ── Buttons ──────────────────────────────────────────────────────────────── */
function Btn({ children, onClick, tint, disabled, title }: {
  children: React.ReactNode; onClick: () => void
  tint?: string; disabled?: boolean; title?: string
}) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={onClick}
      // Material lives in .hb-btn / .hb-btn-tint; the accent is passed as
      // `color` only — an inline border/background would win the cascade and
      // break the one glass recipe (see heartbreaker.css).
      className={`hb-btn hb-glass-sm${tint ? ' hb-btn-tint' : ''}`}
      style={{
        height: 32, padding: '0 0.85rem',
        fontFamily: "'Rajdhani',sans-serif",
        fontSize: '0.74rem', fontWeight: 700,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        ...(tint ? { color: tint } : {}),
      }}
    >
      {children}
    </button>
  )
}

function IconBtn({ children, onClick, title, danger }: {
  children: React.ReactNode; onClick: (e: React.MouseEvent) => void; title: string; danger?: boolean
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="hb-btn-ghost"
      style={{
        width: 26, height: 26,
        color: hover ? (danger ? '#c84a3a' : 'var(--hb-cyan-bright)') : 'var(--hb-icon-dim)',
      }}
    >
      {children}
    </button>
  )
}

/* ── Project card ─────────────────────────────────────────────────────────── */
function ProjectCard({ project, accent, locale, onOpen, onPin, onArchive }: {
  project: Project; accent: string; locale: string
  onOpen: () => void; onPin: () => void; onArchive: () => void
}) {
  const t = useT()
  const [hover, setHover] = useState(false)
  const tint = project.color || accent

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onOpen}
      className="hb-holo hb-glass"
      style={{
        position: 'relative',
        padding: '1rem 1.05rem 0.85rem',
        minHeight: 152,
        display: 'flex', flexDirection: 'column',
        cursor: 'pointer',
        // The pinned/hovered card is the only one that lights its rim — a grid
        // where every tile glows reads as noise, not as a set of choices.
        boxShadow: hover ? 'var(--glass-shadow-active)' : undefined,
        borderColor: hover ? 'var(--hb-edge-bright)' : undefined,
        opacity: project.archived ? 0.55 : 1,
        transition: 'box-shadow 0.14s, border-color 0.14s, opacity 0.14s',
      }}
    >
      {/* The accent tab — a project's one piece of colour, so a familiar grid
          is scannable by hue before it is readable by name. */}
      <span style={{
        position: 'absolute', left: 0, top: 14, bottom: 14, width: 2,
        background: tint, opacity: project.pinned ? 0.95 : 0.4,
        boxShadow: project.pinned ? `0 0 8px ${tint}` : undefined,
      }} />

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.55rem' }}>
        {project.icon ? (
          <span style={{ fontSize: '1.05rem', lineHeight: 1.2, flexShrink: 0 }}>{project.icon}</span>
        ) : (
          <span style={{ color: tint, display: 'flex', paddingTop: 2, flexShrink: 0 }}>
            <Glyph d={Ico.folder} size={15} width={1.6} />
          </span>
        )}
        <h3 style={{
          flex: 1, minWidth: 0,
          fontFamily: "'SamsungOne','Inter',sans-serif",
          fontSize: '1rem', fontWeight: 600, lineHeight: 1.3,
          color: 'var(--hb-text)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {project.name}
        </h3>
        {hover && (
          <div style={{ display: 'flex', gap: 1, marginRight: -6, marginTop: -3 }}
               onClick={e => e.stopPropagation()}>
            <IconBtn title={project.pinned ? t.projects.unpin : t.projects.pin} onClick={onPin}>
              <Glyph d={Ico.pin} size={12} width={1.8} />
            </IconBtn>
            <IconBtn title={project.archived ? t.projects.unarchive : t.projects.archive} onClick={onArchive}>
              <Glyph d={Ico.archive} size={12} width={1.8} />
            </IconBtn>
          </div>
        )}
      </div>

      {project.description && (
        <p style={{
          ...body, marginTop: '0.55rem', flex: 1,
          display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}>
          {project.description}
        </p>
      )}
      {!project.description && <div style={{ flex: 1 }} />}

      {/* Footer — counts on the left, last activity on the right. Both are
          facts about the workspace, which is what a card is for. */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        marginTop: '0.85rem',
        fontFamily: 'var(--font-mono)', fontSize: '0.62rem',
        letterSpacing: '0.06em', color: 'var(--hb-text-faint)',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <Glyph d={Ico.chat} size={10} width={2} /> {project.chat_count}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <Glyph d={Ico.file} size={10} width={2} /> {project.file_count}
        </span>
        {project.archived && <span style={{ color: 'var(--hb-amber)' }}>{t.projects.archived}</span>}
        <span style={{ marginLeft: 'auto' }}>{fmtDate(project.last_activity_at, locale)}</span>
      </div>
    </div>
  )
}

/* ── New-project composer ─────────────────────────────────────────────────── */
function NewProjectForm({ onCancel, onCreate }: {
  onCancel: () => void
  onCreate: (v: { name: string; description: string; instructions: string; icon: string }) => Promise<void>
}) {
  const t = useT()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const [icon, setIcon] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!name.trim() || busy) return
    setBusy(true)
    try { await onCreate({ name, description, instructions, icon }) }
    finally { setBusy(false) }
  }

  return (
    <div className="hb-holo hb-glass" style={{ padding: '1.1rem 1.2rem', marginBottom: '1.25rem' }}>
      <div style={{ ...label, marginBottom: '0.75rem' }}>{t.projects.newProject}</div>

      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.65rem' }}>
        <input
          value={icon}
          onChange={e => setIcon(e.target.value.slice(0, 2))}
          placeholder="🗂"
          title={t.projects.newProject}
          className="hb-field hb-glass-sm"
          style={{ ...fieldBox, width: 52, textAlign: 'center', fontSize: '1rem' }}
        />
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onCancel() }}
          placeholder={t.projects.namePlaceholder}
          className="hb-field hb-glass-sm"
          style={fieldBox}
        />
      </div>

      <input
        value={description}
        onChange={e => setDescription(e.target.value)}
        placeholder={t.projects.descriptionPlaceholder}
        className="hb-field hb-glass-sm"
        style={{ ...fieldBox, marginBottom: '0.65rem' }}
      />
      <textarea
        value={instructions}
        onChange={e => setInstructions(e.target.value)}
        placeholder={t.projects.instructionsPlaceholder}
        rows={3}
        className="hb-field hb-glass-sm"
        style={{ ...fieldBox, marginBottom: '0.85rem' }}
      />

      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <Btn onClick={onCancel}>{t.projects.cancel}</Btn>
        <Btn onClick={submit} tint="var(--hb-cyan-bright)" disabled={!name.trim() || busy}>
          {t.projects.create}
        </Btn>
      </div>
    </div>
  )
}

/* ── Detail pane ──────────────────────────────────────────────────────────── */
function ProjectDetail({ config, project, accent, locale, onBack, onChanged, onOpenChat, onNewChat }: {
  config: AppConfig; project: Project; accent: string; locale: string
  onBack: () => void
  onChanged: (p: Project | null) => void
  onOpenChat: (sessionId: number) => void
  onNewChat: () => void
}) {
  const t = useT()
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description)
  const [instructions, setInstructions] = useState(project.instructions)
  const [saved, setSaved] = useState(false)
  const [sessions, setSessions] = useState<Session[] | null>(null)
  const [files, setFiles] = useState<ProjectFile[] | null>(null)
  const [uploading, setUploading] = useState<string[]>([])
  const [uploadError, setUploadError] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const tint = project.color || accent

  useEffect(() => {
    setName(project.name)
    setDescription(project.description)
    setInstructions(project.instructions)
  }, [project.id, project.name, project.description, project.instructions])

  useEffect(() => {
    let alive = true
    fetchProjectSessions(config, project.id).then(s => { if (alive) setSessions(s) })
    fetchProjectFiles(config, project.id).then(f => { if (alive) setFiles(f) })
    return () => { alive = false }
  }, [config, project.id])

  const dirty =
    name !== project.name ||
    description !== project.description ||
    instructions !== project.instructions

  const save = useCallback(async () => {
    if (!dirty || !name.trim()) return
    const updated = await updateProject(config, project.id, { name, description, instructions })
    onChanged(updated)
    setSaved(true)
    setTimeout(() => setSaved(false), 1600)
  }, [config, project.id, name, description, instructions, dirty, onChanged])

  const addFiles = useCallback(async (picked: FileList | null) => {
    if (!picked?.length) return
    setUploadError('')
    const list = Array.from(picked)
    setUploading(list.map(f => f.name))
    // Sequential, not Promise.all: each upload runs a server-side extractor,
    // and the per-project file cap is checked per request — firing them at once
    // would race past the limit and report the failures out of order.
    for (const file of list) {
      try {
        const added = await uploadProjectFile(config, project.id, file)
        setFiles(prev => [added, ...(prev ?? [])])
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err))
      }
      setUploading(prev => prev.filter(n => n !== file.name))
    }
    setUploading([])
  }, [config, project.id])

  const removeFile = useCallback(async (fileId: number) => {
    setFiles(prev => (prev ?? []).filter(f => f.id !== fileId))
    await deleteProjectFile(config, project.id, fileId)
  }, [config, project.id])

  const remove = useCallback(async () => {
    if (!window.confirm(t.projects.deleteConfirm)) return
    await deleteProject(config, project.id)
    onChanged(null)
    onBack()
  }, [config, project.id, onChanged, onBack, t.projects.deleteConfirm])

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 0 3rem' }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1.1rem' }}>
        <IconBtn title={t.projects.back} onClick={onBack}>
          <Glyph d={Ico.back} size={14} />
        </IconBtn>
        {project.icon && <span style={{ fontSize: '1.2rem' }}>{project.icon}</span>}
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onBlur={save}
          style={{
            flex: 1, minWidth: 0,
            background: 'transparent', border: 'none', outline: 'none',
            fontFamily: "'SamsungOne','Inter',sans-serif",
            fontSize: '1.4rem', fontWeight: 600, color: 'var(--hb-text)',
            userSelect: 'text',
          }}
        />
        <Btn onClick={onNewChat} tint={tint}>
          <Glyph d={Ico.plus} size={12} /> {t.projects.newChatHere}
        </Btn>
        <IconBtn title={t.projects.delete} onClick={remove} danger>
          <Glyph d={Ico.trash} size={13} width={1.8} />
        </IconBtn>
      </div>

      {/* Instructions */}
      <section style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.45rem' }}>
          <span style={label}>{t.projects.instructions}</span>
          {dirty && <Btn onClick={save} tint="var(--hb-cyan-bright)">{t.projects.save}</Btn>}
          {saved && !dirty && (
            <span style={{ ...label, color: 'var(--hb-green)' }}>{t.projects.saved}</span>
          )}
        </div>
        <p style={{ ...body, fontSize: '0.78rem', marginBottom: '0.55rem', color: 'var(--hb-text-faint)' }}>
          {t.projects.instructionsBlurb}
        </p>
        <textarea
          value={instructions}
          onChange={e => setInstructions(e.target.value)}
          placeholder={t.projects.instructionsPlaceholder}
          rows={6}
          className="hb-field hb-glass-sm"
          style={fieldBox}
        />
        <input
          value={description}
          onChange={e => setDescription(e.target.value)}
          onBlur={save}
          placeholder={t.projects.descriptionPlaceholder}
          className="hb-field hb-glass-sm"
          style={{ ...fieldBox, marginTop: '0.5rem' }}
        />
      </section>

      {/* Knowledge */}
      <section style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.45rem' }}>
          <span style={label}>{t.projects.knowledge}</span>
          <Btn onClick={() => picker.current?.click()}>
            <Glyph d={Ico.plus} size={12} /> {t.projects.addFiles}
          </Btn>
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={e => { addFiles(e.target.files); e.target.value = '' }}
          />
        </div>
        <p style={{ ...body, fontSize: '0.78rem', marginBottom: '0.55rem', color: 'var(--hb-text-faint)' }}>
          {t.projects.knowledgeBlurb}
        </p>
        {uploadError && (
          <p style={{ ...body, fontSize: '0.78rem', color: '#c84a3a', marginBottom: '0.5rem' }}>
            {uploadError}
          </p>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {uploading.map(n => (
            <span key={`up-${n}`} className="hb-glass-sm" style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
              padding: '0.35rem 0.6rem',
              fontFamily: 'var(--font-mono)', fontSize: '0.66rem',
              color: 'var(--hb-cyan)',
            }}>
              <Glyph d={Ico.file} size={11} width={1.8} /> {n} · {t.projects.uploading}…
            </span>
          ))}
          {files?.map(f => (
            <span key={f.id} className="hb-glass-sm" style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
              padding: '0.35rem 0.4rem 0.35rem 0.6rem',
              fontFamily: 'var(--font-mono)', fontSize: '0.66rem',
              color: 'var(--hb-text-dim)',
            }}>
              <Glyph d={Ico.file} size={11} width={1.8} />
              {f.name}
              {!!f.size && (
                <span style={{ color: 'var(--hb-text-faint)' }}>{fmtBytes(f.size)}</span>
              )}
              <IconBtn title={t.projects.remove} onClick={() => removeFile(f.id)} danger>
                <Glyph d={Ico.x} size={10} width={2.5} />
              </IconBtn>
            </span>
          ))}
          {files && !files.length && !uploading.length && (
            <span style={{ ...body, fontSize: '0.78rem', color: 'var(--hb-text-faint)' }}>
              {t.projects.noFiles}
            </span>
          )}
        </div>
      </section>

      {/* Chats */}
      <section>
        <div style={{ ...label, marginBottom: '0.55rem' }}>{t.projects.chats}</div>
        {!sessions ? (
          <SkeletonList rows={3} mark={false} markSize={24} />
        ) : sessions.length === 0 ? (
          <p style={{ ...body, fontSize: '0.82rem', color: 'var(--hb-text-faint)' }}>
            {t.projects.noChats}
          </p>
        ) : (
          sessions.map(s => (
            <button
              key={s.id}
              onClick={() => onOpenChat(s.id)}
              className="hb-row"
              style={{
                width: '100%', padding: '0.6rem 0.75rem',
                display: 'flex', alignItems: 'center', gap: '0.6rem',
                cursor: 'pointer', textAlign: 'left',
                color: 'var(--hb-text-dim)',
                fontFamily: "'SamsungOne','Inter',sans-serif", fontSize: '0.885rem',
              }}
            >
              <span style={{ color: tint, display: 'flex' }}>
                <Glyph d={Ico.chat} size={12} width={1.8} />
              </span>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.title || t.sidebar.newConversation}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: '0.62rem', color: 'var(--hb-text-faint)',
              }}>
                {fmtDate(s.started_at, locale)}
              </span>
            </button>
          ))
        )}
      </section>
    </div>
  )
}

/* ── Main view ────────────────────────────────────────────────────────────── */
interface Props {
  config: AppConfig
  locale: string
  /** Which project to open on mount, if the caller came from a sidebar row. */
  initialProjectId?: number | null
  onClose: () => void
  /** Open one of the project's chats in the chat view. */
  onOpenChat: (sessionId: number, projectId: number) => void
  /** Start a new chat bound to this project. */
  onNewChat: (projectId: number) => void
}

export default function ProjectsView({
  config, locale, initialProjectId, onClose, onOpenChat, onNewChat,
}: Props) {
  const t = useT()
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(initialProjectId ?? null)
  // The view stays mounted between openings, so a second tap on the shelf changes
  // this prop without remounting — follow it, or the second tap does nothing.
  useEffect(() => {
    if (initialProjectId != null) setOpenId(initialProjectId)
  }, [initialProjectId])
  const [creating, setCreating] = useState(false)
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)

  const reload = useCallback(async () => {
    setProjects(await fetchProjects(config, showArchived))
  }, [config, showArchived])

  /** The sidebar keeps its own copy of the pinned projects, so anything that
   *  changes the shelf has to say so. One event, raised from the four places
   *  that can change it, beats the sidebar polling for a list that changes a
   *  handful of times a week. */
  const announce = useCallback(() => {
    window.dispatchEvent(new CustomEvent('speda:projects-changed'))
  }, [])

  useEffect(() => { reload() }, [reload])

  // An agent switch rewrites config.agentId, which reloads the list above. The
  // OPEN project has to close with it: it belongs to the agent we just left,
  // and the backend would (correctly) refuse every call it makes.
  //
  // Guarded on an actual CHANGE, not on the effect firing: an effect also runs
  // on MOUNT, and a bare setOpenId(null) here nulled out the initialProjectId we
  // were opened with — so tapping a project on the sidebar shelf dropped the
  // owner on the grid every single time.
  const lastAgentId = useRef(config.agentId)
  useEffect(() => {
    if (lastAgentId.current === config.agentId) return
    lastAgentId.current = config.agentId
    setOpenId(null)
  }, [config.agentId])

  const open = useMemo(
    () => projects?.find(p => p.id === openId) ?? null,
    [projects, openId]
  )

  const filtered = useMemo(() => {
    const list = projects ?? []
    const q = search.trim().toLowerCase()
    if (!q) return list
    return list.filter(p =>
      p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q)
    )
  }, [projects, search])

  const patch = useCallback((updated: Project | null, id: number) => {
    setProjects(prev => {
      const list = prev ?? []
      if (!updated) return list.filter(p => p.id !== id)
      return list.map(p => (p.id === id ? { ...p, ...updated } : p))
    })
  }, [])

  const togglePin = useCallback(async (p: Project) => {
    const updated = await updateProject(config, p.id, { pinned: !p.pinned })
    patch(updated, p.id)
    reload()
    announce()
  }, [config, patch, reload, announce])

  const toggleArchive = useCallback(async (p: Project) => {
    const updated = await updateProject(config, p.id, { archived: !p.archived })
    patch(updated, p.id)
    reload()
    announce()
  }, [config, patch, reload, announce])

  const create = useCallback(async (v: {
    name: string; description: string; instructions: string; icon: string
  }) => {
    const made = await createProject(config, v)
    setProjects(prev => [made, ...(prev ?? [])])
    setCreating(false)
    setOpenId(made.id)
    announce()
  }, [config, announce])

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem 1.75rem' }}>
      {open ? (
        <ProjectDetail
          config={config}
          project={open}
          accent={ACCENT}
          locale={locale}
          onBack={() => setOpenId(null)}
          onChanged={u => { patch(u, open.id); announce() }}
          onOpenChat={id => onOpenChat(id, open.id)}
          onNewChat={() => onNewChat(open.id)}
        />
      ) : (
        <div style={{ maxWidth: 1040, margin: '0 auto' }}>
          {/* Header */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1.25rem',
          }}>
            <h2 style={{
              fontFamily: "'SamsungOne','Inter',sans-serif",
              fontSize: '1.5rem', fontWeight: 600, color: 'var(--hb-text)',
              marginRight: 'auto',
            }}>
              {t.projects.title}
            </h2>
            <div className="hb-field hb-glass-sm" style={{
              display: 'flex', alignItems: 'center', gap: '0.4rem',
              padding: '0 0.6rem', height: 32, width: 210,
            }}>
              <span style={{ color: 'var(--hb-cyan)', display: 'flex' }}>
                <Glyph d={Ico.search} size={12} />
              </span>
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t.projects.searchPlaceholder}
                style={{
                  flex: 1, minWidth: 0,
                  background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--hb-text)', fontFamily: 'var(--font-mono)',
                  fontSize: '0.66rem', letterSpacing: '0.08em', userSelect: 'text',
                }}
              />
            </div>
            <Btn onClick={() => setShowArchived(v => !v)} tint={showArchived ? 'var(--hb-amber)' : undefined}>
              {t.projects.showArchived}
            </Btn>
            <Btn onClick={() => setCreating(v => !v)} tint="var(--hb-cyan-bright)">
              <Glyph d={Ico.plus} size={12} /> {t.projects.newProject}
            </Btn>
            <IconBtn title={t.projects.back} onClick={onClose}>
              <Glyph d={Ico.x} size={13} width={2.4} />
            </IconBtn>
          </div>

          {creating && <NewProjectForm onCancel={() => setCreating(false)} onCreate={create} />}

          {/* Grid */}
          {!projects ? (
            <SkeletonList rows={4} mark={false} markSize={28} />
          ) : filtered.length === 0 ? (
            <div style={{ padding: '3rem 0', textAlign: 'center' }}>
              <p style={{ ...label, marginBottom: '0.6rem' }}>
                {search ? t.projects.noResults : t.projects.empty}
              </p>
              {!search && (
                <p style={{ ...body, maxWidth: 420, margin: '0 auto', color: 'var(--hb-text-faint)' }}>
                  {t.projects.emptyBlurb}
                </p>
              )}
            </div>
          ) : (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              gap: '1rem',
            }}>
              {filtered.map(p => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  accent={ACCENT}
                  locale={locale}
                  onOpen={() => setOpenId(p.id)}
                  onPin={() => togglePin(p)}
                  onArchive={() => toggleArchive(p)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
