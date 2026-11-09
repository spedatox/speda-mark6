"""Durable incident capture and owner-only delivery."""
import asyncio
import json
import linecache
import logging
import re
import sqlite3
import time
from collections import deque
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from hashlib import sha256
from uuid import uuid4

from app.models.delivery import TelegramLink, TelegramOwner
from app.schemas.llm import ProviderError
from app.services.incident_details import clean_message, explain, log_details, stack_locations

logger = logging.getLogger(__name__)
_reporting = ContextVar("damage_control_reporting", default=False)
_identifier = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}")
_trace_id = re.compile(r"[a-f0-9-]{32,36}")
_service_name = re.compile(r"speda(?:-[a-z0-9]+)*|onyx-app|yanki(?:-pot-provider)?")
_release_check = re.compile(r"speda-pg-(?:restore|app-check)-[a-f0-9]{12}")
_tool_failure = re.compile(r"^(?:[a-z_]+: (?:request|lookup|search) failed|"
                           r"Document generation failed|Delivery failed|The browser could not load that page)")


def turn_facts(context):
    facts = {}
    for field, value in {
        "agent": context.agent_id, "role": context.task_role,
        "worker": context.extra.get("incident_worker_role", context.extra.get("worker_role")),
        "model": context.model, "trigger": context.triggered_by,
    }.items():
        if isinstance(value, str) and _identifier.fullmatch(value):
            facts[field] = value
    for field in ("request_id", "run_id", "step_id"):
        value = getattr(context, field)
        if isinstance(value, str) and _trace_id.fullmatch(value):
            facts[field] = value
    if type(context.session_id) is int and context.session_id > 0:
        facts["session_id"] = context.session_id
    return facts


def exception_facts(exc):
    facts = {"exception": type(exc).__name__}
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _identifier.fullmatch(code):
        facts["code"] = code
    if isinstance(exc, ProviderError) and (message := clean_message(str(exc))):
        facts["diagnostic_message"] = message
    cause = exc.__cause__ or exc
    trace = cause.__traceback__
    if trace:
        while trace.tb_next:
            trace = trace.tb_next
        facts["site"] = f"{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}"
        facts["cause_type"] = type(cause).__name__
        facts["stack"] = "\n".join(stack_locations(cause.__traceback__))
    return facts


def tool_error_details(result):
    if not isinstance(result, str):
        return None
    if _tool_failure.match(result):
        return {"diagnostic_message": clean_message(result.split("\n", 1)[0])}
    if not result.startswith("{"):
        return None
    try:
        payload = json.loads(result)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if not (payload.get("isError") is True or payload.get("is_error") is True
            or status in ("failed", "error", "unavailable", "unknown")):
        return None
    details = {"tool_status": status if status in ("failed", "error", "unavailable", "unknown") else "error"}
    for key in ("error", "message", "detail", "reason"):
        if message := clean_message(payload.get(key)):
            details["diagnostic_message"] = message
            break
    return details


