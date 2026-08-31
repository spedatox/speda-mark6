// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

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
 * 2. Saving does NOT sign in. It used to — but a login attempt on every save
 *    means every typo, every slow captcha, every portal having a bad moment
 *    surfaces as a scary red error on what the owner meant as "just store
 *    this". Saving now only stores the credential, same as writing a line to
 *    an .env file. The per-portal "Test" button (and the automatic sign-in
 *    the FIRST time an agent actually opens the portal) are what confirm it
 *    works, when the owner actually wants to know.
 *
 * The advanced block (landing page, agent restriction, note, selectors) stays
 * folded away because none of it is required to get going: a name, the
 * sign-in page, and the credentials are the whole of what's needed. The login
 * form is also the one form on the web with a reliable shape, so the backend
 * finds it by looking for the password box without the selectors below ever
 * being filled in.
 */

import { useEffect, useState } from 'react'
import {
  getPortals, savePortal, portalLogin, portalForget, deletePortal,
  type Portal, type BrowserStatus,
} from '../lib/api'
import type { AppConfig } from '../lib/types'
import { PillBtn, ServiceRow, Switch, fieldStyle } from './settingsUI'
import { Skeleton, SkeletonList } from './Skeleton'
import { useT } from '../lib/i18n'
import type { Dict } from '../lib/i18n/en'

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
function statusLine(p: Portal, t: Dict): string {
  if (!p.enabled) return t.portalsPanel.switchedOff
  const bits: string[] = []
  bits.push(p.session ? t.portalsPanel.signedIn : t.portalsPanel.noLiveSession)
  if (p.username) bits.push(p.username)
  if (p.last_status) bits.push(p.last_status.startsWith('ok:') ? t.portalsPanel.lastSignInWorked : p.last_status)
  return bits.join(' · ')
}

