"""The Hall's owner-only view of captured operational incidents."""
import asyncio
import json
import time
from datetime import datetime, timezone

from app.services.damage_control import render_report, render_reports
from app.services.incident_details import explain

MAX_EXPORT = 1500
MAX_SELECTION = 1500

_fingerprint = "json_array(" + ", ".join(
    f"json_extract(facts, '$.{key}')" for key in (
        "event", "agent", "worker", "tool", "service", "component", "code", "error_code",
        "exception", "kind", "impact", "ended_turn", "provider", "server", "outlet", "endpoint",
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
_matching = f"""WITH events AS ({_events}), grouped AS (
    SELECT *, COUNT(*) OVER group_key AS occurrences,
        ROW_NUMBER() OVER (PARTITION BY fingerprint ORDER BY timestamp DESC, id DESC) AS latest
    FROM events WINDOW group_key AS (PARTITION BY fingerprint)
), matching AS (
    SELECT * FROM grouped WHERE (latest=1 OR id=?)
        AND (? OR (reviewed_at IS NULL AND severity <> 'test'))
        AND (? IS NULL OR severity=?)
        AND (?='' OR INSTR(LOWER(facts), ?) > 0 OR id=?)
)"""


def filters(reviewed, severity, search):
    return search, reviewed, severity, severity, search, search, search


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def facts_for(row):
    return explain({"incident_id": row["id"], "observed_at": timestamp(row["timestamp"]),
                    **json.loads(row["facts"])})


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
            rows = db.execute(_matching + " SELECT * FROM matching ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
                (*filters(reviewed, severity, search), limit + 1, offset)).fetchall()
        incidents = []
        for row in rows[:limit]:
            facts = json.loads(row["facts"])
            incidents.append({
                "id": row["id"], "event": facts["event"], "severity": row["severity"],
                "source": facts.get("agent") or facts.get("service") or facts.get("component"),
                "observed_at": timestamp(row["timestamp"]),
                "occurrences": row["occurrences"], "reviewed": row["reviewed_at"] is not None,
                "summary": explain(facts)["summary"],
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

    def _selection(self, db, *, ids, reviewed, severity, search):
        if ids is not None:
            rows = db.execute(f"""SELECT * FROM ({_events})
                WHERE id IN (SELECT value FROM json_each(?)) ORDER BY timestamp DESC, id DESC""",
                (json.dumps(ids),)).fetchall()
            if len(rows) != len(set(ids)):
                raise ValueError("One or more selected incidents are unavailable. Refresh the journal and try again.")
            return rows
        rows = db.execute(_matching + " SELECT * FROM matching ORDER BY timestamp DESC, id DESC LIMIT ?",
            (*filters(reviewed, severity, search), MAX_SELECTION + 1)).fetchall()
        if len(rows) > MAX_SELECTION:
            raise ValueError(f"Selection exceeds {MAX_SELECTION} incidents. Narrow the filters and try again.")
        return rows

    def selection(self, **scope):
        with self.journal.connect() as db:
            rows = self._selection(db, **scope)
        return {"incidents": [{"id": row["id"], "assessment_state": assessment_for(row)[0]} for row in rows]}

    def review_selection(self, *, mark_reviewed, **scope):
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = self._selection(db, **scope)
            ids = json.dumps([row["id"] for row in rows])
            if mark_reviewed:
                changed = db.execute("""INSERT INTO incident_inspections(id, reviewed_at)
                    SELECT value, ? FROM json_each(?) WHERE TRUE
                    ON CONFLICT(id) DO UPDATE SET reviewed_at=excluded.reviewed_at
                    WHERE incident_inspections.reviewed_at IS NULL""", (time.time(), ids))
            else:
                changed = db.execute("""UPDATE incident_inspections SET reviewed_at=NULL
                    WHERE reviewed_at IS NOT NULL AND id IN (SELECT value FROM json_each(?))""", (ids,))
            return {"matched": len(rows), "changed": changed.rowcount}

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
        return render_report(incident["facts"], incident["assessment"])

    def bulk_pdf(self, *, ids, reviewed, severity, search):
        with self.journal.connect() as db:
            if ids is not None:
                rows = self._selection(db, ids=ids, reviewed=reviewed, severity=severity, search=search)
            else:
                rows = db.execute(_matching + """ SELECT * FROM events
                    WHERE fingerprint IN (SELECT fingerprint FROM matching)
                    ORDER BY timestamp DESC, id DESC LIMIT ?""",
                    (*filters(reviewed, severity, search), MAX_EXPORT + 1)).fetchall()
        if len(rows) > MAX_EXPORT:
            raise ValueError(f"Export exceeds {MAX_EXPORT} occurrences. Narrow the filters or select specific incidents.")
        return render_reports([(facts_for(row), assessment_for(row)[1]) for row in rows], bulk=True) if rows else None
