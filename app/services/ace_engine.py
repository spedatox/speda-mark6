"""ACE: Adaptive Cognitive Engine — contextual online policy learning for Operation Jarvis.

Design invariants:
1. Interpretable regularized online logistic regression: P(y=1 | x) = sigmoid(theta^T x)
   with L2 shrinkage toward conservative prior theta_0 and per-feature AdaGrad damping.
2. Strictly separates 4 feedback dimensions: action_usefulness, notification_timing,
   duplication, and task_completion. Dismissing timing, snoozing, duplicates, or
   missing/ignored interactions NEVER falsely penalize action usefulness.
3. Atomic, idempotent, concurrency-safe, restart-safe training: every feedback event
   carries a unique idempotency_key and updates its policy at most once inside one
   ACID write_session transaction with optimistic version fencing.
4. Hard safety boundary: ACE outputs continuous scores only; it can NEVER modify
   permissions, L0-L3 ceilings, delegation leases, or spending limits.
5. Contextual bandits are explicitly disabled in Phase 0-2; deterministic propensity-1
   logs are never used to claim valid IPS/DR counterfactual estimates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import write_session
from app.models.jarvis import (
    ACEPolicy, ACEPolicySnapshot, ACETrainingEvent, JarvisInitiative,
)

# Conservative priors (theta_0) — safe, non-intrusive initialization
USEFUL_PRIOR: dict[str, float] = {
    "bias": -0.20,
    "intention_confirmed": 0.85,
    "intention_inferred": 0.25,
    "intention_speculative": -1.50,
    "grounding_confidence": 0.70,
    "evidence_count_norm": 0.40,
    "urgency_high": 0.65,
    "urgency_medium": 0.30,
    "est_time_saved_norm": 0.55,
    "domain_accept_rate_30d": 0.50,
    "recent_dismiss_streak_norm": -0.60,
    "prep_silent_hisar": 0.35,
    "prep_draft_stage": 0.25,
}

WELCOME_NOW_PRIOR: dict[str, float] = {
    "bias": -0.45,
    "urgency_high": 1.10,
    "urgency_medium": 0.20,
    "in_calendar_event": -0.95,
    "active_chat_last_10m": -0.50,
    "is_quiet_hours": -2.50,
    "is_weekend": -0.20,
    "pushes_sent_today_norm": -0.80,
    "intention_confirmed": 0.45,
    "domain_accept_rate_30d": 0.30,
    "recent_dismiss_streak_norm": -0.75,
}

ALLOWED_ACTOR_AUTH = frozenset({"owner_session", "owner_telegram", "system_outcome"})

# Explicit 4-dimension feedback mapping:
# signal_name -> (source_category, feedback_dimension, target_head, reward, weight)
FEEDBACK_SPEC: dict[str, tuple[str, str, str, float | None, float]] = {
    # 1. Action Usefulness (explicit + outcome + behavioral)
    "approved": ("explicit", "action_usefulness", "useful", 1.0, 1.00),
    "marked_useful": ("explicit", "action_usefulness", "useful", 1.0, 1.00),
    "approved_with_edit": ("explicit", "action_usefulness", "useful", 0.70, 0.85),
    "dismissed_not_useful": ("explicit", "action_usefulness", "useful", 0.0, 1.00),
    "marked_not_useful": ("explicit", "action_usefulness", "useful", 0.0, 1.00),
    "action_undone": ("outcome", "action_usefulness", "useful", 0.0, 0.90),
    "prep_corrected": ("outcome", "action_usefulness", "useful", 0.30, 0.80),
    "artifact_opened": ("behavioral", "action_usefulness", "useful", 0.80, 0.35),
    "artifact_abandoned": ("behavioral", "action_usefulness", "useful", 0.30, 0.25),
    # 2. Task Completion (outcome)
    "task_completed": ("outcome", "task_completion", "useful", 1.0, 0.85),
    "artifact_referenced": ("outcome", "task_completion", "useful", 1.0, 0.85),
    # 3. Notification Timing (decoupled from action usefulness!)
    "timing_welcome": ("explicit", "notification_timing", "welcome_now", 1.0, 1.00),
    "snoozed": ("explicit", "notification_timing", "welcome_now", 0.15, 0.80),
    "dismissed_bad_timing": ("explicit", "notification_timing", "welcome_now", 0.0, 1.00),
    "marked_annoying_timing": ("explicit", "notification_timing", "welcome_now", 0.0, 1.00),
    # 4. Duplication (logged for dedupe telemetry; NEVER penalizes useful!)
    "dismissed_duplicate": ("explicit", "duplication", "none", None, 0.0),
    # 5. Missing / Unseen (strictly censored; NEVER treated as negative proof!)
    "ignored": ("censored", "notification_timing", "none", None, 0.0),
    "expired_unseen": ("censored", "action_usefulness", "none", None, 0.0),
}


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-min(z, 60.0))
        return 1.0 / (1.0 + ez)
    ez = math.exp(max(z, -60.0))
    return ez / (1.0 + ez)


def _clip(val: float, lo: float = -3.5, hi: float = 3.5) -> float:
    return max(lo, min(hi, val))


def _prior_for(model_head: str) -> dict[str, float]:
    if model_head == "useful":
        return dict(USEFUL_PRIOR)
    if model_head == "welcome_now":
        return dict(WELCOME_NOW_PRIOR)
    raise ValueError(f"Unsupported ACE model_head: {model_head}")


@dataclass(frozen=True)
class PolicyScore:
    probability: float
    logit: float
    policy_scope: str
    model_head: str
    policy_version: int
    frozen: bool
    top_contributors: list[tuple[str, float]]


@dataclass(frozen=True)
class ArmDecision:
    chosen_arm: str
    policy_mode: str
    action_score: float
    interruption_score: float
    explanation: str


class ACEPolicyEngine:
    """Persistent, versioned, atomic contextual online-learning engine."""

    def __init__(
        self,
        sessions,
        *,
        base_lr: float = 0.12,
        l2_lambda: float = 0.04,
        damping_c: float = 8.0,
        brier_drift_threshold: float = 0.38,
        bandit_enabled: bool = False,
    ):
        self.sessions = sessions
        self.base_lr = base_lr
        self.l2_lambda = l2_lambda
        self.damping_c = damping_c
        self.brier_drift_threshold = brier_drift_threshold
        # Refinement #1: Contextual bandits remain strictly disabled until a defensible evaluation exists.
        self.bandit_enabled = False
        if bandit_enabled:
            raise ValueError(
                "Contextual bandits are disabled in Phase 0-2; deterministic logs cannot support valid IPS/DR evaluation."
            )

    async def _get_or_init_policy(self, db, user_id: int, policy_scope: str, model_head: str) -> ACEPolicy:
        row = (await db.execute(select(ACEPolicy).where(
            ACEPolicy.user_id == user_id,
            ACEPolicy.policy_scope == policy_scope,
            ACEPolicy.model_head == model_head,
        ))).scalar_one_or_none()
        if row is not None:
            return row
        prior = _prior_for(model_head)
        now = datetime.now(timezone.utc)
        row = ACEPolicy(
            id=uuid4().hex,
            user_id=user_id,
            policy_scope=policy_scope,
            model_head=model_head,
            version=1,
            weights_json=dict(prior),
            prior_json=dict(prior),
            counts_json={k: 0 for k in prior},
            explicit_locks_json={},
            frozen=False,
            rolling_brier=None,
            brier_window_json=[],
            updated_at=now,
        )
        db.add(row)
        await db.flush()
        db.add(ACEPolicySnapshot(
            policy_id=row.id,
            version=1,
            weights_json=dict(prior),
            delta_summary=f"Initialized conservative prior for {policy_scope}/{model_head}.",
            created_at=now,
        ))
        await db.flush()
        return row

    @staticmethod
    def _predict_with_weights(weights: dict[str, float], features: dict[str, float]) -> tuple[float, float, list[tuple[str, float]]]:
        logit = float(weights.get("bias", 0.0))
        contribs: list[tuple[str, float]] = []
        for k, x_val in features.items():
            if k == "bias":
                continue
            w_val = float(weights.get(k, 0.0))
            if w_val == 0.0 or x_val == 0.0:
                continue
            c = w_val * float(x_val)
            logit += c
            contribs.append((k, round(c, 4)))
        contribs.sort(key=lambda item: -abs(item[1]))
        return _sigmoid(logit), logit, contribs[:5]

    async def score(self, user_id: int, policy_scope: str, model_head: str, features: dict[str, float], *, db=None) -> PolicyScore:
        if db is None:
            async with self.sessions() as own:
                return await self.score(user_id, policy_scope, model_head, features, db=own)
        row = (await db.execute(select(ACEPolicy).where(
            ACEPolicy.user_id == user_id,
            ACEPolicy.policy_scope == policy_scope,
            ACEPolicy.model_head == model_head,
        ))).scalar_one_or_none()
        weights = dict(row.weights_json) if row else _prior_for(model_head)
        prob, logit, top = self._predict_with_weights(weights, features)
        return PolicyScore(
            probability=round(prob, 4),
            logit=round(logit, 4),
            policy_scope=policy_scope,
            model_head=model_head,
            policy_version=row.version if row else 0,
            frozen=bool(row.frozen) if row else False,
            top_contributors=top,
        )

    def select_arm(
        self,
        *,
        action_score: float,
        interruption_score: float,
        allowed_arms: tuple[str, ...] = ("silent_prepare", "stage", "notify", "defer", "ignore"),
        mode: Literal["deterministic_threshold"] = "deterministic_threshold",
    ) -> ArmDecision:
        """Deterministic threshold interface (contextual bandit arm exploration explicitly disabled)."""
        if mode != "deterministic_threshold":
            raise ValueError("Only deterministic_threshold mode is permitted; contextual bandits are disabled.")
        if action_score < 0.55 or "ignore" in allowed_arms and len(allowed_arms) == 1:
            return ArmDecision("ignore", "deterministic_threshold", action_score, interruption_score, "Below action usefulness threshold.")
        if interruption_score >= 0.70 and "notify" in allowed_arms:
            return ArmDecision("notify", "deterministic_threshold", action_score, interruption_score, "High action usefulness and welcome timing.")
        if interruption_score >= 0.40 and "defer" in allowed_arms:
            return ArmDecision("defer", "deterministic_threshold", action_score, interruption_score, "Useful action; deferring notification to better window.")
        if "stage" in allowed_arms:
            return ArmDecision("stage", "deterministic_threshold", action_score, interruption_score, "Useful action; staging quietly in Initiative Inbox.")
        if "silent_prepare" in allowed_arms:
            return ArmDecision("silent_prepare", "deterministic_threshold", action_score, interruption_score, "Executing silent background preparation.")
        return ArmDecision("ignore", "deterministic_threshold", action_score, interruption_score, "No permitted arm available.")

    @staticmethod
    def evaluate_offpolicy_bandit(logged_events: list[dict]) -> None:
        """Refinement #1 guard: explicitly reject invalid IPS/DR claims over deterministic logs."""
        raise ValueError(
            "Off-policy IPS/DR evaluation is invalid on deterministic propensity-1 logs. "
            "Contextual bandits remain disabled until a statistically defensible exploration strategy exists."
        )

    async def record_feedback_and_update(
        self,
        *,
        user_id: int,
        initiative_id: str,
        idempotency_key: str,
        signal_name: str,
        actor_auth: str,
        note: str | None = None,
    ) -> dict:
        """Atomically record authenticated feedback and apply at-most-once online policy update."""
        if actor_auth not in ALLOWED_ACTOR_AUTH:
            raise PermissionError(f"Untrusted actor_auth {actor_auth!r} cannot submit ACE training feedback")
        if signal_name not in FEEDBACK_SPEC:
            raise ValueError(f"Unknown ACE feedback signal: {signal_name!r}")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1..128 characters")

        source_category, dimension, target_head, reward, weight = FEEDBACK_SPEC[signal_name]
        now = datetime.now(timezone.utc)

        async with write_session(self.sessions) as db:
            init_row = await db.get(JarvisInitiative, initiative_id)
            if not init_row or init_row.user_id != user_id:
                raise KeyError("Initiative not found for this owner")

            # Idempotent insert: guarantees at-most-once training per (user_id, idempotency_key)
            insert_fn = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
            res = await db.execute(insert_fn(ACETrainingEvent).values(
                user_id=user_id,
                idempotency_key=idempotency_key,
                initiative_id=initiative_id,
                policy_scope=init_row.policy_scope,
                source_category=source_category,
                feedback_dimension=dimension,
                signal_name=signal_name,
                target_head=target_head,
                reward=reward,
                weight=weight,
                applied=False,
                actor_auth=actor_auth,
                note=(note or "")[:500] or None,
                created_at=now,
            ).on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"]))
            if res.rowcount != 1:
                return {"status": "duplicate_ignored", "applied": False, "idempotency_key": idempotency_key}

            event = (await db.execute(select(ACETrainingEvent).where(
                ACETrainingEvent.user_id == user_id,
                ACETrainingEvent.idempotency_key == idempotency_key,
            ))).scalar_one()

            # Censored / duplicate / zero-weight signals are logged cleanly without modifying weights
            if target_head == "none" or reward is None or weight <= 0.0:
                return {
                    "status": "logged_no_weight_update",
                    "applied": False,
                    "feedback_dimension": dimension,
                    "source_category": source_category,
                    "event_id": event.id,
                }

            policy = await self._get_or_init_policy(db, user_id, init_row.policy_scope, target_head)
            if policy.frozen:
                return {
                    "status": "policy_frozen",
                    "applied": False,
                    "policy_scope": policy.policy_scope,
                    "model_head": target_head,
                    "freeze_reason": policy.freeze_reason,
                    "event_id": event.id,
                }

            features = dict(init_row.feature_vector_json or {})
            weights = dict(policy.weights_json or _prior_for(target_head))
            prior = dict(policy.prior_json or _prior_for(target_head))
            counts = dict(policy.counts_json or {})
            locks = dict(policy.explicit_locks_json or {})

            p_hat, _, _ = self._predict_with_weights(weights, features)
            error = float(reward) - p_hat

            # Regularized online logistic update with prior shrinkage and per-feature count damping
            new_weights = dict(weights)
            deltas: list[tuple[str, float]] = []
            active_features = {"bias": 1.0, **{k: float(v) for k, v in features.items() if k in prior}}
            for fname, x_val in active_features.items():
                if fname in locks:
                    continue  # Explicit owner preference lock cannot be overwritten by behavioral updates
                n_prev = int(counts.get(fname, 0))
                eta = self.base_lr / math.sqrt(1.0 + n_prev / self.damping_c)
                old_w = float(weights.get(fname, prior.get(fname, 0.0)))
                p0 = float(prior.get(fname, 0.0))
                updated_w = _clip(old_w + eta * weight * error * x_val - eta * self.l2_lambda * (old_w - p0))
                new_weights[fname] = round(updated_w, 6)
                counts[fname] = n_prev + 1
                diff = updated_w - old_w
                if abs(diff) >= 1e-4:
                    deltas.append((fname, diff))

            # Track rolling Brier score for drift auto-freeze
            brier = (p_hat - float(reward)) ** 2
            window = [*list(policy.brier_window_json or [])[-19:], round(brier, 5)]
            rolling_brier = round(sum(window) / len(window), 5)
            auto_frozen = False
            freeze_reason = policy.freeze_reason
            if len(window) >= 12 and rolling_brier > self.brier_drift_threshold:
                auto_frozen = True
                freeze_reason = f"drift_detected_brier_{rolling_brier:.3f}"

            deltas.sort(key=lambda item: -abs(item[1]))
            top_delta_str = ", ".join(f"{k} ({d:+.4f})" for k, d in deltas[:3]) or "minimal adjustment"
            summary = f"Signal '{signal_name}' ({dimension}, r={reward:.2f}, p_hat={p_hat:.2f}) adjusted {top_delta_str}."

            expected_ver = policy.version
            new_ver = expected_ver + 1
            updated_rows = await db.execute(update(ACEPolicy).where(
                ACEPolicy.id == policy.id,
                ACEPolicy.version == expected_ver,
            ).values(
                version=new_ver,
                weights_json=new_weights,
                counts_json=counts,
                rolling_brier=rolling_brier,
                brier_window_json=window,
                frozen=True if auto_frozen else policy.frozen,
                freeze_reason=freeze_reason,
                updated_at=now,
            ))
            if updated_rows.rowcount != 1:
                raise RuntimeError("Concurrent ACE policy update conflict; transaction rolled back cleanly.")

            db.add(ACEPolicySnapshot(
                policy_id=policy.id,
                version=new_ver,
                weights_json=new_weights,
                delta_summary=summary,
                trigger_event_id=event.id,
                created_at=now,
            ))
            event.applied = True
            event.policy_version_after = new_ver
            await db.flush()

            return {
                "status": "updated",
                "applied": True,
                "event_id": event.id,
                "policy_scope": policy.policy_scope,
                "model_head": target_head,
                "feedback_dimension": dimension,
                "policy_version": new_ver,
                "p_hat_before": round(p_hat, 4),
                "rolling_brier": rolling_brier,
                "auto_frozen": auto_frozen,
                "delta_summary": summary,
            }

    async def inspect_policies(self, user_id: int) -> list[dict]:
        async with self.sessions() as db:
            rows = (await db.execute(select(ACEPolicy).where(
                ACEPolicy.user_id == user_id,
            ).order_by(ACEPolicy.policy_scope, ACEPolicy.model_head))).scalars().all()
            out = []
            for r in rows:
                snaps = (await db.execute(select(ACEPolicySnapshot).where(
                    ACEPolicySnapshot.policy_id == r.id,
                ).order_by(ACEPolicySnapshot.version.desc()).limit(5))).scalars().all()
                out.append({
                    "id": r.id,
                    "policy_scope": r.policy_scope,
                    "model_head": r.model_head,
                    "version": r.version,
                    "frozen": r.frozen,
                    "freeze_reason": r.freeze_reason,
                    "rolling_brier": r.rolling_brier,
                    "weights": r.weights_json,
                    "recent_changes": [
                        {"version": s.version, "summary": s.delta_summary, "created_at": s.created_at.isoformat()}
                        for s in snaps
                    ],
                })
            return out

    async def set_frozen(self, user_id: int, policy_scope: str, model_head: str, *, frozen: bool, reason: str | None = None) -> dict:
        async with write_session(self.sessions) as db:
            policy = await self._get_or_init_policy(db, user_id, policy_scope, model_head)
            policy.frozen = frozen
            policy.freeze_reason = reason if frozen else None
            policy.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return {"policy_scope": policy_scope, "model_head": model_head, "frozen": policy.frozen, "version": policy.version}

    async def reset_policy(self, user_id: int, policy_scope: str, model_head: str) -> dict:
        now = datetime.now(timezone.utc)
        async with write_session(self.sessions) as db:
            policy = await self._get_or_init_policy(db, user_id, policy_scope, model_head)
            prior = dict(policy.prior_json or _prior_for(model_head))
            policy.weights_json = prior
            policy.counts_json = {k: 0 for k in prior}
            policy.brier_window_json = []
            policy.rolling_brier = None
            policy.frozen = False
            policy.freeze_reason = None
            policy.version += 1
            policy.updated_at = now
            db.add(ACEPolicySnapshot(
                policy_id=policy.id,
                version=policy.version,
                weights_json=prior,
                delta_summary="Owner reset policy weights to conservative prior.",
                created_at=now,
            ))
            await db.flush()
            return {"status": "reset", "policy_scope": policy_scope, "model_head": model_head, "version": policy.version}

    async def rollback_policy(self, user_id: int, policy_scope: str, model_head: str, target_version: int) -> dict:
        now = datetime.now(timezone.utc)
        async with write_session(self.sessions) as db:
            policy = await self._get_or_init_policy(db, user_id, policy_scope, model_head)
            snap = (await db.execute(select(ACEPolicySnapshot).where(
                ACEPolicySnapshot.policy_id == policy.id,
                ACEPolicySnapshot.version == target_version,
            ))).scalar_one_or_none()
            if snap is None:
                raise KeyError(f"Snapshot version {target_version} not found")
            policy.weights_json = dict(snap.weights_json)
            policy.frozen = False
            policy.freeze_reason = None
            policy.version += 1
            policy.updated_at = now
            db.add(ACEPolicySnapshot(
                policy_id=policy.id,
                version=policy.version,
                weights_json=dict(snap.weights_json),
                delta_summary=f"Owner rolled back policy to snapshot v{target_version}.",
                created_at=now,
            ))
            await db.flush()
            return {"status": "rolled_back", "restored_from": target_version, "version": policy.version}
