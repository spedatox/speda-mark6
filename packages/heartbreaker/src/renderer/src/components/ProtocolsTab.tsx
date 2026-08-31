// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useState } from 'react'
import {
  getHouseParty, setHouseParty, getLockdown, standDownLockdown,
  getLifeboat, getDoormat, getOctavius, runOctaviusBackup,
} from '../lib/api'
import type { LockdownState, LifeboatState, DoormatState, OctaviusState } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { SettingsSection, SettingsRow, PillBtn } from './settingsUI'
import SkyfallProjects from './SkyfallProjects'
import { SkeletonText } from './Skeleton'
import { useT } from '../lib/i18n'

/**
 * PROTOCOLS — the standing operational modes, in one place.
 *
 * Five of them now, and they do not all have the same shape, deliberately:
 *
 *   Lockdown   engaged through an authorization modal, stood down with a button
 *   Lifeboat   read-only here; reclamation is owner-led THROUGH Orion
 *   Octavius   read-only plus one button, because "back up now" cannot lose anything
 *   Doormat    read-only here; a domain move is a conversation, not a form
 *   Skyfall    the owner's own launch rail — the one pane that CONFIGURES
 *   House Party  owner voice only
 *
 * The read-only ones are not an omission. Each has a gate in its skill that
 * requires the owner in the conversation, and a button here would be a second
 * path around that gate — the thing CLAUDE.md forbids for House Party and for
 * the same reason. What a panel is genuinely better at than a chat message is
 * showing STATE you want to look at while doing something else: a disk figure, a
 * backup age, the console checklist you are working through. That is what these
 * sections carry.
 *
 * TWO POLLING RATES, ON PURPOSE. Lockdown and House Party are process flags that
 * can move from chat or the phone, and reading them is free — every 5s. Lifeboat
 * costs an SSH round trip to the host and Octavius costs a Google API call;
 * neither answer changes in five seconds, so they are fetched once on mount and
 * after an action. Polling those at 5s for as long as a settings window is open
 * would be a real cost for no information.
 *
 * Every panel here reports the flag AND what the machine actually shows, and
 * says so when the two disagree. That is the whole reason these are panels and
 * not switches.
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

  // The expensive half — see the polling note above.
  const [boat, setBoat] = useState<LifeboatState | null>(null)
  const [door, setDoor] = useState<DoormatState | null>(null)
  const [arc, setArc] = useState<OctaviusState | null>(null)
  const [hostLoaded, setHostLoaded] = useState(false)
  const [backing, setBacking] = useState(false)

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

  const refreshHost = useCallback(async () => {
    try {
      const [b, d, a] = await Promise.all([
        getLifeboat(config), getDoormat(config), getOctavius(config),
      ])
      setBoat(b); setDoor(d); setArc(a)
    } finally {
      setHostLoaded(true)
    }
  }, [config])

  useEffect(() => {
    refresh()
    // The flag can move from chat or the phone, so this pane cannot assume it is
    // the only thing changing it.
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  useEffect(() => { refreshHost() }, [refreshHost])

  const standDown = async () => {
    setBusy(true); setNote(null)
    const res = await standDownLockdown(config)
    setNote(res.ok ? (res.report || t.protocolsTab.containmentStoodDown) : (res.error || t.protocolsTab.standDownFailed))
    await refresh()
    setBusy(false)
  }

  const backUpNow = async () => {
    setBacking(true); setNote(null)
    const res = await runOctaviusBackup(config)
    setNote(
      res.ok
        ? t.protocolsTab.backupDone(res.name || '')
        // The stage is carried through because they are not interchangeable: an
        // integrity failure is a statement about the LIVE database.
        : t.protocolsTab.backupFailed(res.stage || '?', res.error || ''),
    )
    await refreshHost()
    setBacking(false)
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
        <Loading />
      ) : (
        <SettingsRow
          title={
            !lock?.reachable ? t.protocolsTab.backendUnreachable
              : engaged ? t.protocolsTab.containmentActive
                : t.protocolsTab.containmentInactive
          }
          desc={
            !lock?.reachable
              ? t.protocolsTab.backendUnreachableHint
              : !lock.enabled
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
        <Readout title={drift ? t.protocolsTab.firewallMismatch : t.protocolsTab.firewallRules} alarm={drift}>
          {ruleList.map(([label, on]) => (
            <Line key={label} left={label} right={on ? t.protocolsTab.sealed : t.protocolsTab.open}
                  tone={on ? 'alarm' : 'faint'} />
          ))}
        </Readout>
      )}

      {/* ── LIFEBOAT ─────────────────────────────────────────────────────── */}
      <SettingsSection title={t.protocolsTab.lifeboatTitle} />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.lifeboatDesc}
      </div>

      {!hostLoaded ? <Loading /> : (
        <>
          <SettingsRow
            title={
              !boat?.reachable ? t.protocolsTab.backendUnreachable
                : boat.status === 'disabled' ? t.protocolsTab.notEnabled
                  : boat.status === 'error' ? t.protocolsTab.hostUnreadable
                    : t.protocolsTab.lifeboatLevel(boat.level)
            }
            /* Three different failures, three different answers. Reporting an
               unreachable backend as "disabled on this deployment" sends the
               owner to change a setting that was never the problem. */
            desc={
              !boat?.reachable ? t.protocolsTab.backendUnreachableHint
                : boat.status === 'disabled' ? t.protocolsTab.lifeboatDisabledHint
                  : boat.status === 'error'
                    ? (boat.detail || t.protocolsTab.hostUnreadableHint)
                    : (boat.summary || t.protocolsTab.hostHealthy)
            }
          >
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>
              {t.protocolsTab.throughOrion}
            </span>
          </SettingsRow>

          {boat?.status === 'ok' && (
            <Readout
              title={boat.pressed.length ? t.protocolsTab.underPressure : t.protocolsTab.hostResources}
              alarm={boat.level === 'critical'}
            >
              <Line left="disk" right={`${boat.readings.disk_pct ?? '?'}%  ·  ${boat.readings.disk_free_gb ?? '?'} GB free`}
                    tone={levelTone(boat.by_resource.disk)} />
              <Line left="inodes" right={`${boat.readings.inode_pct ?? '?'}%`}
                    tone={levelTone(boat.by_resource.inodes)} />
              <Line left="memory" right={`${boat.readings.mem_pct ?? '?'}%  ·  ${boat.readings.mem_available_gb ?? '?'} GB free`}
                    tone={levelTone(boat.by_resource.memory)} />
              <Line left="docker reclaimable" right={`${boat.readings.docker_reclaimable_gb ?? 0} GB`} tone="faint" />
            </Readout>
          )}
        </>
      )}

      {/* ── OCTAVIUS ─────────────────────────────────────────────────────── */}
      <SettingsSection title={t.protocolsTab.octaviusTitle} />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.octaviusDesc}
      </div>

      {!hostLoaded ? <Loading /> : (
        <SettingsRow
          title={
            !arc?.reachable ? t.protocolsTab.backendUnreachable
              : !arc.enabled ? t.protocolsTab.notEnabled
                : arc.count === 0 ? t.protocolsTab.noBackups
                  : arc.stale ? t.protocolsTab.backupsStale
                    : t.protocolsTab.backupsHealthy
          }
          desc={
            !arc?.reachable ? t.protocolsTab.backendUnreachableHint
              : !arc.enabled ? t.protocolsTab.octaviusDisabledHint
                : arc.latest
                  ? t.protocolsTab.backupLatest(
                    arc.latest.name, arc.latest.mb, arc.age_hours, arc.count)
                  : (arc.detail || t.protocolsTab.nothingToRestoreFrom)
          }
        >
          {arc?.reachable && arc.enabled ? (
            <PillBtn
              tone={arc.stale ? 'danger' : 'neutral'}
              onClick={backing ? undefined : backUpNow}
              title={t.protocolsTab.backupNowTitle}
            >
              {backing ? t.protocolsTab.backingUp : t.protocolsTab.backupNow}
            </PillBtn>
          ) : (
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>
              {t.protocolsTab.notEnabled}
            </span>
          )}
        </SettingsRow>
      )}

      {/* ── DOORMAT ──────────────────────────────────────────────────────── */}
      <SettingsSection title={t.protocolsTab.doormatTitle} />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.doormatDesc}
      </div>

      {!hostLoaded ? <Loading /> : (
        <>
          <SettingsRow
            title={
              !door?.reachable ? t.protocolsTab.backendUnreachable
                : !door.enabled ? t.protocolsTab.notEnabled
                  : door.phase === 'staged' ? t.protocolsTab.movePreparing
                    : door.phase === 'cutover' ? t.protocolsTab.moveCutOver
                      : t.protocolsTab.noMoveInProgress
            }
            desc={
              !door?.reachable ? t.protocolsTab.backendUnreachableHint
                : !door.enabled ? t.protocolsTab.doormatDisabledHint
                  : door.phase
                    ? t.protocolsTab.movingFromTo(door.previous || door.current_domain, door.target)
                    : t.protocolsTab.servingDomain(door.current_domain || '—')
            }
          >
            <span style={{ fontSize: '0.845rem', color: 'var(--hb-text-faint)' }}>
              {t.protocolsTab.throughOrion}
            </span>
          </SettingsRow>

          {/* The restart Orion was supposed to run after cutover. Loud, because
              until it happens every redirect URI still names the old domain. */}
          {door?.restart_pending && (
            <Readout title={t.protocolsTab.restartOutstanding} alarm>
              <div style={{ fontSize: '0.72rem', color: 'var(--hb-text-dim)', lineHeight: 1.6 }}>
                {t.protocolsTab.restartOutstandingHint}
              </div>
            </Readout>
          )}

          {/* The reason this section is worth a panel at all: the console steps
              are something you sit and work through, not something to ask an
              agent to re-print each time you finish one. */}
          {door?.phase === 'staged' && door.checklist.length > 0 && (
            <Readout title={t.protocolsTab.consoleChecklist}>
              <div style={{ fontSize: '0.72rem', color: 'var(--hb-text-dim)', lineHeight: 1.6, marginBottom: 4 }}>
                {t.protocolsTab.addDoNotReplace}
              </div>
              {door.checklist.map((step, i) => (
                <div key={`${step.provider}-${i}`} style={{ marginTop: 8 }}>
                  <div style={{ fontSize: '0.72rem', color: 'var(--hb-text)' }}>
                    {i + 1}. {step.provider}
                  </div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--hb-text-faint)' }}>{step.where}</div>
                  <div style={{
                    fontSize: '0.68rem', color: 'var(--hb-cyan-bright)',
                    wordBreak: 'break-all', userSelect: 'text',
                  }}>
                    {step.field}: {step.value}
                  </div>
                </div>
              ))}
            </Readout>
          )}
        </>
      )}

      {/* ── SKYFALL ──────────────────────────────────────────────────────── */}
      <SettingsSection title={t.protocolsTab.skyfallTitle} />
      <div style={{ fontSize: '0.8125rem', color: 'var(--hb-text-faint)', lineHeight: 1.6, marginTop: -8 }}>
        {t.protocolsTab.skyfallDesc}
      </div>
      {/* The only protocol pane that writes rather than reads, and deliberately:
          a project is a URL, a body and a credential, and no agent has a tool
          that can create one. The countdown means something because the owner
          wrote what is behind it. */}
      <SkyfallProjects config={config} />

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
        <Loading />
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

