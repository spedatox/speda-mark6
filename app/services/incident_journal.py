"""The Hall's owner-only view of captured operational incidents."""
import asyncio
import json
import time
from datetime import datetime, timezone

from app.services.damage_control import render_report

_fingerprint = "json_array(" + ", ".join(
    f"json_extract(facts, '$.{key}')" for key in (
        "event", "agent", "worker", "tool", "service", "component", "code", "error_code",
        "exception", "kind", "impact", "ended_turn",
    )
) + ", CASE WHEN json_extract(facts, '$.event') = 'runtime_log' THEN json_extract(facts, '$.site') END)"
_events = f"""SELECT incidents.*, {_fingerprint} AS fingerprint,
    inspection.reviewed_at, inspection.assessment_state, inspection.assessment, inspection.assessed_at,
    CASE
        WHEN json_extract(facts, '$.event') = 'synthetic_protocol_test' THEN 'test'
        WHEN json_extract(facts, '$.ended_turn') = 1
             OR json_extract(facts, '$.impact') = 'outcome_uncertain'
             OR json_extract(facts, '$.event') IN
                ('service_unexpected_exit', 'service_out_of_memory', 'service_unhealthy') THEN 'critical'
        WHEN json_extract(facts, '$.severity') = 'WARNING' THEN 'warning'
        ELSE 'error'
    END AS severity
    FROM incidents LEFT JOIN incident_inspections AS inspection USING (id)
    WHERE status <> 'active'"""


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def facts_for(row):
    return {"incident_id": row["id"], "observed_at": timestamp(row["timestamp"]), **json.loads(row["facts"])}


def assessment_for(row):
    if row["assessment"]:
        return "complete", row["assessment"]
    if row["analysis"] and not row["analysis"].startswith(("Model analysis unavailable", "Model analysis was interrupted")):
        return "complete", row["analysis"]
    state = row["assessment_state"]
    if state == "running" and time.time() - row["assessed_at"] > 120:
        state = "interrupted"
    if not state:
        state = "running" if row["status"] in {"pending", "analysing"} else "available"
    return state, None


class JournalView:
    def __init__(self, journal):
        self.journal = journal

    def list(self, *, reviewed, severity, search, limit, offset):
        with self.journal.connect() as db:
            rows = db.execute(f"""WITH events AS ({_events}), grouped AS (
                SELECT *, COUNT(*) OVER group_key AS occurrences,
                    ROW_NUMBER() OVER (PARTITION BY fingerprint ORDER BY timestamp DESC, id DESC) AS latest
                FROM events WINDOW group_key AS (PARTITION BY fingerprint)
            )
            SELECT * FROM grouped WHERE latest=1
                AND (? OR (reviewed_at IS NULL AND severity <> 'test'))
                AND (? IS NULL OR severity=?)
                AND (?='' OR INSTR(LOWER(facts), ?) > 0 OR id=?)
            ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?""",
                (reviewed, severity, severity, search, search, search, limit + 1, offset)).fetchall()
        incidents = []
        for row in rows[:limit]:
            facts = json.loads(row["facts"])
            incidents.append({
                "id": row["id"], "event": facts["event"], "severity": row["severity"],
                "source": facts.get("agent") or facts.get("service") or facts.get("component"),
                "observed_at": timestamp(row["timestamp"]),
                "occurrences": row["occurrences"], "reviewed": row["reviewed_at"] is not None,
            })
        return {"incidents": incidents, "next_offset": offset + limit if len(rows) > limit else None}

    def detail(self, identity):
        with self.journal.connect() as db:
            row = db.execute(f"SELECT * FROM ({_events}) WHERE id=?", (identity,)).fetchone()
            if row is None:
                return None
            occurrences = db.execute(f"""SELECT id, timestamp FROM ({_events}) WHERE fingerprint=?
                ORDER BY timestamp DESC, id DESC LIMIT 20""", (row["fingerprint"],)).fetchall()
        state, assessment = assessment_for(row)
        return {
            "id": identity, "severity": row["severity"], "facts": facts_for(row),
            "reviewed": row["reviewed_at"] is not None, "assessment_state": state, "assessment": assessment,
            "delivery_state": row["status"],
            "recent_occurrences": [{"id": item["id"], "observed_at": timestamp(item["timestamp"])} for item in occurrences],
        }

    def review(self, identity):
        with self.journal.connect() as db:
            db.execute("""INSERT INTO incident_inspections(id, reviewed_at)
                SELECT id, ? FROM incidents WHERE id=? AND status <> 'active'
                ON CONFLICT(id) DO UPDATE SET reviewed_at=COALESCE(reviewed_at, excluded.reviewed_at)""",
                (time.time(), identity))
        return self.detail(identity)

    def claim_assessment(self, identity):
        with self.journal.connect() as db:
            row = db.execute(f"SELECT * FROM ({_events}) WHERE id=?", (identity,)).fetchone()
            if row is None or assessment_for(row)[0] != "available":
                return None
            claim = db.execute("""INSERT INTO incident_inspections(id, assessment_state, assessed_at)
                VALUES (?, 'running', ?) ON CONFLICT(id) DO UPDATE SET
                    assessment_state='running', assessed_at=excluded.assessed_at WHERE assessment_state IS NULL""",
                (identity, time.time()))
            return facts_for(row) if claim.rowcount == 1 else None

    def finish_assessment(self, identity, assessment):
        with self.journal.connect() as db:
            db.execute("""UPDATE incident_inspections SET assessment_state=?, assessment=?
                WHERE id=? AND assessment_state='running'""",
                ("complete" if assessment else "unavailable", assessment, identity))
        return self.detail(identity)

    async def assess(self, identity, analyse):
        facts = await asyncio.to_thread(self.claim_assessment, identity)
        if facts is None:
            return await asyncio.to_thread(self.detail, identity)
        analysis = None
        try:
            async with asyncio.timeout(45):
                analysis = await analyse(facts)
        except Exception:
            pass
        finally:
            detail = await asyncio.shield(asyncio.to_thread(self.finish_assessment, identity, analysis))
        return detail

    def pdf(self, identity):
        incident = self.detail(identity)
        if incident is None:
            return None
        return render_report(incident["facts"], incident["assessment"] or "The journal contains recorded observations for this incident.")
