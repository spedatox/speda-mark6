import test from 'node:test'
import assert from 'node:assert/strict'
import React, { useReducer } from 'react'
import Renderer, { act } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { ChatContext, chatReducer, initialState, useChatContext } from '@client/store/chat'
import { useWorkerStreams } from '@client/lib/useWorkerStreams'
import { decodeWorkerFrames, readWorkerJson, WorkerTransportError } from '@client/lib/workerTransport'
import { mergeWorkerRun } from '@client/lib/workerState'
import { useLiveSteering, mayClearSteeringDraft } from '@client/lib/useLiveSteering'
import ProjectWorkspace, { ProjectFolderPicker } from '@client/components/ProjectWorkspace'
import { projectRequest } from '@client/lib/projectWorkspace'
import { useProjectNavigation } from '@client/lib/useProjectNavigation'
import { CloudProjectForm, CloudProjectPanel } from '@client/components/CloudProjects'
import { stepState } from '@client/lib/types'
import { TurnError } from '@client/components/TurnError'

const execution = 'a'.repeat(32), step = 'b'.repeat(32)
const config = { apiBase: 'https://worker.test', apiKey: 'fixture', agentId: 'optimus' }
const delay = (ms = 35) => new Promise(resolve => setTimeout(resolve, ms))
const worker = (sequence = 5, status = 'running') => ({ id: execution, executionId: execution,
  agent: 'autobot', label: 'Build app', running: status === 'running', status, steps: [], lastSequence: sequence,
  source: 'forge', seenEventIds: [] })
const message = (id = '11') => ({ id, role: 'assistant', content: 'Supervisor reply', tools: [],
  isStreaming: false, isError: false, coordinationStepId: step, sessionId: 1 })

test('checkpoint steps only show success after a tool result confirms it', () => {
  const tool = { id: 'checkpoint', name: 'project_checkpoint' }
  assert.equal(stepState(tool, true), 'running')
  assert.equal(stepState(tool, false), 'unconfirmed')
  assert.equal(stepState({ ...tool, result: 'Error: Mission completion blocked.' }, false), 'failed')
  assert.equal(stepState({ ...tool, result: '{"checkpoint":{"status":"active"}}' }, false), 'done')
})

test('turn error keeps the message visible and exposes retry only when available', () => {
  let retries = 0
  const error = 'The connection closed before the reply finished.'
  const view = Renderer.create(<TurnError error={error} retryLabel="Tekrar dene" onRetry={() => retries++} />)
  try {
    assert.equal(view.root.findByProps({ role: 'alert' }).children[1].props.children, error)
    const button = view.root.findByType('button')
    assert.equal(button.props.children, 'Tekrar dene')
    act(() => button.props.onClick())
    assert.equal(retries, 1)
    view.update(<TurnError error={error} />)
    assert.equal(view.root.findAllByType('button').length, 0)
  } finally {
    view.unmount()
  }
})

test('folder picker selects an existing host folder and creates a new folder explicitly', async t => {
  const saved=globalThis.fetch, selected:string[]=[], requests:any[]=[]
  globalThis.fetch=async (url:any, init:any) => {
    requests.push({url:String(url),init})
    if (init?.method==='POST') return Response.json({path:'/work/new-app'})
    const nested=String(url).includes('path=%2Fwork%2F')
    return Response.json({path:nested?'/work/existing':'/work', root:'/work', parent:nested?'/work':null,
      host_id:'test-host',directories:nested?[]:[{name:'existing',path:'/work/existing'}]})
  }
  let view:Renderer.ReactTestRenderer
  await act(async()=>{view=Renderer.create(<ProjectFolderPicker config={config} value="" onChange={p=>selected.push(p)} />);await delay()})
  t.after(async()=>{await act(async()=>view.unmount());globalThis.fetch=saved})
  const button=(text:string)=>view.root.findAllByType('button').find(b=>String(b.props.children).includes(text))!
  assert.equal(button('Use this folder').props.disabled,true)
  await act(async()=>{button('existing').props.onClick();await delay()})
  await act(async()=>{button('Use this folder').props.onClick()})
  assert.equal(selected[0],'/work/existing')
  await act(async()=>{view.root.findByType('input').props.onChange({target:{value:'new-app'}})})
  await act(async()=>{button('Create folder').props.onClick();await delay()})
  const posted=requests.find(r=>r.init?.method==='POST')
  assert.deepEqual(JSON.parse(posted.init.body),{parent:'/work/existing',name:'new-app'})
  assert.ok(requests.every(r=>r.init.headers['X-API-Key']==='fixture'))
  assert.equal(selected.at(-1),'/work/new-app')
})

