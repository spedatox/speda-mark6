import { useEffect, useRef, useState } from 'react'
import en from '../lib/i18n/en'
import tr from '../lib/i18n/tr'
import type { AppConfig, Project } from '../lib/types'
import { CloudProjectPanel } from './CloudProjects'
import { projectRequest, type FolderListing, type WorkspaceState, type ProjectChanges } from '../lib/projectWorkspace'

const box: React.CSSProperties = {padding:12, marginBottom:12, border:'1px solid var(--hb-edge)', borderRadius:8}
const code: React.CSSProperties = {whiteSpace:'pre-wrap', overflowWrap:'anywhere', maxHeight:360, overflow:'auto', fontSize:12, userSelect:'text'}
const message = (error: unknown) => error instanceof Error ? error.message : String(error)

export function ProjectFolderPicker({config, value, onChange, locale = 'en'}: {
  config: AppConfig; locale?: string; value: string; onChange: (path: string) => void
}) {
  const t = (locale === 'tr' ? tr : en).projects.workspace
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    setBusy(true); setError('')
    projectRequest<FolderListing>(config, `/folders?path=${encodeURIComponent(path)}`)
      .then(result => { if (alive) setListing(result) })
      .catch(err => { if (alive) { setError(message(err)); setListing(null) } })
      .finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [config.apiBase, config.apiKey, config.agentId, path])
  const create = async () => {
    if (!listing || !name.trim() || busy) return
    setBusy(true); setError('')
    try {
      const made = await projectRequest<{path:string}>(config, '/folders', {parent:listing.path, name:name.trim()})
      onChange(made.path); setPath(made.path); setName('')
    } catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  return <div style={box} aria-label="Project folder">
    <strong>{t.folder}</strong>
    <p>{t.folderHelp}</p>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">{t.loadingFolders}</p>}
    {listing && <>
      <p style={{overflowWrap:'anywhere'}}>{listing.host_id}: {listing.path}</p>
      <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
        {listing.parent && <button type="button" className="hb-btn" disabled={busy} onClick={() => setPath(listing.parent!)}>{t.up}</button>}
        <button type="button" className="hb-btn" disabled={busy || listing.path === listing.root}
          onClick={() => onChange(listing.path)}>{t.useFolder}</button>
      </div>
      <div style={{maxHeight:160, overflow:'auto', margin:'8px 0'}}>
        {listing.directories.map(folder => <button type="button" className="hb-btn" key={folder.path}
          disabled={busy} style={{display:'block', marginBottom:4}} onClick={() => setPath(folder.path)}>📁 {folder.name}</button>)}
        {listing.truncated && <p>{t.folderLimit}</p>}
      </div>
      <div style={{display:'flex', gap:8}}>
        <input aria-label="New folder name" className="hb-field" value={name} onChange={e => setName(e.target.value)} placeholder={t.newFolder} />
        <button type="button" className="hb-btn" disabled={busy || !name.trim()} onClick={create}>{t.createFolder}</button>
      </div>
    </>}
    {value && <p style={{overflowWrap:'anywhere'}}>{t.selected}: {value}</p>}
  </div>
}

