import { useEffect, useRef, useState } from 'react'
import type { AppConfig, Project } from '../lib/types'
import { projectRequest } from '../lib/projectWorkspace'

interface Environment {
  id: string
  project_id: number
  status: string
  repository: string
  branch: string
  check: string
  mode?: 'prototype' | 'fork'
  phase?: string
  target?: { url: string; upstream?: string }
  setup_pending?: boolean
  initial_session_id?: number
  task_error?: string
  share_error?: string
  setup_error?: string
  commit?: string
  image?: string
  review_hash?: string
  published_at?: string
  error?: string
  check_result?: { exit_code: number; output: string }
}

interface CloudState {
  environments: Environment[]
  pull_requests: { id: string; status: string; title: string; url?: string; error?: string }[]
  checkpoints?: { id: string; status: string; title: string; url?: string; commit?: string; error?: string }[]
}

const message = (error: unknown) => error instanceof Error ? error.message : String(error)

export function CloudProjectForm({ config, project, previous, onPrepared, onCancel }: {
  config: AppConfig
  project?: Project
  previous?: Environment
  onPrepared: (projectId: number) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(project?.name ?? '')
  const [repository, setRepository] = useState(previous?.repository ?? '')
  const [branch, setBranch] = useState(previous?.branch ?? 'main')
  const [check, setCheck] = useState(previous?.check ?? 'git --version')
  const [mode, setMode] = useState<'existing' | 'prototype' | 'fork'>('existing')
  const [task, setTask] = useState('')
  const [allowPublic, setAllowPublic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const retry = useRef({ fingerprint: '', id: '' })

  async function prepare() {
    const common = { name: name.trim(), agent_id: config.agentId }
    const draft = mode === 'existing'
      ? { ...common, repository: repository.trim(), branch: branch.trim(), check }
      : { ...common, mode, task: task.trim(), ...(mode === 'fork' ? { repository: repository.trim(), allow_public: allowPublic } : {}) }
    const fingerprint = JSON.stringify(draft)
    if (retry.current.fingerprint !== fingerprint) retry.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true)
    setError('')
    try {
      const path = mode !== 'existing' ? '/cloud/github-workspaces'
        : project ? `/${project.id}/cloud/environments` : '/cloud/environments'
      const environment = await projectRequest<Environment>(config, path, { ...draft, request_id: retry.current.id })
      onPrepared(environment.project_id)
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }

  return <form className="hb-glass" style={{ display: 'grid', gap: 8, padding: 14, marginBottom: 12 }}
    onSubmit={event => { event.preventDefault(); void prepare() }}>
    <strong>{project ? 'Prepare an environment revision' : 'New cloud project'}</strong>
    {!project && config.agentId === 'optimus' && <label>GitHub workspace
      <select className="hb-field" value={mode} onChange={e => setMode(e.target.value as typeof mode)}>
        <option value="existing">Use an existing repository</option>
        <option value="prototype">Create a private prototype</option>
        <option value="fork">Fork into Optimus’s account</option>
      </select>
    </label>}
    {!project && <label>Project name<input className="hb-field" required value={name} onChange={e => setName(e.target.value)} /></label>}
    {mode !== 'prototype' && <label>GitHub repository<input className="hb-field" required placeholder="owner/repository" value={repository} onChange={e => setRepository(e.target.value)} /></label>}
    {mode === 'existing' && <>
      <label>Base branch<input className="hb-field" required value={branch} onChange={e => setBranch(e.target.value)} /></label>
      <label>Environment check<textarea className="hb-field" required rows={2} value={check} onChange={e => setCheck(e.target.value)} /></label>
    </>}
    {mode === 'fork' && <label><input type="checkbox" checked={allowPublic} onChange={e => setAllowPublic(e.target.checked)} /> Allow public checkpoints if the upstream repo is public</label>}
    {mode !== 'existing' && <label>First task<textarea className="hb-field" rows={3} value={task} onChange={e => setTask(e.target.value)} /></label>}
    <p>The host prepares a checkout and runs the check in its configured Forge image with networking disabled.</p>
    {error && <p role="alert">{error}</p>}
    <div style={{ display: 'flex', gap: 8 }}>
      <button className="hb-btn" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      <button className="hb-btn" type="submit" disabled={busy || !name.trim() || (mode !== 'prototype' && !repository.trim())
        || (mode === 'existing' && (!branch.trim() || !check.trim()))}>{mode === 'existing' ? 'Prepare environment' : 'Start project'}</button>
    </div>
  </form>
}

export function CloudProjectPanel({ config, project, refresh, onPublished }: {
  config: AppConfig
  project: Project
  refresh: number
  onPublished: (environmentId: string | null, checkpoint?: string) => void
}) {
  const [state, setState] = useState<CloudState | null>(null)
  const [revision, setRevision] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const live = state?.environments.some(item => item.mode || item.status === 'preparing')
    || state?.pull_requests.some(item => item.status === 'executing')

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const result = await projectRequest<CloudState>(config, `/${project.id}/cloud`)
        if (!alive) return
        setState(result)
        setError('')
        const active = result.environments.filter(item => item.status === 'published')
          .sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))[0]
        const latest = result.checkpoints?.[0]
        onPublished(active?.id ?? null, latest && `${latest.id}:${latest.status}`)
      } catch (err) { if (alive) setError(message(err)) }
    }
    void load()
    const timer = live ? setInterval(load, 2000) : undefined
    return () => { alive = false; clearInterval(timer) }
  }, [config.apiBase, config.apiKey, config.agentId, project.id, refresh, revision, live])

  async function publish(environment: Environment) {
    if (!environment.review_hash || busy) return
    setBusy(true)
    setError('')
    try {
      await projectRequest(config, `/${project.id}/cloud/publish`, {
        environment_id: environment.id, review_hash: environment.review_hash,
      })
      setRevision(value => value + 1)
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }

  async function reconcile(operationId: string, kind = 'pull-requests') {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await projectRequest(config, `/${project.id}/cloud/${kind}/${operationId}/reconcile`, {})
      setRevision(value => value + 1)
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }

  const current = state?.environments[0]
  return <section className="hb-glass" style={{ padding: 14, marginBottom: 12 }} aria-label="Cloud environment">
    <strong>Cloud environment</strong>
    {error && <p role="alert">{error}</p>}
    {!state && !error && <p>Loading environments…</p>}
    {state?.environments.map(environment => <details key={environment.id} open={environment.id === current?.id || undefined}>
      <summary>{environment.repository} · {environment.branch} · {environment.status}</summary>
      {environment.target?.url.startsWith('https://github.com/') && <p>
        <a href={environment.target.url} target="_blank" rel="noopener noreferrer">{environment.mode === 'fork' ? 'Open GitHub fork' : 'Open GitHub repository'}</a>
      </p>}
      {environment.setup_pending && <p>Finishing GitHub setup…</p>}
      {environment.initial_session_id && <p>The first task is in this project’s chats.</p>}
      {(environment.task_error || environment.share_error || environment.setup_error) &&
        <p role="alert">{[environment.task_error, environment.share_error, environment.setup_error].filter(Boolean).join(' ')}</p>}
      {environment.commit && <p>Commit <code>{environment.commit.slice(0, 12)}</code></p>}
      {environment.image && <p>Toolchain <code>{environment.image.slice(0, 19)}</code></p>}
      {environment.check_result && <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto' }}>{environment.check_result.output}</pre>}
      {environment.error && <p role="alert">{environment.error}</p>}
      {environment.status === 'ready' && <button className="hb-btn" disabled={busy} onClick={() => void publish(environment)}>Publish environment</button>}
      {environment.status === 'reconciling' && environment.phase === 'repository_requested' &&
        <button className="hb-btn" disabled={busy} onClick={() => void reconcile(environment.id, 'environments')}>Check GitHub repository</button>}
    </details>)}
    {creating
      ? <CloudProjectForm config={config} project={project} previous={current} onCancel={() => setCreating(false)}
          onPrepared={() => { setCreating(false); setRevision(value => value + 1) }} />
      : !current?.mode && <button className="hb-btn" onClick={() => setCreating(true)}>Prepare new revision</button>}
    {!!state?.checkpoints?.length && <p><strong>GitHub checkpoints</strong></p>}
    {state?.checkpoints?.map(checkpoint => <p key={checkpoint.id}>
      {checkpoint.url?.startsWith('https://github.com/')
        ? <a href={checkpoint.url} target="_blank" rel="noopener noreferrer">Checkpoint {checkpoint.commit?.slice(0, 8)}</a>
        : `${checkpoint.title} · ${checkpoint.status}`}
      {checkpoint.error && <span role="alert"> {checkpoint.error}</span>}
      {['reconciling', 'failed'].includes(checkpoint.status) &&
        <button className="hb-btn" disabled={busy} onClick={() => void reconcile(checkpoint.id, 'checkpoints')}>
          {checkpoint.commit ? 'Check GitHub result' : 'Retry checkpoint'}
        </button>}
    </p>)}
    {state?.pull_requests.map(pr => <p key={pr.id}>
      {pr.url ? <a href={pr.url} target="_blank" rel="noopener noreferrer">{pr.title}</a> : `${pr.title} · ${pr.status}`}
      {pr.error && <span role="alert"> {pr.error}</span>}
      {pr.status === 'reconciling' && <button className="hb-btn" disabled={busy} onClick={() => void reconcile(pr.id)}>Check GitHub result</button>}
    </p>)}
  </section>
}
