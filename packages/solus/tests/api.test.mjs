import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createAPI} from '../src/api.js'

test('network timeout and browser refresh preserve opaque retry identity without financial data',async()=>{
  const storage=new Map()
  globalThis.sessionStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}
  const bodies=[]
  globalThis.fetch=async(url,options)=>{
    bodies.push(JSON.parse(options.body))
    if(bodies.length===1) throw new Error('Connection lost after server commit')
    return {ok:true,json:async()=>({status:'success',command_key:bodies[0].command_key})}
  }
  const payload={operation:'post',description:'Sensitive description',amount:'1234.56'}
  const first=createAPI()
  await assert.rejects(first.write(payload),/Connection lost/)
  const stored=storage.get('solus_pending')
  assert.ok(!stored.includes('Sensitive')&&!stored.includes('1234'))
  const afterRefresh=createAPI()
  await assert.rejects(afterRefresh.write({...payload,amount:'555.00'}),/unresolved receipt/)
  await afterRefresh.write(payload)
  assert.deepEqual(bodies[1],bodies[0])
  assert.equal(storage.size,0)
})

test('lost form can be resolved without losing a committed receipt or retaining its retry key',async()=>{
  const storage=new Map()
  globalThis.sessionStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}
  globalThis.fetch=async()=>{throw new Error('Offline')}
  await assert.rejects(createAPI().write({operation:'book_create',name:'Private name'}),/Offline/)
  const pending=JSON.parse(storage.get('solus_pending'))
  const refreshed=createAPI()
  globalThis.fetch=async(url,options)=>{
    assert.ok(url.endsWith('/receipts/'+pending.key+'/resolve'))
    assert.equal(options.method,'POST')
    return {ok:true,json:async()=>({status:'cancelled',command_key:pending.key})}
  }
  assert.equal((await refreshed.resolve()).status,'cancelled')
  assert.equal(storage.size,0)
})