test('project artifact review uses exact revisions and refuses stale source application', async t=>{
  const saved=globalThis.fetch, writes:any[]=[]
  let stale=true
  const state={cwd:'/work/existing',host_id:'test',execution_enabled:true,source_revision:'source',files:['app.ts'],files_omitted:0,
    checkpoint:{objective:'Continue the existing app'},git:{available:true,status:' M app.ts'},claims:[],operations:[],
    tasks:[{id:execution,task:'Implement feature',status:'succeeded'}]}
  globalThis.fetch=async(url:any,init:any)=>{
    if (init.method==='POST') {writes.push(JSON.parse(init.body));return Response.json({status:'complete'})}
    if (String(url).includes('/changes/')) return Response.json({task_id:execution, expected_source_revision:'source',
      expected_artifact_revision:'artifact',source_changed_since_execution:stale,diff:'-old\n+new',changes:[{path:'app.ts',change:'modify'}]})
    return Response.json(state)
  }
  let view:Renderer.ReactTestRenderer
  await act(async()=>{view=Renderer.create(<ProjectWorkspace config={config} projectId={9}/>);await delay()})
  t.after(async()=>{await act(async()=>view.unmount());globalThis.fetch=saved})
  const button=(text:string)=>view.root.findAllByType('button').find(b=>String(b.props.children).includes(text))!
  await act(async()=>{button('Review changes').props.onClick();await delay()})
  assert.equal(button('Apply changes').props.disabled,true)
  assert.equal(writes.length,0)
  stale=false
  await act(async()=>{button('Review changes').props.onClick();await delay()})
  assert.equal(button('Apply changes').props.disabled,false)
  await act(async()=>{button('Apply changes').props.onClick();await delay()})
  assert.deepEqual(writes,[{task_id:execution,expected_source_revision:'source',expected_artifact_revision:'artifact'}])
})

test('project permission and configuration failures remain visible', async()=>{
  const saved=globalThis.fetch
  try {
    globalThis.fetch=async()=>Response.json({detail:'Project folder is not configured'},{status:503})
    await assert.rejects(projectRequest(config,'/folders'),/not configured/)
  } finally {globalThis.fetch=saved}
})

test('cloud environment publication is explicit and bound to the reviewed revision', async t => {
  const saved = globalThis.fetch
  const posted: unknown[] = []
  const review = { id: 'e'.repeat(32), review_hash: 'c'.repeat(64), repository: 'example/repository',
    branch: 'main', status: 'ready', commit: 'd'.repeat(40), check: 'git --version', project_id: 42 }
  globalThis.fetch = async (_url, init) => {
    if (init?.method === 'POST') {
      posted.push(JSON.parse(String(init.body)))
      review.status = 'published'
      return Response.json(review)
    }
    return Response.json({ environments: [review], pull_requests: [] })
  }
  const selections: (string|null)[] = []
  let view: Renderer.ReactTestRenderer
  await act(async () => {
    view = Renderer.create(<CloudProjectPanel config={config} project={{ id:42 } as any} refresh={0}
      onPublished={id => selections.push(id)} />)
    await delay()
  })
  t.after(async () => { await act(async () => view.unmount()); globalThis.fetch = saved })
  assert.equal(posted.length, 0)
  assert.equal(selections.at(-1), null)
  await act(async () => {
    const button = view.root.findAllByType('button').find(node => node.props.children === 'Publish environment')!
    button.props.onClick()
    await delay()
  })
  assert.deepEqual(posted, [{ environment_id: review.id, review_hash: review.review_hash }])
  assert.equal(selections.at(-1), review.id)
})

