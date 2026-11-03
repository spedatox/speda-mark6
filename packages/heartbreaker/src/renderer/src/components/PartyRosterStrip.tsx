import type { AppConfig } from '../lib/types'
import { useT } from '../lib/i18n'

/** Protocol controls only. Membership and speaking state come from the active room. */
export default function PartyRosterStrip({ engaged, onExit, onOpenConfig }: {
  config: AppConfig; engaged: boolean; onExit: () => void; onOpenConfig: () => void
}) {
  const t = useT().room
  return <div className="hb-room-toolbar hb-room-protocol">
    <div><strong>{engaged ? t.houseParty : t.standby}</strong><span className="hb-room-caption">{t.highStakes}</span></div>
    <button className="hb-btn glass-round" onClick={onOpenConfig}>Roster cores</button>
    {!engaged && <button className="hb-btn glass-round" onClick={() => window.dispatchEvent(new CustomEvent('speda:hpp-authorize'))}>{t.engage}</button>}
    <button className="hb-btn glass-round" onClick={onExit}>{engaged ? 'Stand down' : 'Exit'}</button>
  </div>
}
