export function createAPI() {
  let csrf = ''
  let pending = null
  try { pending=JSON.parse(sessionStorage.getItem('solus_pending') || 'null') } catch { sessionStorage.removeItem('solus_pending') }
  const clearPending=()=>{pending=null;sessionStorage.removeItem('solus_pending')}
  async function request(path, options = {}) {
    const response = await fetch('/solus/api/v1' + path, {
      credentials: 'same-origin', ...options,
      headers: { 'Content-Type': 'application/json', 'X-Solus-CSRF': csrf, ...options.headers },
    })
    const data = await response.json()
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(e => `${e.loc.join('.')}: ${e.msg}`).join('; ') : data.detail
      const error = new Error(detail || `Request failed (${response.status})`)
      error.status = response.status
      throw error
    }
    return data
  }
  return {
    read: request,
    async resume() { const data = await request('/session'); csrf = data.csrf; return data },
    async login(password) { const data = await request('/login', { method: 'POST', body: JSON.stringify({password}) }); csrf = data.csrf },
    async setup(key, new_password, token) { return await request('/setup', {method:'POST', headers:key ? {'X-API-Key':key}:{},body:JSON.stringify({new_password,token:token || null})}) },
    async changePassword(password,new_password) { return await request('/password',{method:'POST',body:JSON.stringify({password,new_password})}) },
    async logout() { await request('/session', { method: 'DELETE' }); csrf = '' },
    hasPending:()=>!!pending,
    async recover() { if(!pending) return null;const receipt=await request('/receipts/'+pending.key);clearPending();return receipt },
    async resolve() { if(!pending) return null;const receipt=await request('/receipts/'+pending.key+'/resolve',{method:'POST'});clearPending();return receipt },
    async write(payload) {
      const encoded=new TextEncoder().encode(JSON.stringify(payload))
      const fingerprint=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoded))).map(b=>b.toString(16).padStart(2,'0')).join('')
      if(pending && pending.fingerprint!==fingerprint) throw new Error('An earlier command has an unresolved receipt. Check it before submitting another action, or retry the unchanged form.')
      if (!pending) {
        const key = crypto.randomUUID()
        pending = { fingerprint, key }
        // Only an opaque key and hash are retained. No amounts, accounts,
        // passwords or session credentials enter browser storage.
        sessionStorage.setItem('solus_pending',JSON.stringify(pending))
      }
      const body={...payload,command_key:pending.key}
      if(['post','investment_buy','investment_sell'].includes(payload.operation)) body.occurrence_key ||= pending.key
      try {
        const data = await request('/commands', { method: 'POST', body: JSON.stringify(body) })
        clearPending();return data
      } catch(e) { if(e.status && e.status<500) clearPending();throw e }
    },
  }
}