export default function PortalsPanel({ config }: { config: AppConfig }) {
  const t = useT()
  const [portals, setPortals] = useState<Portal[]>([])
  const [browser, setBrowser] = useState<BrowserStatus>({ status: 'off' })
  const [agents, setAgents] = useState<string[]>([])
  const [loaded, setLoaded] = useState(false)
  const [draft, setDraft] = useState({ ...EMPTY })
  const [allowed, setAllowed] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = async () => {
    try {
      const r = await getPortals(config)
      setPortals(r.portals)
      setBrowser(r.browser)
      setAgents(r.agents)
    } finally {
      // Always clear — an unreachable backend must not skeleton this forever.
      setLoaded(true)
    }
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
      // Just store it — see the module docstring for why saving no longer
      // signs in on its own. The "Test" button is right there for whenever
      // the owner actually wants that checked.
      test: false,
    })
    setBusy(null)
    await load()
    if (r.error) { setMsg({ text: r.error, ok: false }); return }
    setMsg({ text: r.message || t.portalsPanel.saved, ok: true })
    setTimeout(reset, 1200)
  }

  const test = async (name: string) => {
    setBusy(name); setMsg(null)
    const r = await portalLogin(config, name)
    setBusy(null)
    await load()
    setMsg({
      text: r.already
        ? t.portalsPanel.alreadySignedIn(name)
        : t.portalsPanel.testResult(name, !!r.ok, r.message || r.error || ''),
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
        {t.portalsPanel.intro}
      </p>
      {!loaded ? (
        <div style={{ margin: '0 0 14px' }}>
          <Skeleton height={13} width="65%" />
        </div>
      ) : (
        <p style={{
          fontSize: '0.8125rem', lineHeight: 1.55, margin: '0 0 14px',
          color: browser.status === 'ok' ? 'var(--hb-text-faint)' : '#e5b07c',
        }}>
          {browser.status === 'ok'
            ? t.portalsPanel.browserOnline(browser.sessions ?? 0)
            : browser.status === 'off'
              ? t.portalsPanel.browserNotConfigured
              : t.portalsPanel.browserUnreachable(browser.reason ?? t.portalsPanel.noAnswer)}
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {!loaded && <SkeletonList rows={2} />}
        {loaded && portals.map(p => (
          <ServiceRow
            key={p.name}
            tint={p.session ? '#5cc98f' : undefined}
            icon={<PortalIcon />}
            name={p.label || p.name}
            desc={statusLine(p, t)}
          >
            <Switch
              on={p.enabled}
              onChange={v => toggle(p, v)}
              title={p.enabled ? t.mcpServersPanel.enabledClickDisable : t.mcpServersPanel.disabledClickEnable}
            />
            <PillBtn onClick={() => test(p.name)} title={t.portalsPanel.testTitle}>
              {busy === p.name ? '…' : t.portalsPanel.test}
            </PillBtn>
            {p.session && (
              <PillBtn onClick={() => signOut(p.name)} title={t.portalsPanel.signOutTitle}>
                {t.portalsPanel.signOut}
              </PillBtn>
            )}
            <PillBtn onClick={() => startEdit(p)}>{t.mcpServersPanel.edit}</PillBtn>
            <PillBtn onClick={() => remove(p.name)} tone="danger" title={t.portalsPanel.deleteTitle}>
              {t.portalsPanel.remove}
            </PillBtn>
          </ServiceRow>
        ))}
        {loaded && portals.length === 0 && !open && (
          <p style={{ fontSize: '0.875rem', color: 'var(--hb-text-faint)', margin: 0 }}>
            {t.portalsPanel.noneYet}
          </p>
        )}
      </div>

      {!open && (
        <div style={{ marginTop: 14 }}>
          <PillBtn onClick={() => setOpen(true)} tone="accent">{t.portalsPanel.addPortal}</PillBtn>
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
            {editing ? t.portalsPanel.editPortal(editing) : t.portalsPanel.newPortal}
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ width: 180 }}>
              <Label>{t.portalsPanel.shortName}</Label>
              <input
                style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                value={draft.name}
                disabled={Boolean(editing)}
                placeholder="obs"
                onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Label>{t.portalsPanel.label}</Label>
              <input
                style={fieldStyle}
                value={draft.label}
                placeholder="Öğrenci Bilgi Sistemi"
                onChange={e => setDraft(d => ({ ...d, label: e.target.value }))}
              />
            </div>
          </div>
          <Hint>
            {t.portalsPanel.shortNameHintPre} <code>obs</code> {t.portalsPanel.shortNameHintPost}
          </Hint>

          <div>
            <Label>{t.portalsPanel.signInPage}</Label>
            <input
              style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
              value={draft.login_url}
              placeholder="https://obs.example.edu.tr/login"
              onChange={e => setDraft(d => ({ ...d, login_url: e.target.value }))}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Label>{t.portalsPanel.username}</Label>
              <input
                style={fieldStyle}
                value={draft.username}
                autoComplete="off"
                onChange={e => setDraft(d => ({ ...d, username: e.target.value }))}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Label>{t.portalsPanel.password}</Label>
              <input
                style={fieldStyle}
                type="password"
                value={draft.password}
                autoComplete="new-password"
                placeholder={editing ? t.portalsPanel.unchanged : ''}
                onChange={e => setDraft(d => ({ ...d, password: e.target.value }))}
              />
            </div>
          </div>

          <div>
            <PillBtn onClick={() => setAdvanced(v => !v)}>
              {advanced ? t.portalsPanel.hideAdvanced : t.portalsPanel.showAdvanced}
            </PillBtn>
          </div>

          {advanced && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <Label>{t.portalsPanel.landingPage}</Label>
                <input
                  style={{ ...fieldStyle, fontFamily: 'var(--font-mono)' }}
                  value={draft.home_url}
                  placeholder="https://obs.example.edu.tr/dashboard"
                  onChange={e => setDraft(d => ({ ...d, home_url: e.target.value }))}
                />
                <Hint>{t.portalsPanel.landingPageHint}</Hint>
              </div>

              {agents.length > 0 && (
                <div>
                  <Label>{t.portalsPanel.whichAgents}</Label>
                  <Hint>{t.portalsPanel.whichAgentsHint}</Hint>
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
                <Label>{t.portalsPanel.note}</Label>
                <input
                  style={fieldStyle}
                  value={draft.note}
                  placeholder={t.portalsPanel.notePlaceholder}
                  onChange={e => setDraft(d => ({ ...d, note: e.target.value }))}
                />
                <Hint>{t.portalsPanel.noteHint}</Hint>
              </div>

              <Hint>
                {t.portalsPanel.advancedSelectorsHint}
              </Hint>
              {([
                ['username_selector', t.portalsPanel.usernameSelector, '#txtParamT01'],
                ['password_selector', t.portalsPanel.passwordSelector, '#txtParamT02'],
                ['submit_selector', t.portalsPanel.submitSelector, '#btnLogin'],
                ['success_selector', t.portalsPanel.successSelector, '.main-menu'],
                ['success_url_contains', t.portalsPanel.successUrlContains, '/dashboard'],
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
              title={canSave ? undefined : t.portalsPanel.needsFields}
            >
              {busy === 'save' ? t.portalsPanel.saving : t.portalsPanel.save}
            </PillBtn>
            <PillBtn onClick={reset}>{t.mcpServersPanel.cancel}</PillBtn>
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