export default function ProjectWorkspace({config, projectId, project, onCloudReady, locale = 'en'}: {config:AppConfig; projectId:number; project?:Project; onCloudReady?:(ready:boolean)=>void; locale?:string}) {
  const t = (locale === 'tr' ? tr : en).projects.workspace
  const [state, setState] = useState<WorkspaceState | null>(null)
  const [changes, setChanges] = useState<ProjectChanges | null>(null)
  const [preview, setPreview] = useState<{path:string; content:string; truncated:boolean} | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [epoch, setEpoch] = useState(0)
  const [cloudRefresh, setCloudRefresh] = useState(0)
  const [prTitle, setPrTitle] = useState('')
  const published = useRef<string | null>(null)
  useEffect(() => {
    let alive = true
    projectRequest<WorkspaceState>(config, `/${projectId}/workspace`)
      .then(result => { if (alive) { setState(result); setError('') } })
      .catch(err => { if (alive) setError(message(err)) })
    return () => { alive = false }
  }, [config.apiBase, config.apiKey, config.agentId, projectId, epoch])
  const review = async (taskId: string) => {
    setBusy(true); setError('')
    try {
      setChanges(await projectRequest<ProjectChanges>(config, `/${projectId}/changes/${taskId}`))
      setPrTitle(state?.tasks.find(task => task.id === taskId)?.task.slice(0, 200) ?? 'Optimus project update')
    }
    catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!changes || busy) return
    setBusy(true); setError('')
    try {
      await projectRequest(config, `/${projectId}/integrate`, {
        task_id:changes.task_id, expected_source_revision:changes.expected_source_revision,
        expected_artifact_revision:changes.expected_artifact_revision,
      })
      setChanges(null); setPreview(null); setEpoch(n => n+1)
    } catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  async function openPullRequest() {
    if (!changes?.environment || !prTitle.trim() || busy) return
    setBusy(true); setError('')
    try {
      await projectRequest(config, `/${projectId}/cloud/pull-requests`, {
        task_id: changes.task_id, title: prTitle.trim(),
        expected_source_revision: changes.expected_source_revision,
        expected_artifact_revision: changes.expected_artifact_revision,
      })
      setChanges(null); setCloudRefresh(value => value + 1)
    } catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  const rollback = async (operationId:string) => {
    if (!state || busy) return
    setBusy(true); setError('')
    try {
      await projectRequest(config, `/${projectId}/operations/${operationId}/rollback`, {expected_revision:state.source_revision})
      setChanges(null); setPreview(null); setEpoch(n => n+1)
    } catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  const read = async (path:string) => {
    setBusy(true); setError('')
    try { setPreview(await projectRequest(config, `/${projectId}/workspace/file?path=${encodeURIComponent(path)}`)) }
    catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  return <section style={box} aria-label="Project workspace">
    <div style={{display:'flex', gap:12, alignItems:'center'}}><strong>{t.changes}</strong>
      <button className="hb-btn" disabled={busy} onClick={() => setEpoch(n=>n+1)}>{t.refresh}</button></div>
    {error && <p role="alert">{error}</p>}
    {!state && !error && <p>{t.loading}</p>}
    {project?.cloud && <CloudProjectPanel config={config} project={project} refresh={cloudRefresh}
      onPublished={id => {
        onCloudReady?.(id !== null)
        if (id !== published.current) { published.current = id; setEpoch(n => n+1) }
      }} />}
    {state && <>
      <p style={{overflowWrap:'anywhere', userSelect:'text'}}>{state.environment ? `${state.environment.repository} · ${state.environment.branch} · ${state.environment.commit.slice(0, 12)}` : `${state.host_id}: ${state.cwd}`}</p>
      {!state.execution_enabled && <p>{t.disabled}</p>}
      {state.checkpoint && <div><strong>{state.checkpoint.objective}</strong><p>{state.checkpoint.progress}</p>
        {state.checkpoint.next_steps?.map((step,i)=><p key={i}>{t.next}: {step}</p>)}
        {state.checkpoint.blockers?.map((step,i)=><p key={i}>{t.blocked}: {step}</p>)}</div>}
      <details><summary>{t.files} ({state.files.length}{state.files_omitted ? '+' : ''})</summary>
        <div style={{maxHeight:200, overflow:'auto'}}>{state.files.map(path=><button key={path} className="hb-btn" disabled={busy}
          style={{display:'block', marginBottom:4}} onClick={()=>read(path)}>{path}</button>)}</div></details>
      {preview && <div><strong>{preview.path}</strong><pre style={code}>{preview.content}</pre>
        {preview.truncated && <p>{t.truncated}</p>}</div>}
      <details><summary>{t.git}</summary><pre style={code}>{state.git.available ? state.git.status || t.clean : t.noGit}</pre>
        {state.git.truncated && <p>{t.gitTruncated}</p>}</details>
      <h4>{t.work}</h4>
      {state.tasks.length === 0 && <p>{t.empty}</p>}
      {state.tasks.map(task => {
        const applied = state.operations.some(o=>o.kind==='integrate' && o.status==='complete' && o.details.task_id===task.id)
        return <div key={task.id} style={{marginBottom:8}}><span>{task.task} — {task.status}{applied ? ` · ${t.applied}` : ''}</span>{' '}
          {task.status==='succeeded' && <button className="hb-btn" disabled={busy} onClick={()=>review(task.id)}>{t.review}</button>}</div>
      })}
      {changes && <div style={box}>
        <strong>{changes.changes.length} {t.changedFiles}</strong>
        {changes.changes.map(c=><p key={c.path}>{c.change}: {c.path}</p>)}
        <pre style={code}>{changes.diff || t.noChanges}</pre>
        {changes.source_changed_since_execution && <p role="alert">{t.stale}</p>}
        {changes.environment ? <>
          <p>{changes.environment.repository} · {changes.environment.branch}</p>
          <label>Pull request title <input className="hb-field" value={prTitle} onChange={e=>setPrTitle(e.target.value)} /></label>
          <button className="hb-btn" disabled={busy || !changes.changes.length || !prTitle.trim()} onClick={() => void openPullRequest()}>Open draft pull request</button>
        </> : <>
          <button className="hb-btn" disabled={busy || changes.source_changed_since_execution || !changes.changes.length} onClick={apply}>{t.apply}</button>
          <p>{t.rollbackCopy}</p>
        </>}
      </div>}
      {state.operations.filter(o=>o.status==='complete' && o.details.after_revision===state.source_revision).slice(0,1).map(o=>
        <button key={o.id} className="hb-btn" disabled={busy} onClick={()=>rollback(o.id)}>{t.undo}</button>)}
    </>}
  </section>
}