test('an Optimus prototype form provisions a private repo and sends the first task', async t => {
  const saved = globalThis.fetch
  const posted: { url: string; body: Record<string, unknown> }[] = []
  globalThis.fetch = async (url, init) => {
    posted.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return Response.json({ project_id: 42 })
  }
  const selected: number[] = []
  let view: Renderer.ReactTestRenderer
  await act(async () => {
    view = Renderer.create(<CloudProjectForm config={config} onCancel={() => {}} onPrepared={id => selected.push(id)} />)
  })
  t.after(async () => { await act(async () => view.unmount()); globalThis.fetch = saved })
  await act(async () => {
    view.root.findByType('select').props.onChange({ target: { value: 'prototype' } })
  })
  await act(async () => {
    view.root.findByType('input').props.onChange({ target: { value: 'Log Viewer' } })
    view.root.findByType('textarea').props.onChange({ target: { value: 'Build a log viewer' } })
  })
  await act(async () => {
    view.root.findByType('form').props.onSubmit({ preventDefault() {} })
    await delay()
  })
  assert.equal(posted.length, 1)
  const url = new URL(posted[0].url)
  assert.equal(url.pathname, '/projects/cloud/github-workspaces')
  assert.equal(url.searchParams.get('agent_id'), 'optimus')
  const { request_id, ...body } = posted[0].body
  assert.equal(typeof request_id, 'string')
  assert.deepEqual(body, { name: 'Log Viewer', agent_id: 'optimus', mode: 'prototype', task: 'Build a log viewer' })
  assert.deepEqual(selected, [42])
})

test('reopening the app restores the authorized project chat and retains its binding', async t=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,'localStorage')
  const key=`speda-project-navigation:${config.apiBase}:${config.agentId}`
  const storage=new Map([[key,JSON.stringify({sessionId:2,projectId:9})]])
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:(k:string)=>storage.get(k)??null,setItem:(k:string,v:string)=>storage.set(k,v)}})
  t.after(()=>{if(original)Object.defineProperty(globalThis,'localStorage',original);else delete (globalThis as any).localStorage})
  function useNavigation(cfg:any){
    const {dispatch}=useChatContext()
    useProjectNavigation(cfg,(sessionId,projectId)=>dispatch({type:'SELECT_SESSION',payload:{sessionId,projectId,messages:[]}}))
  }
  const h=await harness(t,{config,sessionsLoaded:true,sessions:[{id:2,project_id:9}],activeSessionId:null,messages:[]},undefined,useNavigation)
  assert.equal(h.state().activeSessionId,2)
  assert.equal(h.state().activeProjectId,9)
  assert.deepEqual(JSON.parse(storage.get(key)!),{sessionId:2,projectId:9})
  assert.ok(!storage.get(key)!.includes(config.apiKey))
})
const snapshot = (sessionId = 1, sequence = 5, cards = [{ parent_step_id: step, parent_message_id: '11', run: worker(sequence) }]) =>
  Response.json({ session_id: sessionId, sequence, cards, omitted: 0 })
const event = (sequence: number, data = {}) => ({ id: execution, execution_id: execution, parent_step_id: step,
  agent: 'autobot', phase: 'text', text: 'İstanbul', sequence, event_id: `room:${sequence}`, ...data })
const frame = (data: unknown) => `data: ${JSON.stringify({ type: 'subagent', data })}\n\n`

test('updated math renderer preserves formulas and refuses untrusted HTML commands', () => {
  const render = (content: string) => renderToStaticMarkup(React.createElement(ReactMarkdown,
    { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex], children: content }))
  assert.match(render('$x^2 + y^2$'), /class="katex"/)
  const untrusted = render('$\\href{javascript:alert(1)}{click}$')
  assert.doesNotMatch(untrusted, /href="javascript:/)
  assert.doesNotMatch(render('$\\htmlClass{evil}{text}$'), /class="evil"/)
})

function streaming(signal?: AbortSignal) {
  let controller: ReadableStreamDefaultController<Uint8Array>, cancelled = false
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value }, cancel() { cancelled = true } })
  signal?.addEventListener('abort', () => { if (!cancelled) { cancelled = true; controller.close() } }, { once: true })
  return { response: new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }),
    emit(text: string) { if (!cancelled) controller.enqueue(new TextEncoder().encode(text)) },
    close() { if (!cancelled) { cancelled = true; controller.close() } }, cancelled: () => cancelled }
}

