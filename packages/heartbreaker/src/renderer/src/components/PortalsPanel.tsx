/**
 * Web portals — the sites the owner has an account on.
 *
 * The student automation, the library, anything else that never published an
 * API and never will. Name it, give it the sign-in page and the credentials,
 * and the agents can read it and work through it via the browser container.
 *
 * Two things this panel is careful about, because they are the two things that
 * make storing a password here defensible at all:
 *
 * 1. The password comes back MASKED and is sent back masked. The backend reads
 *    an unchanged mask as "keep the stored one", so editing a label never costs
 *    the owner a credential they can no longer read off the screen.
 * 2. Saving SIGNS IN. A stored-but-never-tried credential is indistinguishable
 *    from a working one until an agent needs it, which is always the worst
 *    moment to find the typo. The verdict is shown immediately, in the same
 *    words the browser reported.
 *
 * The advanced block (selectors, success check) stays folded away because it is
 * almost never needed: the login form is the one form on the web with a
 * reliable shape, and the backend finds it by looking for the password box.
 */

import { useEffect, useState } from 'react'
import {
  getPortals, savePortal, portalLogin, portalForget, deletePortal,
  type Portal, type BrowserStatus,
} from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PillBtn, ServiceRow, Switch, fieldStyle } from './settingsUI'

const EMPTY = {
  name: '', label: '', login_url: '', home_url: '', username: '', password: '',
  username_selector: '', password_selector: '', submit_selector: '',
  success_selector: '', success_url_contains: '', note: '',
}

const PortalIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 9h18" />
    <path d="M10 14h4" />
  </svg>
)

/** "logged in · last checked 2 hours ago", or whatever the truth is. */
function statusLine(p: Portal): string {
  if (!p.enabled) return 'switched off'
  const bits: string[] = []
  bits.push(p.session ? 'signed in' : 'no live session')
  if (p.username) bits.push(p.username)
  if (p.last_status) bits.push(p.last_status.startsWith('ok:') ? 'last sign-in worked' : p.last_status)
  return bits.join(' · ')
}