class IncidentJournal:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY, timestamp REAL NOT NULL, facts TEXT NOT NULL,
                status TEXT NOT NULL, analysis TEXT, next_attempt REAL NOT NULL DEFAULT 0,
                telegram_message_id INTEGER
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS incident_pending ON incidents(status, next_attempt, timestamp)")
            db.execute("CREATE TABLE IF NOT EXISTS docker_cursor (id INTEGER PRIMARY KEY CHECK (id=1), timestamp INTEGER)")
            db.execute("""CREATE TABLE IF NOT EXISTS incident_inspections (
                id TEXT PRIMARY KEY, reviewed_at REAL, assessment_state TEXT, assessment TEXT, assessed_at REAL
            )""")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=0.5)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def add(self, facts, status="pending", *, identity=None):
        identity = identity or uuid4().hex
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO incidents(id, timestamp, facts, status) VALUES (?, ?, ?, ?)",
                       (identity, time.time(), json.dumps(facts), status))
        return identity

    def finish(self, identity, event=None, details=None):
        with self.connect() as db:
            if not event:
                db.execute("DELETE FROM incidents WHERE id=? AND status='active'", (identity,))
                return
            row = db.execute("SELECT facts, timestamp FROM incidents WHERE id=? AND status='active'", (identity,)).fetchone()
            if row:
                started = datetime.fromtimestamp(row["timestamp"], timezone.utc).isoformat(timespec="seconds")
                facts = {**json.loads(row["facts"]), **(details or {}), "event": event, "ended_turn": True,
                         "started_at": started, "duration_ms": round(max(0, time.time() - row["timestamp"]) * 1000)}
                db.execute("UPDATE incidents SET facts=?, status='pending', timestamp=? WHERE id=? AND status='active'",
                           (json.dumps(facts), time.time(), identity))

    def recover(self):
        with self.connect() as db:
            active = list(db.execute("SELECT id FROM incidents WHERE status='active'"))
            db.execute("UPDATE incidents SET status='unknown' WHERE status='sending'")
            db.execute("UPDATE incidents SET status='ready', analysis=? WHERE status='analysing'",
                       ("Model analysis was interrupted. The recorded observations follow.",))
        for row in active:
            self.finish(row["id"], "backend_stopped_with_active_execution")

    def next(self):
        with self.connect() as db:
            return db.execute("""SELECT * FROM incidents WHERE status IN ('pending', 'ready')
                AND next_attempt <= ? ORDER BY COALESCE(json_extract(facts, '$.ended_turn'), 0) DESC,
                timestamp, id LIMIT 1""", (time.time(),)).fetchone()

    def transition(self, identity, previous, status, *, analysis=None, remote_id=None, delay=0):
        with self.connect() as db:
            return db.execute("""UPDATE incidents SET status=?, next_attempt=?,
                analysis=COALESCE(?, analysis), telegram_message_id=COALESCE(?, telegram_message_id)
                WHERE id=? AND status=?""",
                (status, time.time() + delay, analysis, remote_id, identity, previous)).rowcount == 1

    def docker_cursor(self, timestamp=None):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO docker_cursor VALUES (1, ?)", (time.time_ns(),))
            if timestamp is not None:
                db.execute("UPDATE docker_cursor SET timestamp=MAX(timestamp, ?) WHERE id=1", (timestamp,))
            return db.execute("SELECT timestamp FROM docker_cursor WHERE id=1").fetchone()[0]


