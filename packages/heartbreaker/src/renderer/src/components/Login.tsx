import { useState, type FormEvent } from 'react'
import { login as apiLogin } from '../lib/api'

interface Props {
  apiBase: string
  title?: string
  /** Called with the session token and the (possibly edited) server URL. */
  onSuccess: (token: string, expiresAt: number, server: string) => void
}

const mono = "'Share Tech Mono', monospace"

const inputStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  padding: '0.6rem 0.7rem',
  background: 'rgba(10, 16, 22, 0.55)',
  border: '1px solid var(--hb-edge)',
  borderRadius: 8,
  color: 'var(--text-primary, #dfeaf2)',
  fontFamily: mono,
  fontSize: '0.82rem',
  letterSpacing: '0.04em',
  outline: 'none',
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontFamily: mono,
  fontSize: '0.6rem',
  letterSpacing: '0.18em',
  textTransform: 'uppercase',
  color: 'var(--hb-cyan)',
  marginBottom: '0.35rem',
}

export default function Login({ apiBase, title = 'S.P.E.D.A.', onSuccess }: Props) {
  const [server, setServer] = useState(apiBase)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    const base = server.trim().replace(/\/+$/, '')
    const res = await apiLogin(base, username, password)
    setBusy(false)
    if (res.token && res.expires_at) {
      onSuccess(res.token, res.expires_at, base)
    } else {
      setError(res.error || 'Login failed')
      setPassword('')
    }
  }

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-primary)',
      }}
    >
      <form
        className="hb-holo"
        onSubmit={submit}
        style={{
          width: 340,
          padding: '1.8rem 1.6rem',
          borderRadius: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        }}
      >
        <div style={{ textAlign: 'center' }}>
          <div
            style={{
              fontFamily: mono,
              fontSize: '1.05rem',
              letterSpacing: '0.32em',
              color: 'var(--hb-cyan-bright)',
              marginBottom: '0.3rem',
            }}
          >
            {title}
          </div>
          <div
            style={{
              fontFamily: mono,
              fontSize: '0.58rem',
              letterSpacing: '0.24em',
              textTransform: 'uppercase',
              color: 'var(--hb-cyan-dim)',
            }}
          >
            Authenticate to continue
          </div>
        </div>

        <div>
          <label style={labelStyle} htmlFor="hb-server">Server</label>
          <input
            id="hb-server"
            style={inputStyle}
            value={server}
            onChange={(e) => setServer(e.target.value)}
            autoComplete="url"
            spellCheck={false}
          />
        </div>

        <div>
          <label style={labelStyle} htmlFor="hb-user">Username</label>
          <input
            id="hb-user"
            style={inputStyle}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            spellCheck={false}
            autoFocus
          />
        </div>

        <div>
          <label style={labelStyle} htmlFor="hb-pass">Password</label>
          <input
            id="hb-pass"
            type="password"
            style={inputStyle}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>

        {error && (
          <div
            role="alert"
            style={{
              fontFamily: mono,
              fontSize: '0.68rem',
              letterSpacing: '0.04em',
              color: 'var(--hb-amber-bright)',
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !username || !password}
          style={{
            marginTop: '0.2rem',
            padding: '0.6rem',
            background: busy ? 'var(--hb-cyan-dim)' : 'var(--hb-cyan)',
            color: '#04111a',
            border: 'none',
            borderRadius: 8,
            fontFamily: mono,
            fontSize: '0.72rem',
            letterSpacing: '0.2em',
            textTransform: 'uppercase',
            cursor: busy || !username || !password ? 'default' : 'pointer',
            opacity: !username || !password ? 0.5 : 1,
          }}
        >
          {busy ? 'Authenticating…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