export default function PortalsPanel({ config }: { config: AppConfig }) {
  const [portals, setPortals] = useState<Portal[]>([])
  const [browser, setBrowser] = useState<BrowserStatus>({ status: 'off' })
  const [agents, setAgents] = useState<string[]>([])
  const [draft, setDraft] = useState({ ...EMPTY })
  const [allowed, setAllowed] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = async () => {
    const r = await getPortals(config)
    setPortals(r.portals)
    setBrowser(r.browser)
    setAgents(r.agents)
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => {
    setDraft({ ...EMPTY }); setAllowed([]); setEditing(null)
    setOpen(false); setAdvanced(false); setMsg(null)
  }

  const startEdit = (p: Portal) => {
    setDraft({
      name: p.name, label: p.label, login_url: p.login_url, home_url: p.home_url,
      username: p.username, password: p.password,
      username_selector: p.selectors?.username ?? '',
      password_selector: p.selectors?.password ?? '',
      submit_selector: p.selectors?.submit ?? '',
      success_selector: p.success_selector ?? '',
      success_url_contains: p.success_url_contains ?? '',
      note: p.note ?? '',
    })
    setAllowed(p.allowed_agents ?? [])
    setEditing(p.name)
    setAdvanced(Boolean(p.selectors?.username || p.success_selector || p.success_url_contains))
    setOpen(true)
    setMsg(null)
  }

  const save = async () => {
    setBusy('save'); setMsg(null)
    const r = await savePortal(config, {
      name: draft.name.trim().toLowerCase(),
      label: draft.label,
      login_url: draft.login_url.trim(),
      home_url: draft.home_url.trim(),
      username: draft.username.trim(),
      password: draft.password,
      selectors: {
        username: draft.username_selector.trim(),
        password: draft.password_selector.trim(),
        submit: draft.submit_selector.trim(),
      },
      success_selector: draft.success_selector.trim(),
      success_url_contains: draft.success_url_contains.trim(),
      allowed_agents: allowed,
      note: draft.note,
      enabled: true,
    })
    setBusy(null)
    await load()
    if (r.error) { setMsg({ text: r.error, ok: false }); return }
    setMsg({
      text: r.ok
        ? `Signed in${r.landed_on ? ` — landed on “${r.landed_on}”` : ''}.`
        : (r.message || 'Saved.'),
      ok: Boolean(r.ok),
    })
    if (r.ok) setTimeout(reset, 1800)
  }

  const test = async (name: string) => {
    setBusy(name); setMsg(null)
    const r = await portalLogin(config, name)
    setBusy(null)
    await load()
    setMsg({
      text: r.already
        ? `${name}: already signed in — the stored session is still live.`
        : `${name}: ${r.ok ? 'signed in.' : (r.message || r.error || 'sign-in failed.')}`,
      ok: Boolean(r.ok || r.already),
    })
  }

  const signOut = async (name: string) => {
    setBusy(name)
    await portalForget(config, name)
    setBusy(null)
    load()
  }

  const remove = async (name: string) => {
    setPortals(ps => ps.filter(p => p.name !== name)) // optimistic
    await deletePortal(config, name)
    load()
  }

  const toggle = async (p: Portal, enabled: boolean) => {
    setPortals(ps => ps.map(x => (x.name === p.name ? { ...x, enabled } : x)))
    await savePortal(config, { ...p, enabled, test: false })
    load()
  }

  const canSave = Boolean(
    draft.name.trim() && draft.login_url.trim().startsWith('http') &&
    draft.username.trim() && draft.password && !busy,
  )

  return (
    <div style={{ marginTop: -8 }}>
      <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.55, margin: '0 0 6px' }}>
        Sites you have an account on — your student automation, a library, anything without
        an API. Your agents open them in a real browser, already signed in. The password is
        stored on the backend and handed straight to the browser; it never passes through a
        conversation, and no agent can read it back.
      </p>
      <p style={{
        fontSize: '0.8125rem', lineHeight: 1.55, margin: '0 0 14px',
        color: browser.status === 'ok' ? 'var(--hb-text-faint)' : '#e5b07c',
      }}>
        {browser.status === 'ok'
          ? `Browser online — ${browser.sessions ?? 0} live session${browser.sessions === 1 ? '' : 's'}.`
          : browser.status === 'off'
            ? 'The browser container is not configured (BROWSER_URL unset), so portals cannot be opened.'
            : `Browser unreachable: ${browser.reason ?? 'no answer'}. Is the browser container running?`}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {portals.map(p => (
          <ServiceRow
            key={p.name}
            tint={p.session ? '#5cc98f' : undefined}
            icon={<PortalIcon />}
            name={p.label || p.name}
            desc={statusLine(p)}
          >
            <Switch
              on={p.enabled}
              onChange={v => toggle(p, v)}
              title={p.enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}
            />
            <PillBtn onClick={() => test(p.name)} title="Sign in now and report what happened">
              {busy === p.name ? '…' : 'Test'}
            </PillBtn>
            {p.session && (
              <PillBtn onClick={() => signOut(p.name)} title="Drop the stored session, keep the credentials">
                Sign out
              </PillBtn>
            )}
            <PillBtn onClick={() => startEdit(p)}>Edit</PillBtn>
            <PillBtn onClick={() => remove(p.name)} tone="danger" title="Delete the credentials and the session">
              Remove
            </PillBtn>
          </ServiceRow>
        ))}
        {portals.length === 0 && !open && (
          <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', margin: 0 }}>
            No portals yet.
          </p>
        )}
      </div>

      {!open && (
        <div style={{ marginTop: 14 }}>
          <PillBtn onClick={() => setOpen(true)} tone="accent">+ Add portal</PillBtn>
        </div>
      )}

      {msg && !open && (
        <p style={{ fontSize: '0.845rem', color: msg.ok ? '#8fdcb3' : '#e5897c', margin: '12px 0 0', lineHeight: 1.5 }}>
          {msg.text}
        </p>
      )}

      {open && (
        <div className="hb-tile" style={{
          marginTop: 14, padding: 18, display: 'flex', flexDirection: 'column', gap: 14,
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.09)',
        }}>
          <div style={{ fontSize: '0.9375rem', color: 'var(--hb-text)' }}>
            {editing ? `Edit “${editing}”` : 'New portal'}
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ width: 180 }}>
              <Label>Short name</Label>
              <input
                style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                value={draft.name}
                disabled={Boolean(editing)}
                placeholder="obs"
                onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Label>Label</Label>
              <input
                style={fieldStyle}
                value={draft.label}
                placeholder="Öğrenci Bilgi Sistemi"
                onChange={e => setDraft(d => ({ ...d, label: e.target.value }))}
              />
            </div>
          </div>
          <Hint>
            The short name is what an agent says — “check my grades on <code>obs</code>”. Keep it
            lowercase and memorable.
          </Hint>

          <div>
            <Label>Sign-in page</Label>
            <input
              style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
              value={draft.login_url}
              placeholder="https://obs.example.edu.tr/login"
              onChange={e => setDraft(d => ({ ...d, login_url: e.target.value }))}
            />
          </div>
          <div>
            <Label>Page to land on after signing in (optional)</Label>
            <input
              style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
              value={draft.home_url}
              placeholder="https://obs.example.edu.tr/dashboard"
              onChange={e => setDraft(d => ({ ...d, home_url: e.target.value }))}
            />
            <Hint>Where the agents start when they open this portal without a specific page.</Hint>
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Label>Username</Label>
              <input
                style={fieldStyle}
                value={draft.username}
                autoComplete="off"
                onChange={e => setDraft(d => ({ ...d, username: e.target.value }))}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Label>Password</Label>
              <input
                style={fieldStyle}
                type="password"
                value={draft.password}
                autoComplete="new-password"
                placeholder={editing ? 'unchanged' : ''}
                onChange={e => setDraft(d => ({ ...d, password: e.target.value }))}
              />
            </div>
          </div>

          {agents.length > 0 && (
            <div>
              <Label>Which agents may use it</Label>
              <Hint>None selected means all of them — right for a student portal, wrong for a bank.</Hint>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                {agents.map(a => {
                  const on = allowed.includes(a)
                  return (
                    <PillBtn
                      key={a}
                      tone={on ? 'accent' : undefined}
                      onClick={() => setAllowed(list => on ? list.filter(x => x !== a) : [...list, a])}
                    >
                      {a}
                    </PillBtn>
                  )
                })}
              </div>
            </div>
          )}

          <div>
            <Label>Note (optional)</Label>
            <input
              style={fieldStyle}
              value={draft.note}
              placeholder="grades, attendance, exam schedule"
              onChange={e => setDraft(d => ({ ...d, note: e.target.value }))}
            />
            <Hint>Shown to the agents, so write what the portal is for.</Hint>
          </div>

          <div>
            <PillBtn onClick={() => setAdvanced(v => !v)}>
              {advanced ? 'Hide advanced' : 'Advanced — the form did not work'}
            </PillBtn>
          </div>

          {advanced && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Hint>
                Normally none of this is needed: the browser finds the login form by looking for
                the password box. Fill these in only if a test sign-in reported it could not find
                the fields — right-click the field in Chrome → Inspect → Copy → Copy selector.
              </Hint>
              {([
                ['username_selector', 'Username field selector', '#txtParamT01'],
                ['password_selector', 'Password field selector', '#txtParamT02'],
                ['submit_selector', 'Sign-in button selector', '#btnLogin'],
                ['success_selector', 'Something only visible once signed in', '.main-menu'],
                ['success_url_contains', 'Text the signed-in URL contains', '/dashboard'],
              ] as const).map(([key, label, ph]) => (
                <div key={key}>
                  <Label>{label}</Label>
                  <input
                    style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                    value={draft[key]}
                    placeholder={ph}
                    onChange={e => setDraft(d => ({ ...d, [key]: e.target.value } as typeof d))}
                  />
                </div>
              ))}
            </div>
          )}

          {msg && (
            <p style={{ fontSize: '0.845rem', color: msg.ok ? '#8fdcb3' : '#e5897c', margin: 0, lineHeight: 1.5 }}>
              {msg.text}
            </p>
          )}

          <div style={{ display: 'flex', gap: 10 }}>
            <PillBtn
              onClick={canSave ? save : undefined}
              tone="accent"
              title={canSave ? undefined : 'Needs a name, a sign-in URL, a username and a password'}
            >
              {busy === 'save' ? 'Signing in…' : 'Save & sign in'}
            </PillBtn>
            <PillBtn onClick={reset}>Cancel</PillBtn>
          </div>
        </div>
      )}
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: '0.845rem', color: 'var(--hb-text-dim)', marginBottom: 8 }}>
      {children}
    </div>
  )
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', margin: '8px 0 0', lineHeight: 1.5 }}>
      {children}
    </p>
  )
}