async function harness(t: any, initial: any = {}, fetcher?: typeof fetch, hook = useWorkerStreams) {
  const saved = globalThis.fetch
  if (fetcher) globalThis.fetch = fetcher
  let state: any, dispatch: any, feed: any, renders = 0
  function Probe() { feed = hook(config); renders++; return null }
  function Root() {
    const pair = useReducer(chatReducer, { ...initialState, activeSessionId: 1, messages: [message()], ...initial })
    state = pair[0]; dispatch = pair[1]
    return React.createElement(ChatContext.Provider, { value: { state, dispatch } }, React.createElement(Probe))
  }
  let view: Renderer.ReactTestRenderer
  await act(async () => { view = Renderer.create(React.createElement(Root)); await delay() })
  t.after(async () => { await act(async () => view.unmount()); globalThis.fetch = saved })
  return { state: () => state, feed: () => feed, renders: () => renders,
    async dispatch(action: any) { await act(async () => { dispatch(action); await delay() }) } }
}

test('SSE preserves fragmented UTF-8, CRLF, multiline data and final frames', async () => {
  const source = streaming()
  const bytes = new TextEncoder().encode(`: keepalive\r\n\r\ndata: {"type":"subagent",\r\ndata: "data":{"text":"İstanbul"}}\r\n\r\n${frame(event(7)).trimEnd()}`)
  const body = new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() } })
  const result = []
  for await (const item of decodeWorkerFrames(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }), new AbortController().signal)) result.push(item)
  assert.equal(result.length, 2)
  assert.equal(result[0].text, 'İstanbul')
  assert.equal(result[1].sequence, 7)
  source.close()
})

test('invalid frames and oversized bodies fail explicitly and cancel their readers', async () => {
  for (const text of ['data: broken\n\n', 'data: '+ 'x'.repeat(270000)]) {
    const source = streaming()
    source.emit(text)
    await assert.rejects(async () => { for await (const _ of decodeWorkerFrames(source.response, new AbortController().signal)) {} }, WorkerTransportError)
    assert.equal(source.cancelled(), true)
  }
  const huge = new Response(new Uint8Array(1500001))
  await assert.rejects(readWorkerJson(huge, new AbortController().signal), /size limit/)
})

test('snapshot identity rejects a different chat before applying any worker state', async t => {
  const app = await harness(t, {}, async () => snapshot(2))
  assert.equal(app.feed().status, 'unavailable')
  assert.equal(app.state().messages[0].subagents, undefined)
})

test('worker completion leaves an active public reply and composer available', async t => {
  let stream: ReturnType<typeof streaming>
  const app = await harness(t, { isStreaming: true }, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot()
    stream = streaming(init?.signal as AbortSignal); return stream.response
  })
  await act(async () => { stream.emit(frame(event(6, { phase:'finished', status:'succeeded', result:'Verified artifact', ok:true }))); await delay() })
  assert.equal(app.state().isStreaming, true)
  assert.equal(app.state().messages[0].subagents[0].running, false)
  assert.equal(app.state().messages[0].subagents[0].report, 'Verified artifact')
})

test('events received before the parent history loads are retained and attached by stable identity', async t => {
  let stream: ReturnType<typeof streaming>
  const other = { ...message('22'), coordinationStepId: 'c'.repeat(32) }
  const app = await harness(t, { messages: [other] }, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot()
    stream = streaming(init?.signal as AbortSignal); return stream.response
  })
  await act(async () => { stream.emit(frame(event(6))); await delay() })
  await app.dispatch({ type:'SELECT_SESSION', payload:{sessionId:1,messages:[other,message()]} })
  assert.equal(app.state().messages.find((m: any) => m.id === '11').subagents[0].steps[0].text, 'İstanbul')
})