class DamageControl(logging.Handler):
    def __init__(self, path):
        super().__init__(logging.WARNING)
        self.journal = IncidentJournal(path)
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self._release_check_kills = deque(maxlen=32)

    def capture(self, facts, *, identity=None, active=False):
        try:
            identity = self.journal.add(facts, "active" if active else "pending", identity=identity)
        except (OSError, sqlite3.Error):
            logger.error("damage_control_capture_failed", exc_info=True)
            return None
        if not active and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.wake.set)
        return identity

    def begin(self, context):
        return self.capture(turn_facts(context), active=True)

    def finish(self, identity, event=None, **details):
        if identity is None:
            return
        try:
            self.journal.finish(identity, event, details)
        except (OSError, sqlite3.Error):
            logger.error("damage_control_finish_failed", exc_info=True)
        if event:
            self.wake.set()

    def emit(self, record):
        if _reporting.get() or getattr(record, "damage_control_handled", False):
            return
        if record.name == __name__ or not record.name.startswith(("app.", "forge.", "uvicorn.", "asyncio")):
            return
        if record.levelno < logging.ERROR and not record.name.startswith(("app.", "forge.")):
            return
        record.damage_control_handled = True
        facts = {"event": "runtime_log", "component": record.name, "severity": record.levelname,
                 "site": f"{record.filename}:{record.lineno}"}
        # Retain literal event names only. Interpolated logs and exception text may contain private data.
        if isinstance(record.msg, str) and _identifier.fullmatch(record.msg) and "_" in record.msg:
            source = "".join(linecache.getline(record.pathname, line)
                             for line in range(record.lineno, record.lineno + 4))
            if repr(record.msg) in source or f'"{record.msg}"' in source:
                facts["event"] = record.msg
        if record.exc_info and record.exc_info[1]:
            facts.update(exception_facts(record.exc_info[1]))
        facts.update(log_details(facts["event"], record.__dict__))
        for field in ("request_id", "run_id", "step_id", "job_id"):
            value = getattr(record, field, None)
            if isinstance(value, str) and _trace_id.fullmatch(value):
                facts[field] = value
        for field in ("agent", "role", "worker", "tool", "error_code"):
            value = getattr(record, field, None)
            if isinstance(value, str) and _identifier.fullmatch(value):
                facts[field] = value
        self.capture(facts)

    def observe_container(self, data):
        action = data["Action"]
        timestamp = data["timeNano"]
        attributes = data["Actor"]["Attributes"]
        name = attributes.get("name", "")
        if not isinstance(name, str) or not _service_name.fullmatch(name):
            return
        if type(timestamp) is not int or timestamp < self.journal.docker_cursor():
            return
        event = {"oom": "service_out_of_memory", "health_status: unhealthy": "service_unhealthy"}.get(action)
        identity = data["Actor"]["ID"]
        if _release_check.fullmatch(name) and action == "kill" and attributes.get("signal") == "9":
            self._release_check_kills.append((identity, timestamp))
        if action == "die" and attributes.get("exitCode") not in ("0", "143"):
            # The deployment controller removes disposable checks with docker rm -f.
            release_cleanup = attributes.get("exitCode") == "137" and any(
                actor == identity and 0 <= timestamp - killed_at <= 30_000_000_000
                for actor, killed_at in self._release_check_kills
            )
            if not release_cleanup:
                event = "service_unexpected_exit"
        if event:
            identity = sha256(f"docker:{identity}:{timestamp}:{action}".encode()).hexdigest()[:32]
            facts = {"event": event, "service": name,
                     "service_event_at": datetime.fromtimestamp(timestamp / 1e9, timezone.utc).isoformat(timespec="seconds")}
            exit_code = attributes.get("exitCode", "")
            if isinstance(exit_code, str) and re.fullmatch(r"[0-9]{1,3}", exit_code):
                facts["exit_code"] = int(exit_code)
            if not self.capture(facts, identity=identity):
                return
        self.journal.docker_cursor(timestamp)

    async def observe_docker(self):
        """Read container lifecycle signals over the already mounted Docker socket."""
        unavailable = False
        while True:
            process = None
            try:
                stamp = self.journal.docker_cursor() // 1_000_000_000
                process = await asyncio.create_subprocess_exec(
                    "docker", "events", "--filter", "type=container", "--since", str(stamp),
                    "--format", "{{json .}}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                async for line in process.stdout:
                    self.observe_container(json.loads(line))
                    unavailable = False
            except (OSError, ValueError, KeyError, sqlite3.Error):
                pass
            finally:
                if process and process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    await process.communicate()
            if not unavailable:
                self.capture({"event": "container_event_monitor_disconnected"})
                unavailable = True
            await asyncio.sleep(15)

    async def run(self, analyse, deliveries):
        token = _reporting.set(True)
        try:
            while True:
                self.wake.clear()
                try:
                    row = self.journal.next()
                    if row:
                        await self.process(row, analyse, deliveries)
                        await asyncio.sleep(0)
                        continue
                except Exception:
                    logger.error("damage_control_worker_failed", exc_info=True)
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=10)
                except TimeoutError:
                    pass
        finally:
            _reporting.reset(token)

    async def process(self, row, analyse, deliveries):
        identity = row["id"]
        facts = {"incident_id": identity,
                 "observed_at": datetime.fromtimestamp(row["timestamp"], timezone.utc).isoformat(timespec="seconds"),
                 **json.loads(row["facts"])}
        if row["status"] == "pending":
            if not self.journal.transition(identity, "pending", "analysing"):
                return
            try:
                async with asyncio.timeout(45):
                    analysis = await analyse(explain(facts))
            except Exception as exc:
                # Keep the incident deliverable during a provider outage or a budget pause.
                analysis = f"Model analysis unavailable ({type(exc).__name__}). Review the recorded observations."
            self.journal.transition(identity, "analysing", "ready", analysis=analysis)
        else:
            analysis = row["analysis"]
        try:
            async with deliveries.sessions() as db:
                principal = await db.get(TelegramOwner, 1)
                link = await db.get(TelegramLink, (1, "speda"))
            configured_owner = deliveries.config.telegram_owner_id
            if (not principal or not link or link.chat_id != principal.sender_id
                    or (configured_owner and configured_owner != principal.sender_id)
                    or not deliveries.token("speda")):
                raise ValueError("Owner Telegram route is unavailable")
            report = await asyncio.to_thread(render_report, facts, analysis)
        except Exception as exc:
            logger.warning("damage_control_delivery_preparation_failed", extra={"error_type": type(exc).__name__})
            self.journal.transition(identity, "ready", "ready", delay=60)
            return
        if not self.journal.transition(identity, "ready", "sending"):
            return
        try:
            title = "Damage Control Protocol"
            if facts.get("ended_turn"):
                title += " | Turn ended"
            source = facts.get("agent") or facts.get("service")
            event = facts["event"].replace("_", " ")
            caption = f"{title}\n{source + ' | ' if source else ''}{event}\n{identity}"
            result = await deliveries.telegram("speda", "sendDocument", {"chat_id": link.chat_id, "caption": caption},
                files={"document": (f"damage-control-{identity}.pdf", report, "application/pdf")})
            remote_id = result.get("message_id") if isinstance(result, dict) else None
            if type(remote_id) is not int:
                raise ValueError("Telegram did not return a message ID")
        except BaseException as exc:
            if isinstance(exc, ProviderError) and exc.code == "telegram_throttled":
                self.journal.transition(identity, "sending", "ready", delay=60)
                return
            self.journal.transition(identity, "sending", "unknown")
            if not isinstance(exc, Exception):
                raise
            logger.warning("damage_control_delivery_unknown", extra={"incident_id": identity})
        else:
            self.journal.transition(identity, "sending", "sent", remote_id=remote_id)