/* ── The shapes these five sections share ─────────────────────────────────── */

type Tone = 'alarm' | 'warn' | 'faint'

/** healthy → faint, watch → amber, critical → the same red the firewall uses. */
function levelTone(level?: string): Tone {
  return level === 'critical' ? 'alarm' : level === 'watch' ? 'warn' : 'faint'
}

const TONE_COLOR: Record<Tone, string> = {
  alarm: '#e5897c', warn: '#d9a441', faint: 'var(--hb-text-faint)',
}

function Loading() {
  return (
    <div className="hb-tile" style={{
      padding: '16px 18px', background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.07)',
    }}>
      <SkeletonText lines={2} lastWidth="60%" />
    </div>
  )
}

/** A mono readout of what the machine actually reports, under a small caption.
 *  `alarm` reddens the whole block — used when the numbers are the bad news, not
 *  merely the detail. */
function Readout({ title, alarm, children }: {
  title: string; alarm?: boolean; children: React.ReactNode
}) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: '12px 16px',
      background: alarm ? 'rgba(216,72,60,0.08)' : 'rgba(255,255,255,0.03)',
      border: `1px solid ${alarm ? 'rgba(216,72,60,0.35)' : 'rgba(255,255,255,0.07)'}`,
      fontFamily: 'var(--font-mono)',
    }}>
      <div style={{
        fontSize: '0.6rem', letterSpacing: '0.18em', textTransform: 'uppercase',
        color: alarm ? '#e5897c' : 'var(--hb-text-faint)',
      }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Line({ left, right, tone }: { left: string; right: string; tone: Tone }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: 12,
      fontSize: '0.68rem', color: 'var(--hb-text-dim)',
    }}>
      <span>{left}</span>
      <span style={{ color: TONE_COLOR[tone] }}>{right}</span>
    </div>
  )
}