test('an older history response cannot erase newer worker events', async t => {
  let stream: ReturnType<typeof streaming>
  const app = await harness(t, {}, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot()
    stream = streaming(init?.signal as AbortSignal); return stream.response
  })
  await act(async () => { stream.emit(frame(event(6))); await delay() })
  await app.dispatch({type:'SELECT_SESSION',payload:{sessionId:1,messages:[{...message(),subagents:[worker(5)]}]}})
  assert.equal(app.state().messages[0].subagents[0].lastSequence, 6)
  assert.equal(app.state().messages[0].subagents[0].steps[0].text, 'İstanbul')
})

test('late snapshot responses cannot mutate the newly selected chat', async t => {
  let finish: (response: Response) => void
  const held = new Promise<Response>(resolve => { finish = resolve })
  const urls: string[] = []
  const app = await harness(t, {}, async (url, init) => {
    urls.push(String(url))
    if (String(url).includes('/sessions/1/snapshot')) return held // Deliberately ignores abort.
    if (String(url).endsWith('/snapshot')) return snapshot(2, 0, [])
    return streaming(init?.signal as AbortSignal).response
  })
  await app.dispatch({type:'SELECT_SESSION',payload:{sessionId:2,messages:[{...message('99'),sessionId:2}]}})
  await act(async () => { finish(snapshot()); await delay() })
  assert.equal(app.state().activeSessionId, 2)
  assert.equal(app.state().messages[0].subagents, undefined)
  assert.equal(urls.some(url => url.includes('/sessions/1/attach')), false)
})

test('replayed and out-of-order frames do not duplicate text or mismatch tool results', async t => {
  let stream: ReturnType<typeof streaming>
  const app = await harness(t, {}, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot()
    stream = streaming(init?.signal as AbortSignal); return stream.response
  })
  await act(async () => {
    for (const data of [event(6),event(6),event(5),event(7,{phase:'tool',tool:'run_command',tool_call_id:'check-1'}),
      event(8,{phase:'tool_result',tool_call_id:'check-1',result:'tests passed'})]) stream.emit(frame(data))
    await delay()
  })
  const steps = app.state().messages[0].subagents[0].steps
  assert.equal(steps[0].text, 'İstanbul')
  assert.equal(steps[1].toolCallId, 'check-1')
  assert.equal(steps[1].result, 'tests passed')
})

test('disconnect recovery reads canonical state without sending another task', async t => {
  let streams = 0, snapshots = 0
  const methods: string[] = []
  const app = await harness(t, {}, async (url, init) => {
    methods.push(init?.method || 'GET')
    if (String(url).endsWith('/snapshot')) {
      snapshots++
      return snapshot(1, snapshots > 1 ? 8 : 5, [{parent_step_id:step,parent_message_id:'11',run:worker(snapshots > 1 ? 8 : 5, snapshots > 1 ? 'succeeded':'running')}])
    }
    const result = streaming(init?.signal as AbortSignal)
    if (++streams === 1) result.close()
    return result.response
  })
  await act(async () => { await delay(1150) })
  assert.ok(snapshots >= 2)
  assert.equal(app.state().messages[0].subagents[0].status, 'succeeded')
  assert.equal(methods.every(method => method === 'GET'), true)
})

test('permission rejection is visible and never enters an automatic retry loop', async t => {
  let calls = 0
  const app = await harness(t, {}, async () => { calls++; return new Response('', {status:401}) })
  await act(async () => { await delay(80) })
  assert.equal(app.feed().status, 'unavailable')
  assert.match(app.feed().error, /401/)
  assert.equal(calls, 1)
})

test('a stopped parent can retain an operational worker card without inventing an answer', async t => {
  const app = await harness(t, {messages:[]}, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot(1,5,[{parent_step_id:step,parent_message_id:null,run:worker()}])
    return streaming(init?.signal as AbortSignal).response
  })
  assert.equal(app.state().messages[0].isWorkerAnchor, true)
  assert.equal(app.state().messages[0].isStreaming, false)
  await app.dispatch({type:'ADD_ASSISTANT_MESSAGE',payload:{...message('real'),coordinationStepId:undefined,isStreaming:true}})
  await app.dispatch({type:'TAG_MESSAGE_SESSION',payload:{id:'real',sessionId:1,stepId:step}})
  assert.equal(app.state().messages.filter((m: any) => m.coordinationStepId === step).length, 1)
  assert.equal(app.state().messages.find((m: any) => m.id === 'real').subagents[0].executionId, execution)
})

