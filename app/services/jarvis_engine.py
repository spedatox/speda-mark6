"""Operation Jarvis Phase 0: Prospective Memory, Local Cue Engine, Decoupled Gates & Shadow Mode.

Reuses Iacon, Coordinator, GlobalBudget patterns, and ACEPolicyEngine.
Phase 0 strictly runs in Shadow Mode (zero autonomous tool execution, zero proactive push notifications).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import write_session
from app.models.iacon import IaconRecord, IaconWork
from app.models.jarvis import (
    JarvisActionLease, JarvisAllowance, JarvisControl, JarvisGoal, JarvisInitiative,
)
from app.services.ace_engine import ACEPolicyEngine
from app.services.memory_store import digest

logger = logging.getLogger(__name__)


def _live_execution_allowed() -> bool:
    """Phase 0 server-side hard lock: live L0-L3 execution stays disabled unless explicitly unlocked."""
    return os.getenv("SPEDA_JARVIS_ALLOW_LIVE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}

INTENTION_TIERS = frozenset({"confirmed_commitment", "inferred_intention", "speculative_interest"})
DOMAINS = frozenset({"academic", "siberay", "engineering", "health", "travel", "comms", "finance", "personal"})

# Action kind -> (base_tier, technically_reversible, downstream_irreversible)
ACTION_CLASSIFICATION: dict[str, tuple[str, bool, bool]] = {
    "hisar_prepared_doc": ("L0", True, False),
    "research_brief": ("L0", True, False),
    "conflict_analysis": ("L0", True, False),
    "draft_email_reply": ("L1", True, False),
    "staged_calendar_diff": ("L1", True, False),
    "staged_course_diff": ("L1", True, False),
    "file_to_speda": ("L2", True, False),
    "tentative_personal_hold": ("L2", True, False),
    "provisional_state": ("L2", True, False),
    "send_email": ("L3", False, True),
    "send_external_message": ("L3", False, True),
    "modify_shared_calendar": ("L3", True, True),  # Technically reversible, but externally visible!
    "write_attendance_ledger": ("L3", True, True),
    "host_ops": ("L3", False, True),
    "delete_data": ("L3", False, True),
    "financial_action": ("L3", False, True),
}

# Conservative initial ACE Action Gate thresholds per tier
ACTION_THRESHOLDS: dict[str, float] = {
    "L0": 0.50,
    "L1": 0.60,
    "L2": 0.80,
    "L3": 0.75,
}


@dataclass(frozen=True)
class AutonomyAssessment:
    tier: str
    technically_reversible: bool
    downstream_irreversible: bool
    authorized: bool
    requires_owner_approval: bool
    reason: str


@dataclass(frozen=True)
class CandidateCue:
    dedupe_key: str
    goal_id: str | None
    domain: str
    assigned_agent: str
    title: str
    rationale: str
    action_kind: str
    intention_tier: str
    grounding_confidence: float
    evidence_count: int
    hours_to_deadline: float | None
    est_time_saved_min: float
    estimated_microdollars: int
    source_authority: str  # "owner_confirmed" | "iacon_state" | "untrusted_evidence"
    external_visibility: bool = False
    action_plan: dict | None = None


def classify_autonomy(
    *,
    action_kind: str,
    intention_tier: str,
    source_authority: str,
    external_visibility: bool = False,
    has_matching_lease: bool = False,
) -> AutonomyAssessment:
    """Deterministic autonomy classifier: ACE can NEVER override or relax this logic."""
    base_tier, tech_rev, downstream_irrev = ACTION_CLASSIFICATION.get(action_kind, ("L3", False, True))
    if external_visibility:
        downstream_irrev = True
        base_tier = "L3"

    # Rule 1: Speculative interests NEVER become actionable initiatives
    if intention_tier == "speculative_interest":
        return AutonomyAssessment(
            tier=base_tier, technically_reversible=tech_rev, downstream_irreversible=downstream_irrev,
            authorized=False, requires_owner_approval=True,
            reason="Speculative interest cannot be auto-promoted into an actionable initiative.",
        )

    # Rule 2: Untrusted evidence (emails, webpages, sensors) is evidence ONLY — never authorization for L2/L3!
    if source_authority == "untrusted_evidence" and base_tier in {"L2", "L3"}:
        return AutonomyAssessment(
            tier="L3" if downstream_irrev else base_tier,
            technically_reversible=tech_rev, downstream_irreversible=downstream_irrev,
            authorized=False, requires_owner_approval=True,
            reason="Untrusted external evidence cannot authorize L2/L3 execution without owner confirmation.",
        )

    # Rule 3: Inferred intentions are restricted to L0 Silent Prep and L1 Staging until owner-confirmed
    if intention_tier == "inferred_intention" and base_tier in {"L2", "L3"}:
        return AutonomyAssessment(
            tier=base_tier, technically_reversible=tech_rev, downstream_irreversible=downstream_irrev,
            authorized=False, requires_owner_approval=True,
            reason="Inferred intention is limited to L0/L1 until confirmed by owner.",
        )

    # Rule 4: L3 actions with downstream irreversibility always require explicit owner approval or scoped lease
    if base_tier == "L3" or downstream_irrev:
        return AutonomyAssessment(
            tier="L3", technically_reversible=tech_rev, downstream_irreversible=True,
            authorized=has_matching_lease, requires_owner_approval=not has_matching_lease,
            reason="Authorized via active scoped Delegation Lease." if has_matching_lease else "External/irreversible L3 action requires 1-tap owner approval.",
        )

    return AutonomyAssessment(
        tier=base_tier, technically_reversible=tech_rev, downstream_irreversible=False,
        authorized=True, requires_owner_approval=False,
        reason=f"Authorized at {base_tier} (owner-private, reversible).",
    )


class JarvisBudgetDenied(RuntimeError):
    def __init__(self, message: str, *, code: str = "jarvis_budget_denied"):
        super().__init__(message)
        self.code = code


class JarvisEngine:
    """Operation Jarvis Phase 0 Executive Engine + ACE Integration."""

    def __init__(self, sessions, *, ace: ACEPolicyEngine | None = None, global_budget=None):
        self.sessions = sessions
        self.ace = ace or ACEPolicyEngine(sessions)
        self.global_budget = global_budget

    async def get_control(self, user_id: int, *, db=None) -> JarvisControl:
        if db is None:
            async with self.sessions() as own:
                return await self.get_control(user_id, db=own)
        row = await db.get(JarvisControl, user_id)
        if row is not None:
            return row
        return JarvisControl(
            user_id=user_id, version=0, enabled=True, shadow_mode=True,
            daily_microdollars=250000, daily_calls=20, daily_pushes=3,
            quiet_start_hour=22, quiet_end_hour=9,
        )

    async def save_control(
        self,
        user_id: int,
        *,
        enabled: bool | None = None,
        shadow_mode: bool | None = None,
        daily_microdollars: int | None = None,
        daily_calls: int | None = None,
        daily_pushes: int | None = None,
        quiet_start_hour: int | None = None,
        quiet_end_hour: int | None = None,
    ) -> dict:
        """Persist reboot-safe control & hard hierarchical budget policy."""
        if shadow_mode is False and not _live_execution_allowed():
            raise ValueError("Phase 0 Shadow Mode lock is active server-side; live autonomous execution is disabled.")
        now = datetime.now(timezone.utc)
        async with write_session(self.sessions) as db:
            row = await db.get(JarvisControl, user_id)
            if row is None:
                row = JarvisControl(
                    user_id=user_id, version=1,
                    enabled=True if enabled is None else bool(enabled),
                    shadow_mode=True if shadow_mode is None else bool(shadow_mode),
                    daily_microdollars=250000 if daily_microdollars is None else max(0, int(daily_microdollars)),
                    daily_calls=20 if daily_calls is None else max(0, int(daily_calls)),
                    daily_pushes=3 if daily_pushes is None else max(0, int(daily_pushes)),
                    quiet_start_hour=22 if quiet_start_hour is None else int(quiet_start_hour) % 24,
                    quiet_end_hour=9 if quiet_end_hour is None else int(quiet_end_hour) % 24,
                    updated_at=now,
                )
                db.add(row)
            else:
                if enabled is not None:
                    row.enabled = bool(enabled)
                if shadow_mode is not None:
                    row.shadow_mode = bool(shadow_mode)
                if daily_microdollars is not None:
                    row.daily_microdollars = max(0, int(daily_microdollars))
                if daily_calls is not None:
                    row.daily_calls = max(0, int(daily_calls))
                if daily_pushes is not None:
                    row.daily_pushes = max(0, int(daily_pushes))
                if quiet_start_hour is not None:
                    row.quiet_start_hour = int(quiet_start_hour) % 24
                if quiet_end_hour is not None:
                    row.quiet_end_hour = int(quiet_end_hour) % 24
                row.version += 1
                row.updated_at = now
            await db.flush()
            return {
                "user_id": row.user_id, "version": row.version,
                "enabled": row.enabled, "shadow_mode": row.shadow_mode,
                "daily_microdollars": row.daily_microdollars, "daily_calls": row.daily_calls,
                "daily_pushes": row.daily_pushes,
                "quiet_hours": [row.quiet_start_hour, row.quiet_end_hour],
            }

    async def reserve_tree_budget(
        self,
        user_id: int,
        *,
        timezone_name: str = "UTC",
        calls: int = 1,
        microdollars: int = 0,
        pushes: int = 0,
    ) -> dict:
        """Pessimistic hierarchical root-tree reservation covering executive + delegated specialist calls."""
        try:
            day = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        except (ValueError, ZoneInfoNotFoundError):
            day = datetime.now(timezone.utc).date().isoformat()

        async with write_session(self.sessions) as db:
            ctrl = await self.get_control(user_id, db=db)
            if not ctrl.enabled:
                raise JarvisBudgetDenied("Operation Jarvis is paused by owner kill switch.", code="paused")
            insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
            await db.execute(insert_fn(JarvisAllowance).values(
                user_id=user_id, day=day, calls=0, microdollars=0, pushes=0,
            ).on_conflict_do_nothing(index_elements=["user_id", "day"]))
            accepted = await db.execute(update(JarvisAllowance).where(
                JarvisAllowance.user_id == user_id,
                JarvisAllowance.day == day,
                JarvisAllowance.calls + calls <= ctrl.daily_calls,
                JarvisAllowance.microdollars + microdollars <= ctrl.daily_microdollars,
                JarvisAllowance.pushes + pushes <= ctrl.daily_pushes,
            ).values(
                calls=JarvisAllowance.calls + calls,
                microdollars=JarvisAllowance.microdollars + microdollars,
                pushes=JarvisAllowance.pushes + pushes,
            ))
            if accepted.rowcount != 1:
                raise JarvisBudgetDenied(
                    f"Hierarchical daily Jarvis budget exhausted (limit: ${ctrl.daily_microdollars/1_000_000:.2f}/day, {ctrl.daily_calls} calls/day, {ctrl.daily_pushes} pushes/day).",
                    code="daily_limit",
                )
            row = (await db.execute(select(JarvisAllowance).where(
                JarvisAllowance.user_id == user_id, JarvisAllowance.day == day,
            ))).scalar_one()
            return {
                "day": day, "calls_used": row.calls, "microdollars_used": row.microdollars,
                "pushes_used": row.pushes,
                "remaining_microdollars": ctrl.daily_microdollars - row.microdollars,
            }

    async def create_goal(
        self,
        user_id: int,
        *,
        title: str,
        domain: str,
        description: str = "",
        intention_tier: str = "inferred_intention",
        confidence: float = 0.6,
        priority: float = 0.5,
        deadline_at: datetime | None = None,
        cue_rules: dict | None = None,
        source_record_ids: list[str] | None = None,
        confirmed_by_owner: bool = False,
    ) -> dict:
        if intention_tier not in INTENTION_TIERS:
            raise ValueError(f"Invalid intention_tier: {intention_tier}")
        if domain not in DOMAINS:
            raise ValueError(f"Invalid domain: {domain}")
        if confirmed_by_owner:
            intention_tier = "confirmed_commitment"
        status = "awaiting_confirmation" if intention_tier == "speculative_interest" else "active"
        now = datetime.now(timezone.utc)
        async with write_session(self.sessions) as db:
            goal = JarvisGoal(
                id=uuid4().hex, user_id=user_id, title=title[:256], domain=domain,
                description=description[:2000], intention_tier=intention_tier,
                confidence=max(0.0, min(1.0, float(confidence))),
                status=status, priority=max(0.0, min(1.0, float(priority))),
                deadline_at=deadline_at, cue_rules_json=dict(cue_rules or {}),
                source_record_ids=list(source_record_ids or []),
                confirmed_by_owner=bool(confirmed_by_owner),
                created_at=now, updated_at=now,
            )
            db.add(goal)
            await db.flush()
            return {
                "id": goal.id, "title": goal.title, "domain": goal.domain,
                "intention_tier": goal.intention_tier, "status": goal.status,
                "deadline_at": goal.deadline_at.isoformat() if goal.deadline_at else None,
            }

    async def confirm_goal(self, user_id: int, goal_id: str) -> dict:
        """Explicit owner confirmation promotes an inferred/speculative item into confirmed_commitment."""
        async with write_session(self.sessions) as db:
            goal = await db.get(JarvisGoal, goal_id)
            if not goal or goal.user_id != user_id:
                raise KeyError("Goal not found")
            goal.intention_tier = "confirmed_commitment"
            goal.confirmed_by_owner = True
            goal.confidence = max(goal.confidence, 0.95)
            goal.status = "active"
            goal.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return {"id": goal.id, "intention_tier": goal.intention_tier, "status": goal.status}

    async def list_goals(self, user_id: int, *, status: str | None = None) -> list[dict]:
        async with self.sessions() as db:
            q = select(JarvisGoal).where(JarvisGoal.user_id == user_id)
            if status:
                q = q.where(JarvisGoal.status == status)
            rows = (await db.execute(q.order_by(JarvisGoal.priority.desc(), JarvisGoal.created_at.desc()).limit(50))).scalars().all()
            return [{
                "id": g.id, "title": g.title, "domain": g.domain,
                "intention_tier": g.intention_tier, "confidence": g.confidence,
                "status": g.status, "priority": g.priority,
                "deadline_at": g.deadline_at.isoformat() if g.deadline_at else None,
                "confirmed_by_owner": g.confirmed_by_owner,
                "source_record_ids": g.source_record_ids,
            } for g in rows]

    @staticmethod
    def _is_quiet_hours(local_hour: int, start_hour: int, end_hour: int) -> bool:
        if start_hour > end_hour:
            return local_hour >= start_hour or local_hour < end_hour
        return start_hour <= local_hour < end_hour

    @staticmethod
    def _build_features(
        cue: CandidateCue,
        *,
        is_quiet: bool,
        in_calendar_event: bool,
        active_chat_last_10m: bool,
        is_weekend: bool,
        pushes_sent_today: int,
        daily_push_cap: int,
    ) -> dict[str, float]:
        hrs = cue.hours_to_deadline
        return {
            "intention_confirmed": 1.0 if cue.intention_tier == "confirmed_commitment" else 0.0,
            "intention_inferred": 1.0 if cue.intention_tier == "inferred_intention" else 0.0,
            "intention_speculative": 1.0 if cue.intention_tier == "speculative_interest" else 0.0,
            "grounding_confidence": max(0.0, min(1.0, cue.grounding_confidence)),
            "evidence_count_norm": min(1.0, cue.evidence_count / 3.0),
            "urgency_high": 1.0 if (hrs is not None and hrs <= 24.0) else 0.0,
            "urgency_medium": 1.0 if (hrs is not None and 24.0 < hrs <= 72.0) else 0.0,
            "est_time_saved_norm": min(1.0, cue.est_time_saved_min / 30.0),
            "domain_accept_rate_30d": 0.5,
            "recent_dismiss_streak_norm": 0.0,
            "prep_silent_hisar": 1.0 if cue.action_kind in {"hisar_prepared_doc", "research_brief", "conflict_analysis"} else 0.0,
            "prep_draft_stage": 1.0 if cue.action_kind in {"draft_email_reply", "staged_calendar_diff", "staged_course_diff"} else 0.0,
            "in_calendar_event": 1.0 if in_calendar_event else 0.0,
            "active_chat_last_10m": 1.0 if active_chat_last_10m else 0.0,
            "is_quiet_hours": 1.0 if is_quiet else 0.0,
            "is_weekend": 1.0 if is_weekend else 0.0,
            "pushes_sent_today_norm": min(1.0, pushes_sent_today / max(1, daily_push_cap)),
        }

    async def scan_local_cues(
        self,
        user_id: int,
        *,
        now_utc: datetime | None = None,
        external_events: list[dict] | None = None,
    ) -> list[CandidateCue]:
        """Zero-cost ($0.00) deterministic local delta & cue scanner over goals, Iacon states, and events."""
        now = now_utc or datetime.now(timezone.utc)
        cues: list[CandidateCue] = []

        async with self.sessions() as db:
            goals = (await db.execute(select(JarvisGoal).where(
                JarvisGoal.user_id == user_id,
                JarvisGoal.status.in_(("active", "awaiting_confirmation")),
            ))).scalars().all()
            states = (await db.execute(select(IaconRecord).where(
                IaconRecord.user_id == user_id,
                IaconRecord.kind == "state",
                IaconRecord.status == "accepted",
            ).limit(40))).scalars().all()
            observed_works = (await db.execute(select(IaconWork).where(
                IaconWork.user_id == user_id,
                IaconWork.kind == "observed_state",
                IaconWork.status == "observed",
            ).limit(20))).scalars().all()

        # 1. Goal deadline cues (72h / 24h / 4h windows)
        for g in goals:
            if g.deadline_at is None:
                continue
            dl = g.deadline_at if g.deadline_at.tzinfo else g.deadline_at.replace(tzinfo=timezone.utc)
            hrs = (dl - now).total_seconds() / 3600.0
            if hrs < 0 or hrs > 72.0:
                continue
            bucket = "4h" if hrs <= 4.0 else ("24h" if hrs <= 24.0 else "72h")
            agent = "ultron" if g.domain == "academic" else ("atomix" if g.domain == "health" else "speda")
            action_kind = "hisar_prepared_doc" if bucket == "72h" else "staged_calendar_diff"
            cues.append(CandidateCue(
                dedupe_key=f"goal:{g.id}:{bucket}",
                goal_id=g.id,
                domain=g.domain,
                assigned_agent=agent,
                title=f"Prepare for {g.title} ({int(round(hrs))}h remaining)",
                rationale=f"Deadline window ({bucket}) triggered for {g.intention_tier} goal '{g.title}'.",
                action_kind=action_kind,
                intention_tier=g.intention_tier,
                grounding_confidence=g.confidence,
                evidence_count=max(1, len(g.source_record_ids or [])),
                hours_to_deadline=hrs,
                est_time_saved_min=25.0,
                estimated_microdollars=1500,
                source_authority="owner_confirmed" if g.confirmed_by_owner else "iacon_state",
                action_plan={"goal_id": g.id, "action_kind": action_kind, "bucket": bucket},
            ))

        # 2. Active Iacon situational states (starts_on / ends_on / review_on within 72h)
        for st in states:
            p = st.payload or {}
            if p.get("closed_on"):
                continue
            target_iso = p.get("starts_on") or p.get("ends_on") or p.get("review_on")
            cue_label = "starts" if p.get("starts_on") else ("ends" if p.get("ends_on") else "review_due")
            if not target_iso:
                continue
            try:
                st_dt = datetime.fromisoformat(str(target_iso)[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            hrs = (st_dt - now).total_seconds() / 3600.0
            if -24.0 <= hrs <= 72.0:
                bucket = "overdue" if hrs < 0 else ("24h" if hrs <= 24.0 else "72h")
                cues.append(CandidateCue(
                    dedupe_key=f"state:{st.id}:v{st.version}:{cue_label}:{bucket}",
                    goal_id=None,
                    domain="personal",
                    assigned_agent="speda",
                    title=f"Situational prep ({cue_label}): {st.title}",
                    rationale=f"Iacon state '{st.title}' ({cue_label}) is {int(round(hrs))}h from now.",
                    action_kind="conflict_analysis" if hrs >= 0 else "hisar_prepared_doc",
                    intention_tier="confirmed_commitment",
                    grounding_confidence=0.85,
                    evidence_count=max(1, len(st.evidence or [])),
                    hours_to_deadline=max(0.0, hrs),
                    est_time_saved_min=15.0,
                    estimated_microdollars=1000,
                    source_authority="iacon_state",
                    action_plan={"state_id": st.id, "cue_label": cue_label, "bucket": bucket},
                ))

        # 2b. Observed provisional states awaiting confirmation
        for ow in observed_works:
            op = ow.payload or {}
            st_p = op.get("state") or {}
            if st_p.get("closed_on"):
                continue
            target_iso = st_p.get("starts_on") or st_p.get("ends_on") or st_p.get("review_on")
            if not target_iso:
                continue
            try:
                st_dt = datetime.fromisoformat(str(target_iso)[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            hrs = (st_dt - now).total_seconds() / 3600.0
            if -12.0 <= hrs <= 72.0:
                title = str(op.get("title") or "Observed situation")
                cues.append(CandidateCue(
                    dedupe_key=f"observed:{ow.id}:{str(target_iso)[:10]}",
                    goal_id=None,
                    domain="personal",
                    assigned_agent="speda",
                    title=f"Provisional state check: {title}",
                    rationale=f"Observed provisional state '{title}' is {int(round(hrs))}h away; L0/L1 prep only.",
                    action_kind="hisar_prepared_doc",
                    intention_tier="inferred_intention",
                    grounding_confidence=0.72,
                    evidence_count=1,
                    hours_to_deadline=max(0.0, hrs),
                    est_time_saved_min=15.0,
                    estimated_microdollars=1000,
                    source_authority="iacon_state",
                    action_plan={"observed_work_id": ow.id, "action_kind": "hisar_prepared_doc"},
                ))

        # 3. External untrusted sensor / mail_watch events (strictly tagged untrusted_evidence!)
        for ev in (external_events or []):
            ev_id = str(ev.get("event_id") or digest(str(ev))[:16])
            domain = str(ev.get("domain") or "comms")
            if domain not in DOMAINS:
                domain = "comms"
            requested_action = str(ev.get("suggested_action_kind") or "draft_email_reply")
            cues.append(CandidateCue(
                dedupe_key=f"event:{ev_id}",
                goal_id=ev.get("goal_id"),
                domain=domain,
                assigned_agent=str(ev.get("assigned_agent") or "speda"),
                title=str(ev.get("title") or "Incoming event triage")[:256],
                rationale=str(ev.get("rationale") or "External event cue (untrusted evidence)."),
                action_kind=requested_action,
                intention_tier=str(ev.get("intention_tier") or "confirmed_commitment"),
                grounding_confidence=float(ev.get("confidence", 0.75)),
                evidence_count=1,
                hours_to_deadline=ev.get("hours_to_deadline"),
                est_time_saved_min=float(ev.get("est_time_saved_min", 15.0)),
                estimated_microdollars=int(ev.get("estimated_microdollars", 1200)),
                source_authority="untrusted_evidence",
                external_visibility=bool(ev.get("external_visibility", False)),
                action_plan={"event_id": ev_id, "action_kind": requested_action, "untrusted_evidence": True},
            ))

        return cues

    async def evaluate_and_record_cue(
        self,
        user_id: int,
        cue: CandidateCue,
        *,
        timezone_name: str = "UTC",
        now_utc: datetime | None = None,
        in_calendar_event: bool = False,
        active_chat_last_10m: bool = False,
    ) -> dict:
        """Run independent Action Gate and Interruption Gate, then record Shadow initiative."""
        now = now_utc or datetime.now(timezone.utc)
        try:
            local_now = now.astimezone(ZoneInfo(timezone_name))
        except (ValueError, ZoneInfoNotFoundError):
            local_now = now
        day = local_now.date().isoformat()
        policy_scope = f"jarvis:{cue.domain}"

        async with self.sessions() as db:
            ctrl = await self.get_control(user_id, db=db)
            if not ctrl.enabled:
                return {"status": "paused", "dedupe_key": cue.dedupe_key}

            existing = (await db.execute(select(JarvisInitiative.id).where(
                JarvisInitiative.user_id == user_id,
                JarvisInitiative.dedupe_key == cue.dedupe_key,
            ))).scalar_one_or_none()
            if existing is not None:
                return {"status": "duplicate_skipped", "dedupe_key": cue.dedupe_key, "initiative_id": existing}

            allowance = (await db.execute(select(JarvisAllowance).where(
                JarvisAllowance.user_id == user_id, JarvisAllowance.day == day,
            ))).scalar_one_or_none()
            pushes_today = allowance.pushes if allowance else 0
            microdollars_used = allowance.microdollars if allowance else 0
            calls_used = allowance.calls if allowance else 0

            # Check active scoped Delegation Leases
            leases = (await db.execute(select(JarvisActionLease).where(
                JarvisActionLease.user_id == user_id,
                JarvisActionLease.domain == cue.domain,
                JarvisActionLease.revoked_at.is_(None),
            ))).scalars().all()
            has_lease = any(
                (l.expires_at is None or (l.expires_at if l.expires_at.tzinfo else l.expires_at.replace(tzinfo=timezone.utc)) > now)
                and cue.action_kind in (l.allowed_tools or [])
                for l in leases
            )

            is_quiet = self._is_quiet_hours(local_now.hour, ctrl.quiet_start_hour, ctrl.quiet_end_hour)
            features = self._build_features(
                cue,
                is_quiet=is_quiet,
                in_calendar_event=in_calendar_event,
                active_chat_last_10m=active_chat_last_10m,
                is_weekend=local_now.weekday() >= 5,
                pushes_sent_today=pushes_today,
                daily_push_cap=ctrl.daily_pushes,
            )

            # --- GATE 1: ACTION GATE (Completely independent of interruption cost!) ---
            autonomy = classify_autonomy(
                action_kind=cue.action_kind,
                intention_tier=cue.intention_tier,
                source_authority=cue.source_authority,
                external_visibility=cue.external_visibility,
                has_matching_lease=has_lease,
            )
            act_score = await self.ace.score(user_id, policy_scope, "useful", features, db=db)
            int_score = await self.ace.score(user_id, policy_scope, "welcome_now", features, db=db)

        budget_ok = (
            (microdollars_used + cue.estimated_microdollars <= ctrl.daily_microdollars)
            and (calls_used + 1 <= ctrl.daily_calls)
        )
        threshold = ACTION_THRESHOLDS.get(autonomy.tier, 0.75)

        if not budget_ok:
            action_decision = "reject"
            status = "rejected_budget"
        elif cue.intention_tier == "speculative_interest":
            action_decision = "reject"
            status = "rejected_speculative"
        elif not autonomy.authorized and not autonomy.requires_owner_approval:
            action_decision = "reject"
            status = "rejected_policy"
        elif cue.source_authority == "untrusted_evidence" and autonomy.tier in {"L2", "L3"}:
            action_decision = "reject"
            status = "rejected_untrusted_auth"
        elif act_score.probability < threshold:
            action_decision = "reject"
            status = "rejected_low_utility"
        else:
            if autonomy.tier == "L0":
                action_decision = "execute_l0"
            elif autonomy.tier == "L1":
                action_decision = "stage_l1"
            elif autonomy.tier == "L2" and autonomy.authorized:
                action_decision = "execute_l2"
            else:
                action_decision = "await_l3"
            # Phase 0 server-side invariant: shadow_mode is enforced unless explicitly unlocked.
            effective_shadow = bool(ctrl.shadow_mode) or (not _live_execution_allowed())
            status = "shadow" if effective_shadow else action_decision

        # --- GATE 2: INTERRUPTION GATE (Independent decision: notify_now | defer_window | stage_inbox | silent_only) ---
        if action_decision == "reject":
            interruption_decision = "silent_only"
        elif is_quiet or pushes_today >= ctrl.daily_pushes:
            # Quiet hours or push cap NEVER block L0/L1 Action Gate; they only defer/silence notification!
            interruption_decision = "defer_window" if autonomy.tier in {"L1", "L3"} else "silent_only"
        elif autonomy.tier == "L0":
            interruption_decision = "silent_only"
        elif int_score.probability >= 0.70 and (cue.hours_to_deadline is not None and cue.hours_to_deadline <= 24.0):
            interruption_decision = "notify_now"
        elif int_score.probability >= 0.40:
            interruption_decision = "defer_window"
        else:
            interruption_decision = "stage_inbox"

        async with write_session(self.sessions) as db:
            init_row = JarvisInitiative(
                id=uuid4().hex,
                user_id=user_id,
                goal_id=cue.goal_id,
                dedupe_key=cue.dedupe_key,
                policy_scope=policy_scope,
                assigned_agent=cue.assigned_agent,
                tier=autonomy.tier,
                downstream_irreversible=autonomy.downstream_irreversible,
                title=cue.title,
                rationale=f"{cue.rationale} | {autonomy.reason}",
                action_plan_json=dict(cue.action_plan or {}),
                artifact_paths=[],
                feature_vector_json=features,
                policy_mode="deterministic_threshold",
                action_score=act_score.probability,
                interruption_score=int_score.probability,
                action_decision=action_decision,
                interruption_decision=interruption_decision,
                status=status,
                estimated_microdollars=cue.estimated_microdollars,
                actual_microdollars=0,  # Shadow Mode: $0.00 actual execution spend
                created_at=now,
                updated_at=now,
            )
            db.add(init_row)
            await db.flush()
            return {
                "status": status,
                "initiative_id": init_row.id,
                "dedupe_key": cue.dedupe_key,
                "tier": autonomy.tier,
                "downstream_irreversible": autonomy.downstream_irreversible,
                "action_score": act_score.probability,
                "interruption_score": int_score.probability,
                "action_decision": action_decision,
                "interruption_decision": interruption_decision,
                "shadow_mode": ctrl.shadow_mode,
                "executed_tools": 0,
                "pushed_notifications": 0,
            }

    async def run_pulse(
        self,
        user_id: int,
        *,
        timezone_name: str = "UTC",
        now_utc: datetime | None = None,
        external_events: list[dict] | None = None,
        in_calendar_event: bool = False,
        active_chat_last_10m: bool = False,
    ) -> dict:
        """n8n-triggered Phase 0 pulse: zero-cost local pre-filter + Shadow Mode evaluation."""
        t0 = perf_counter()
        ctrl = await self.get_control(user_id)
        if not ctrl.enabled:
            return {"status": "paused", "elapsed_ms": round((perf_counter() - t0) * 1000.0, 2), "evaluated": 0}

        cues = await self.scan_local_cues(user_id, now_utc=now_utc, external_events=external_events)
        if not cues:
            return {
                "status": "no_material_cues",
                "shadow_mode": ctrl.shadow_mode,
                "elapsed_ms": round((perf_counter() - t0) * 1000.0, 2),
                "evaluated": 0,
                "actual_microdollars": 0,
            }

        results = []
        for cue in cues[:12]:
            res = await self.evaluate_and_record_cue(
                user_id, cue, timezone_name=timezone_name, now_utc=now_utc,
                in_calendar_event=in_calendar_event, active_chat_last_10m=active_chat_last_10m,
            )
            results.append(res)

        return {
            "status": "ok",
            "shadow_mode": ctrl.shadow_mode,
            "elapsed_ms": round((perf_counter() - t0) * 1000.0, 2),
            "evaluated": len(results),
            "shadow_recorded": sum(1 for r in results if r["status"] == "shadow"),
            "duplicates_skipped": sum(1 for r in results if r["status"] == "duplicate_skipped"),
            "rejected": sum(1 for r in results if str(r["status"]).startswith("rejected")),
            "actual_microdollars": 0,
            "items": results,
        }

    async def list_initiatives(self, user_id: int, *, status: str | None = None, limit: int = 50) -> list[dict]:
        async with self.sessions() as db:
            q = select(JarvisInitiative).where(JarvisInitiative.user_id == user_id)
            if status:
                q = q.where(JarvisInitiative.status == status)
            rows = (await db.execute(q.order_by(JarvisInitiative.created_at.desc()).limit(min(limit, 100)))).scalars().all()
            return [{
                "id": r.id, "goal_id": r.goal_id, "dedupe_key": r.dedupe_key,
                "policy_scope": r.policy_scope, "assigned_agent": r.assigned_agent,
                "tier": r.tier, "downstream_irreversible": r.downstream_irreversible,
                "title": r.title, "rationale": r.rationale,
                "action_score": r.action_score, "interruption_score": r.interruption_score,
                "action_decision": r.action_decision, "interruption_decision": r.interruption_decision,
                "policy_mode": r.policy_mode, "status": r.status,
                "estimated_microdollars": r.estimated_microdollars,
                "actual_microdollars": r.actual_microdollars,
                "created_at": r.created_at.isoformat(),
            } for r in rows]
