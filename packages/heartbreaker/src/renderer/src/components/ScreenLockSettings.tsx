// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react'
import { SettingsSection, SettingsRow, Switch, PillBtn, fieldStyle } from './settingsUI'
import GlassSelect from './GlassSelect'
import { useSettings } from '../store/settings'
import { useT } from '../lib/i18n'
import { hashPasscode } from '../lib/lock'

/** Idle-before-lock choices, in minutes. 0 is "never". */
const IDLE_MINUTES = [0, 1, 5, 10, 15, 30, 60]
/** Idle-on-the-lock-screen choices before the screensaver, in seconds. */
const SAVER_SECONDS = [0, 15, 30, 45, 60, 120, 300]

/**
 * Screen lock, in the Interface tab: whether the deck asks on launch, the
 * passcode itself, and the two idle timings. Its own component because the
 * passcode form carries state (two fields and an error) that has no business
 * living in the settings modal's already-long body.
 */
export default function ScreenLockSettings() {
  const { settings, update } = useSettings()
  const t = useT()
  const [editing, setEditing] = useState(false)
  const [first, setFirst] = useState('')
  const [second, setSecond] = useState('')
  const [err, setErr] = useState('')

  const hasPasscode = !!settings.lockPasscodeHash

  const save = async () => {
    if (!first) { setErr(t.settingsInterface.passcodeEmpty); return }
    if (first !== second) { setErr(t.settingsInterface.passcodeMismatch); return }
    update({ lockPasscodeHash: await hashPasscode(first) })
    setEditing(false)
    setFirst(''); setSecond(''); setErr('')
  }

  const label = (n: number, unit: 'min' | 'sec') =>
    n === 0
      ? t.settingsInterface.never
      : `${n} ${unit === 'min' ? t.settingsInterface.minutesUnit : t.settingsInterface.secondsUnit}`

  return (
    <>
      <SettingsSection title={t.settingsInterface.screenLock} />

      <SettingsRow
        title={t.settingsInterface.lockOnLaunch}
        desc={t.settingsInterface.lockOnLaunchDesc}
      >
        <Switch
          on={settings.lockOnLaunch}
          onChange={v => update({ lockOnLaunch: v })}
        />
      </SettingsRow>

      <SettingsRow
        title={t.settingsInterface.passcode}
        desc={hasPasscode
          ? t.settingsInterface.passcodeSetDesc
          : t.settingsInterface.passcodeUnsetDesc}
      >
        <div style={{ display: 'flex', gap: 8 }}>
          <PillBtn onClick={() => { setEditing(e => !e); setErr('') }} tone="accent">
            {hasPasscode ? t.settingsInterface.changePasscode : t.settingsInterface.setPasscode}
          </PillBtn>
          {hasPasscode && (
            <PillBtn tone="danger" onClick={() => update({ lockPasscodeHash: '' })}>
              {t.settingsInterface.clearPasscode}
            </PillBtn>
          )}
        </div>
      </SettingsRow>

      {editing && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: -10 }}>
          <input
            className="hb-tile"
            type="password"
            autoFocus
            value={first}
            onChange={e => { setFirst(e.target.value); setErr('') }}
            placeholder={t.settingsInterface.newPasscode}
            style={fieldStyle}
          />
          <input
            className="hb-tile"
            type="password"
            value={second}
            onChange={e => { setSecond(e.target.value); setErr('') }}
            onKeyDown={e => { if (e.key === 'Enter') void save() }}
            placeholder={t.settingsInterface.repeatPasscode}
            style={fieldStyle}
          />
          {err && (
            <div style={{ fontSize: '0.8125rem', color: '#e5897c' }}>{err}</div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <PillBtn tone="accent" onClick={() => void save()}>
              {t.settingsInterface.savePasscode}
            </PillBtn>
            <PillBtn onClick={() => { setEditing(false); setFirst(''); setSecond(''); setErr('') }}>
              {t.settingsInterface.cancelPasscode}
            </PillBtn>
          </div>
        </div>
      )}

      <SettingsRow
        title={t.settingsInterface.autoLock}
        desc={t.settingsInterface.autoLockDesc}
      >
        <div style={{ minWidth: 130 }}>
          <GlassSelect
            value={String(settings.lockIdleMinutes)}
            options={IDLE_MINUTES.map(n => ({ value: String(n), label: label(n, 'min') }))}
            onChange={v => update({ lockIdleMinutes: Number(v) })}
            tint="var(--hb-cyan-bright)"
          />
        </div>
      </SettingsRow>

      <SettingsRow
        title={t.settingsInterface.screensaver}
        desc={t.settingsInterface.screensaverDesc}
      >
        <div style={{ minWidth: 130 }}>
          <GlassSelect
            value={String(settings.lockScreensaverSeconds)}
            options={SAVER_SECONDS.map(n => ({ value: String(n), label: label(n, 'sec') }))}
            onChange={v => update({ lockScreensaverSeconds: Number(v) })}
            tint="var(--hb-cyan-bright)"
          />
        </div>
      </SettingsRow>
    </>
  )
}
