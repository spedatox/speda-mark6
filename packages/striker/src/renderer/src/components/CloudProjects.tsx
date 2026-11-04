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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const retry = useRef({ fingerprint: '', id: '' })

  async function prepare() {
    const draft = { name: name.trim(), repository: repository.trim(), branch: branch.trim(), check, agent_id: config.agentId }
    const fingerprint = JSON.stringify(draft)
    if (retry.current.fingerprint !== fingerprint) retry.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true)
    setError('')
    try {
      const path = project ? `/${project.id}/cloud/environments` : '/cloud/environments'
      const environment = await projectRequest<Environment>(config, path, { ...draft, request_id: retry.current.id })
      onPrepared(environment.project_id)
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }

  return <form className="hb-glass" style={{ display: 'grid', gap: 8, padding: 14, marginBottom: 12 }}
    onSubmit={event => { event.preventDefault(); void prepare() }}>
    <strong>{project ? 'Prepare an environment revision' : 'New cloud project'}</strong>
    {!project && <label>Project name<input className="hb-field" required value={name} onChange={e => setName(e.target.value)} /></label>}
    <label>GitHub repository<input className="hb-field" required placeholder="owner/repository" value={repository} onChange={e => setRepository(e.target.value)} /></label>
    <label>Base branch<input className="hb-field" required value={branch} onChange={e => setBranch(e.target.value)} /></label>
    <label>Environment check<textarea className="hb-field" required rows={2} value={check} onChange={e => setCheck(e.target.value)} /></label>
    <p>The host prepares a checkout and runs the check in its configured Forge image with networking disabled.</p>
    {error && <p role="alert">{error}</p>}
    <div style={{ display: 'flex', gap: 8 }}>
      <button className="hb-btn" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      <button className="hb-btn" type="submit" disabled={busy || !name.trim() || !repository.trim() || !branch.trim() || !check.trim()}>Prepare environment</button>
    </div>
  </form>
}

export function CloudProjectPanel({ config, project, refresh, onPublished }: {
  config: AppConfig
  project: Project
  refresh: number
  onPublished: (environmentId: string | null) => void
}) {
  const [state, setState] = useState<CloudState | null>(null)
  const [revision, setRevision] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const live = state?.environments.some(item => item.status === 'preparing')
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
        onPublished(active?.id ?? null)
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

  async function reconcile(operationId: string) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await projectRequest(config, `/${project.id}/cloud/pull-requests/${operationId}/reconcile`, {})
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
      {environment.commit && <p>Commit <code>{environment.commit.slice(0, 12)}</code></p>}
      {environment.image && <p>Toolchain <code>{environment.image.slice(0, 19)}</code></p>}
      {environment.check_result && <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto' }}>{environment.check_result.output}</pre>}
      {environment.error && <p role="alert">{environment.error}</p>}
      {environment.status === 'ready' && <button className="hb-btn" disabled={busy} onClick={() => void publish(environment)}>Publish environment</button>}
    </details>)}
    {creating
      ? <CloudProjectForm config={config} project={project} previous={current} onCancel={() => setCreating(false)}
          onPrepared={() => { setCreating(false); setRevision(value => value + 1) }} />
      : <button className="hb-btn" onClick={() => setCreating(true)}>Prepare new revision</button>}
    {state?.pull_requests.map(pr => <p key={pr.id}>
      {pr.url ? <a href={pr.url} target="_blank" rel="noopener noreferrer">{pr.title}</a> : `${pr.title} · ${pr.status}`}
      {pr.error && <span role="alert"> {pr.error}</span>}
      {pr.status === 'reconciling' && <button className="hb-btn" disabled={busy} onClick={() => void reconcile(pr.id)}>Check GitHub result</button>}
    </p>)}
  </section>
}