def render_report(facts, analysis):
    return render_reports([(facts, analysis)])


def render_reports(records, *, bulk=False):
    # Keep WeasyPrint's layout and memory bounded when a journal spans hundreds of pages.
    if bulk and len(records) > 40:
        from io import BytesIO
        from pypdf import PdfWriter

        writer = PdfWriter()
        for start in range(0, len(records), 40):
            chunk = records[start:start + 40]
            subtitle = f"{len(records)} incident occurrences | Records {start + 1} to {start + len(chunk)}"
            writer.append(BytesIO(_render_sections(chunk, subtitle)), import_outline=False)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    return _render_sections(records, f"{len(records)} incident occurrences" if bulk else "")


def _render_sections(records, subtitle):
    from html import escape
    from weasyprint import HTML, URLFetcher

    sections = []
    for facts, analysis in records:
        details = explain(facts)
        title = details.pop("summary")
        next_check = details.pop("next_check")
        entries = "".join(f"<p><strong>{escape(key.replace('_', ' ').title())}</strong> {escape(str(value))}</p>"
                          for key, value in details.items() if value is not None)
        assessment = f"<h3>Model assessment</h3><p>{escape(analysis)}</p>" if analysis else ""
        sections.append(f"""<section><h2>{escape(title)}</h2><div class="facts">{entries}</div>
          <p><strong>Next check</strong> {escape(next_check)}</p>{assessment}</section>""")
    count = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    return HTML(string=f"""<!doctype html>
    <html lang="en"><meta charset="utf-8"><title>Damage Control Protocol</title>
    <style>
      @page {{ size: A4; margin: 20mm; @bottom-right {{ content: "SPEDA"; font-size: 8pt; }} }}
      body {{ font: 11pt/1.5 'DejaVu Sans', sans-serif; color: #172436; overflow-wrap: anywhere; }}
      header {{ border-bottom: 2pt solid #a43736; margin-bottom: 22pt; }}
      h1 {{ font-size: 24pt; margin: 0 0 9pt; }}
      h2 {{ font-size: 13pt; margin: 12pt 0 6pt; break-after: avoid; }}
      h3 {{ font-size: 10pt; margin: 7pt 0 3pt; break-after: avoid; }}
      section {{ border-bottom: 0.5pt solid #d0d8e0; margin-bottom: 12pt; padding-bottom: 9pt; }}
      .facts {{ margin: 0 0 6pt; font-size: 9pt; }}
      .facts strong {{ color: #516074; }}
      p {{ white-space: pre-wrap; margin: 0; }}
    </style>
    <body>
    <header><h1>Damage Control Protocol</h1>{count}</header>
    {''.join(sections)}</body>
    </html>""", url_fetcher=URLFetcher(allowed_protocols=())).write_pdf()
