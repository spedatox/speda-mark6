import { useCallback, useEffect, useState } from 'react'
import {
  getHouseParty, setHouseParty, getLockdown, standDownLockdown,
} from '../lib/api'
import type { LockdownState } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { SettingsSection, SettingsRow, PillBtn } from './settingsUI'
import { SkeletonText } from './Skeleton'

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
    setNote(res.ok ? (res.report || 'Containment stood down.') : (res.error || 'Stand down failed.'))
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
      <SettingsSection title="Lockdown Protocol" first />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        Emergency inbound containment. Engaging seals the server's exposed SSH and raw app
        ports behind firewall rules immediately. This app's HTTPS channel and all outbound
        traffic stay up, so SPEDA keeps working and containment can always be lifted from here.
      </div>

      {!loaded ? (
        <div className="hb-tile" style={{ padding: '16px 18px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
          <SkeletonText lines={2} lastWidth="60%" />
        </div>
      ) : (
        <SettingsRow
          title={engaged ? 'Containment ACTIVE' : 'Containment inactive'}
          desc={
            !lock?.enabled
              ? 'Disabled on this deployment — set LOCKDOWN_PROTOCOL_ENABLED to use it.'
              : engaged
                ? 'Host SSH (22) and the app raw port (8000) are sealed.'
                : 'The server is accepting inbound connections normally.'
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
            <PillBtn tone="danger" onClick={standDown} title="Remove the containment rules">
              {busy ? 'Standing down…' : 'Stand down'}
            </PillBtn>
          ) : (
            <PillBtn
              tone="danger"
              onClick={lock?.enabled ? onEngageLockdown : undefined}
              title={lock?.enabled ? 'Requires the authorization passphrase' : 'Not enabled on this deployment'}
            >
              Engage lockdown
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
            {drift ? 'Firewall does not match the flag' : 'Firewall rules'}
          </div>
          {ruleList.map(([label, on]) => (
            <div key={label} style={{
              display: 'flex', justifyContent: 'space-between',
              fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--hb-text-dim)',
            }}>
              <span>{label}</span>
              <span style={{ color: on ? '#e5897c' : 'var(--hb-text-faint)' }}>
                {on ? 'SEALED' : 'open'}
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

      <SettingsSection title="House Party Protocol" />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        All-hands mode: SPEDA becomes mission commander and dispatches the entire roster in
        parallel at full model grade. Heavy, expensive, still a prototype — engaged by telling
        SPEDA directly ("House Party Protocol"), which opens its authorization window.
      </div>

      {!loaded ? (
        <div className="hb-tile" style={{ padding: '16px 18px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
          <SkeletonText lines={2} lastWidth="55%" />
        </div>
      ) : (
        <SettingsRow
          title={party ? 'Protocol ENGAGED' : 'Protocol offline'}
          desc={party
            ? 'The full roster is mobilized; the deck is in war-room mode.'
            : 'Say "House Party Protocol" to SPEDA to request authorization.'}
        >
          {party ? (
            <PillBtn
              tone="danger"
              onClick={async () => { setParty(await setHouseParty(config, false)) }}
              title="End the House Party Protocol"
            >
              Stand down
            </PillBtn>
          ) : (
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>Owner voice only</span>
          )}
        </SettingsRow>
      )}
    </div>
  )
}
