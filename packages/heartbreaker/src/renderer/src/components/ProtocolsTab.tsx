import { useCallback, useEffect, useState } from 'react'
import {
  getHouseParty, setHouseParty, getLockdown, standDownLockdown,
} from '../lib/api'
import type { LockdownState } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { SettingsSection, SettingsRow, PillBtn } from './settingsUI'
import { SkeletonText } from './Skeleton'
import { useT } from '../lib/i18n'

/**
 * PROTOCOLS — the standing operational modes, in one place.
 *
 * Both protocols are ENGAGED through an authorization modal rather than a
 * switch: they are passphrase-gated, and a toggle implies a reversibility that
 * "seal the server's ports" does not have. Standing either one down is a plain
 * button, because the way out is never gated.
 *
 * Lockdown reports two things that can disagree — the flag, and whether the
 * firewall rules are actually there. When they drift, this says so instead of
 * showing one confident green light (see LockdownState).
 */
export default function ProtocolsTab({ config, onEngageLockdown }: {
  config: AppConfig
  /** Opens the Lockdown authorization modal — owned by Layout, which renders it
   *  above the settings window. */
  onEngageLockdown: () => void
}) {
  const t = useT()
  const [party, setParty] = useState(false)
  const [lock, setLock] = useState<LockdownState | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [p, l] = await Promise.all([getHouseParty(config), getLockdown(config)])
      setParty(p)
      setLock(l)
    } finally {
      // Always clear — an unreachable backend must not skeleton this forever.
      setLoaded(true)
    }
  }, [config])

  useEffect(() => {
    refresh()
    // The flag can move from chat or the phone, so this pane cannot assume it is
    // the only thing changing it.
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  const standDown = async () => {
    setBusy(true); setNote(null)
    const res = await standDownLockdown(config)
    setNote(res.ok ? (res.report || t.protocolsTab.containmentStoodDown) : (res.error || t.protocolsTab.standDownFailed))
    await refresh()
    setBusy(false)
  }

  const engaged = !!lock?.engaged
  const ruleList = Object.entries(lock?.rules || {})
  // The flag says contained but the rules are not all in place (or vice versa).
  // Worth surfacing loudly: it is the difference between believing you are
  // sealed and being sealed.
  const drift = !!lock && lock.enabled && ruleList.length > 0
    && ruleList.some(([, on]) => on !== engaged)
  // Whether anything is ACTUALLY sealed, regardless of what the flag believes.
  // This — not `engaged` — is what decides whether standing down is offered.
  const sealed = ruleList.some(([, on]) => on)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <SettingsSection title={t.protocolsTab.lockdownTitle} first />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.lockdownDesc}
      </div>

      {!loaded ? (
        <div className="hb-tile" style={{ padding: '16px 18px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
          <SkeletonText lines={2} lastWidth="60%" />
        </div>
      ) : (
        <SettingsRow
          title={engaged ? t.protocolsTab.containmentActive : t.protocolsTab.containmentInactive}
          desc={
            !lock?.enabled
              ? t.protocolsTab.lockdownDisabled
              : engaged
                ? t.protocolsTab.portsSealed
                : t.protocolsTab.acceptingNormally
          }
        >
          {/* Offered whenever the flag says contained OR a rule is actually in
              place. Gating this on the flag alone hid the escape hatch in the one
              state that most needs it: engage() applies the firewall rules BEFORE
              it persists the flag (deliberately — see lockdown.py), so a request
              that dies in between leaves the ports sealed with the flag still
              reading off. The panel would then correctly report "firewall does not
              match the flag" while showing only an Engage button. disengage() is
              ungated and removes rules unconditionally, so this is always safe. */}
          {engaged || sealed ? (
            <PillBtn tone="danger" onClick={standDown} title={t.protocolsTab.removeRulesTitle}>
              {busy ? t.protocolsTab.standingDown : t.protocolsTab.standDown}
            </PillBtn>
          ) : (
            <PillBtn
              tone="danger"
              onClick={lock?.enabled ? onEngageLockdown : undefined}
              title={lock?.enabled ? t.protocolsTab.requiresPassphrase : t.protocolsTab.notEnabled}
            >
              {t.protocolsTab.engageLockdown}
            </PillBtn>
          )}
        </SettingsRow>
      )}

      {/* What the firewall actually shows, not just what the flag claims. */}
      {ruleList.length > 0 && (
        <div style={{
          display: 'flex', flexDirection: 'column', gap: 6,
          padding: '12px 16px',
          background: drift ? 'rgba(216,72,60,0.08)' : 'rgba(255,255,255,0.03)',
          border: `1px solid ${drift ? 'rgba(216,72,60,0.35)' : 'rgba(255,255,255,0.07)'}`,
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '0.6rem', letterSpacing: '0.18em',
            textTransform: 'uppercase', color: drift ? '#e5897c' : 'var(--hb-text-faint)',
          }}>
            {drift ? t.protocolsTab.firewallMismatch : t.protocolsTab.firewallRules}
          </div>
          {ruleList.map(([label, on]) => (
            <div key={label} style={{
              display: 'flex', justifyContent: 'space-between',
              fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--hb-text-dim)',
            }}>
              <span>{label}</span>
              <span style={{ color: on ? '#e5897c' : 'var(--hb-text-faint)' }}>
                {on ? t.protocolsTab.sealed : t.protocolsTab.open}
              </span>
            </div>
          ))}
        </div>
      )}

      {note && (
        <div style={{
          padding: '10px 14px', background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.07)',
          fontFamily: 'var(--font-mono)', fontSize: '0.68rem', lineHeight: 1.6,
          color: 'var(--hb-text-dim)', whiteSpace: 'pre-wrap',
        }}>
          {note}
        </div>
      )}

      <SettingsSection title={t.protocolsTab.housePartyTitle} />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.housePartyDesc}
      </div>

      {!loaded ? (
        <div className="hb-tile" style={{ padding: '16px 18px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
          <SkeletonText lines={2} lastWidth="55%" />
        </div>
      ) : (
        <SettingsRow
          title={party ? t.protocolsTab.protocolEngaged : t.protocolsTab.protocolOffline}
          desc={party ? t.protocolsTab.rosterMobilized : t.protocolsTab.sayToRequest}
        >
          {party ? (
            <PillBtn
              tone="danger"
              onClick={async () => { setParty(await setHouseParty(config, false)) }}
              title={t.protocolsTab.endHouseParty}
            >
              {t.protocolsTab.standDown}
            </PillBtn>
          ) : (
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>{t.protocolsTab.ownerVoiceOnly}</span>
          )}
        </SettingsRow>
      )}
    </div>
  )
}