test('snapshot overlap retains a more complete preview at the same sequence', () => {
  const prior = {...worker(8),steps:[{kind:'text',text:'full recorded text'}]}
  assert.equal(mergeWorkerRun(prior,{...worker(8),activityPartial:true}).steps[0].text,'full recorded text')
  assert.equal(mergeWorkerRun(prior,worker(7)).lastSequence,8)
})

test('bursts are coalesced and previews remain bounded', async t => {
  let stream: ReturnType<typeof streaming>
  const app = await harness(t, {}, async (url, init) => {
    if (String(url).endsWith('/snapshot')) return snapshot()
    stream = streaming(init?.signal as AbortSignal); return stream.response
  })
  const before = app.renders()
  await act(async () => {
    stream.emit(Array.from({length:1000},(_,index)=>frame(event(index+6,{text:'x'.repeat(128)}))).join(''))
    await delay(150)
  })
  const run = app.state().messages[0].subagents[0]
  assert.equal(run.lastSequence,1005)
  assert.ok(app.renders()-before < 25)
  assert.ok(JSON.stringify(run).length <= 65000)
  assert.equal(run.activityPartial,true)
})

test('late steering acknowledgment cannot echo into another chat', async t => {
  let finish: (response: Response) => void
  const held = new Promise<Response>(resolve => { finish = resolve })
  const app = await harness(t, {}, async () => held,
    cfg => useLiveSteering(cfg, () => ({requestId:'request-1',sessionId:1,stepId:step})))
  const accepted = app.feed()('Keep the current requirement')
  await app.dispatch({type:'SELECT_SESSION',payload:{sessionId:2,messages:[{...message('99'),sessionId:2}]}})
  await act(async () => { finish(Response.json({steered:true})); assert.equal(await accepted,true) })
  assert.equal(app.state().messages.some((m: any) => m.role === 'user'),false)
})

test('concurrent steering submits once, with expected turn identity and one acknowledgment', async t => {
  let finish: (response: Response) => void, calls = 0, body: any
  const held = new Promise<Response>(resolve => { finish = resolve })
  const app = await harness(t, {}, async (_url,init) => { calls++; body=JSON.parse(String(init?.body)); return held },
    cfg => useLiveSteering(cfg, () => ({requestId:'request-1',sessionId:1,stepId:step})))
  const first = app.feed()('Revise the plan'), second = app.feed()('Revise the plan')
  await act(async () => { finish(Response.json({steered:true})); await Promise.all([first,second]) })
  assert.equal(calls,1)
  assert.equal(body.expected_step_id,step)
  assert.equal(app.state().messages.filter((m: any) => m.role === 'user').length,1)
})

test('lost steering acknowledgments retry the same input ID without silently retargeting', async t => {
  const bodies: any[] = []
  const app = await harness(t, {}, async (_url,init) => {
    bodies.push(JSON.parse(String(init?.body)))
    if(bodies.length===1) throw new TypeError('Connection lost after send')
    return Response.json({steered:true})
  }, cfg => useLiveSteering(cfg, () => ({requestId:'request-1',sessionId:1,stepId:step})))
  assert.equal(await app.feed()('Keep this change'),false)
  await act(async () => { assert.equal(await app.feed()('Keep this change'),true) })
  assert.equal(bodies[0].message_id,bodies[1].message_id)
  assert.equal(bodies[1].expected_step_id,step)
})

test('a new draft or chat is preserved when an older steering request succeeds', () => {
  const submitted = {revision:1,sessionId:1,agentId:'optimus'}
  assert.equal(mayClearSteeringDraft(submitted,{...submitted}),true)
  assert.equal(mayClearSteeringDraft(submitted,{...submitted,revision:2}),false)
  assert.equal(mayClearSteeringDraft(submitted,{...submitted,sessionId:2}),false)
  assert.equal(mayClearSteeringDraft(submitted,{...submitted,agentId:'scourge'}),false)
})
