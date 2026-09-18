# Orion Spark

**Orion Spark** is a lightweight reliability extension of Orion designed to keep watch over Speda Mark VI from outside its primary runtime.

Normally, Orion operates as part of Speda and therefore dies with the rest of the system if the main application crashes. Spark is the exception: a small independent service that remains operational even when Speda, IGOR, FORGE, the agents, or the MCP layer are completely unavailable.

Its responsibility is simple:

> **Detect when Speda goes down, attempt safe recovery, and notify the owner regardless of whether recovery succeeds.**

Spark is not an AI agent and does not depend on an LLM. Recovery remains strictly deterministic, bounded, and predictable.

---

## Architectural Invariants

1. **Host-level isolation**: Spark runs as an independent host service (via `systemd`) outside the Speda Docker Compose stack.
2. **Zero code dependency**: Spark must never import or depend on Speda application code (`packages/igor`, `app.*`, etc.).
3. **Speda dead invariant**: Speda may be completely dead or uninstalled while Orion Spark remains operational and reporting.
4. **Independent notification**: Spark communicates directly with notification endpoints (Telegram Bot API, Webhooks). It never routes messages through Speda's normal agent or tool infrastructure.
5. **Bounded recovery**: Spark enforces attempt limits and cooldowns. It will never create an infinite restart or rollback loop.

---

## Health Monitoring

Spark continuously observes Speda using five distinct signals:

```text
Container state       (running, restarting, exited, dead)
/health/live          (confirms application process exists)
/health/ready         (confirms Speda is capable of serving requests)
Restart count         (crash-loop thrashing detection)
Current deployed SHA  (active Git commit)
```

Probes are evaluated consecutively. Spark requires a configurable number of consecutive failures (default: 3) before confirming an outage, preventing reactions to transient network hiccups or brief startup transitions.

---

## Defibrillation Protocol

Once an outage is confirmed, Spark initiates the Defibrillation Protocol:

```text
Failure detected
      │
      ▼
Confirm outage (consecutive failures >= threshold)
      │
      ▼
Restart Speda (docker compose restart app)
      │
      ▼
Verify health
   │       │
healthy   failed
   │       │
   ▼       ▼
 DONE    Check deployment & LKG
              │
              ▼
      Restore Last Known Good (git checkout <lkg>)
              │
              ▼
         Verify again
          │       │
        healthy  failed
          │       │
          ▼       ▼
        DONE    CRITICAL
```

1. **Step 1 — Restart**: Spark first executes the least destructive action (`docker compose restart app`).
2. **Step 2 — Verify**: Waits for startup grace window and checks `/health/live` and `/health/ready`. If healthy, logs incident and notifies owner.
3. **Step 3 — Rollback to LKG**: If restart fails and candidate revision differs from the Last Known Good (LKG), Spark quarantines the defective revision and checks out the LKG commit, then rebuilds/restarts the stack.
4. **Step 4 — Verify or Escalate**: If rollback succeeds, logs incident and notifies owner. If rollback also fails, escalates immediately to **CRITICAL** notification requesting manual intervention.

---

## Last Known Good (LKG) & Quarantining

Spark does not assume that the previous Git commit is safe. Instead, it manages verified states:

```text
candidate → deploy → health checks → stability window (5 min) → LKG
```

- When a new revision is deployed, it enters the stability window as a **candidate**.
- If the candidate remains healthy throughout the stability window, it is promoted to **Last Known Good (LKG)**.
- If the candidate crashes or fails probes during startup or the stability window, it is marked **FAILED / QUARANTINED** and Spark rolls back to the previous LKG.
- Quarantined revisions cannot be accepted or re-deployed automatically by Spark.

---

## Incident Reporting & Independent Notifications

Every outage generates a structured JSON incident record saved to `incidents.jsonl`:

```json
{
  "service": "speda",
  "type": "deployment_failure",
  "failed_revision": "a91bc72",
  "restored_revision": "83ce21f",
  "recovery": "rollback",
  "result": "success",
  "downtime_seconds": 47,
  "timestamp": "2026-09-18T14:30:00Z",
  "details": "Probe failed 3 times: /health/ready returned 503"
}
```

Notifications are formatted cleanly for Telegram and webhooks:

**On Rollback Recovery Success:**
```text
ORION SPARK

Speda went down after deployment a91bc72.

Automatic rollback succeeded.
Restored revision: 83ce21f
Downtime: 47 seconds.
```

**On Restart Recovery Success:**
```text
ORION SPARK

Speda went down.

Automatic restart succeeded.
Active revision: 83ce21f
Downtime: 22 seconds.
```

**On Critical Failure:**
```text
ORION SPARK — CRITICAL

Speda is down.

Restart failed.
Rollback failed.

Current revision: a91bc72
Last Known Good: 83ce21f

Manual intervention required.
```

---

## CLI Usage

Install or run locally with Python:

```bash
# Continuous watchdog monitor loop
python -m orion_spark run

# Single health probe check
python -m orion_spark check

# View status (LKG, candidate, quarantined builds, outage state)
python -m orion_spark status

# Manually trigger defibrillation protocol
python -m orion_spark defibrillate

# View recent incidents
python -m orion_spark incidents --limit 10

# View or update Last Known Good revision
python -m orion_spark lkg
python -m orion_spark lkg --set <commit_sha>

# Manage quarantined revisions
python -m orion_spark quarantine
python -m orion_spark quarantine --add <commit_sha> --reason "Syntax error in startup router"
python -m orion_spark quarantine --remove <commit_sha>
```

---

## Systemd Host Deployment

1. Copy `packages/orion-spark/spark.example.env` to `/opt/speda/spark.env` and populate Telegram / Webhook credentials.
2. Copy `packages/orion-spark/orion-spark.service` to `/etc/systemd/system/orion-spark.service`:
   ```bash
   sudo cp packages/orion-spark/orion-spark.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable orion-spark
   sudo systemctl start orion-spark
   ```
3. Check service status:
   ```bash
   sudo systemctl status orion-spark
   sudo journalctl -u orion-spark -f
   ```
