# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deployment state and Last Known Good (LKG) manager for Orion Spark.

Manages:
- Active deployed revision
- Last Known Good (LKG) verified revision
- Candidate revisions undergoing trial in the stability window
- Quarantined failed revisions preventing deployment loops
- Outage state, consecutive failure tracking, and recovery limits
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("orion_spark.state")


@dataclass
class DeploymentState:
    active_revision: str | None = None
    last_known_good: str | None = None
    candidate_revision: str | None = None
    candidate_healthy_since: float | None = None
    quarantined_revisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Health & Outage tracking
    in_outage: bool = False
    outage_started_at: float | None = None
    consecutive_failures: int = 0

    # Defibrillation recovery attempt bounds
    last_recovery_at: float | None = None
    restart_attempts_count: int = 0
    rollback_attempts_count: int = 0


class DeploymentStateManager:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.json"
        self.state = DeploymentState()
        self._ensure_dir()
        self.load()

    def _ensure_dir(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create state directory {self.state_dir}: {e}")

    def load(self) -> DeploymentState:
        if self.state_file.is_file():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state = DeploymentState(**data)
                    logger.debug(f"Loaded state from {self.state_file}")
            except Exception as e:
                logger.error(f"Failed to load state from {self.state_file}: {e}")
        return self.state

    def save(self) -> None:
        self._ensure_dir()
        tmp_file = self.state_file.with_suffix(".tmp")
        try:
            data = asdict(self.state)
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")

    # ── Candidate & LKG Progression ──────────────────────────────────────────

    def record_probe_success(
        self,
        active_rev: str | None,
        stability_window_seconds: float,
    ) -> tuple[bool, str | None, int | None]:
        """
        Records a successful probe.
        Resets consecutive failures.
        Manages candidate evaluation across the stability window.
        Returns:
            (outage_recovered: bool, promoted_lkg_revision: str | None, downtime_seconds: int | None)
        """
        now = time.time()
        outage_recovered = False
        downtime_seconds = None
        promoted_revision = None

        if self.state.in_outage:
            outage_recovered = True
            if self.state.outage_started_at:
                downtime_seconds = max(1, int(now - self.state.outage_started_at))
            self.state.in_outage = False
            self.state.outage_started_at = None
            self.reset_recovery_counters()
            logger.info(f"Outage recovered! Downtime was {downtime_seconds}s")

        self.state.consecutive_failures = 0

        if active_rev:
            self.state.active_revision = active_rev

            # If no LKG exists yet, bootstrap it immediately
            if not self.state.last_known_good:
                self.state.last_known_good = active_rev
                promoted_revision = active_rev
                logger.info(f"Initial Last Known Good (LKG) set to active revision: {active_rev}")

            # If active matches LKG, clear any candidate
            elif active_rev == self.state.last_known_good:
                self.state.candidate_revision = None
                self.state.candidate_healthy_since = None

            # Active is different from LKG -> evaluate candidate
            else:
                if self.state.candidate_revision != active_rev:
                    self.state.candidate_revision = active_rev
                    self.state.candidate_healthy_since = now
                    logger.info(
                        f"Candidate revision {active_rev} entered stability window "
                        f"({stability_window_seconds}s)"
                    )
                else:
                    # Candidate has been healthy for some duration
                    healthy_duration = now - (self.state.candidate_healthy_since or now)
                    if healthy_duration >= stability_window_seconds:
                        self.state.last_known_good = active_rev
                        promoted_revision = active_rev
                        self.state.candidate_revision = None
                        self.state.candidate_healthy_since = None
                        logger.info(
                            f"Candidate {active_rev} satisfied stability window "
                            f"({healthy_duration:.1f}s >= {stability_window_seconds}s) "
                            f"-> PROMOTED to Last Known Good (LKG)"
                        )

        self.save()
        return outage_recovered, promoted_revision, downtime_seconds

    def record_probe_failure(
        self,
        active_rev: str | None,
        failure_threshold: int,
    ) -> bool:
        """
        Records a failed probe.
        Increments consecutive failures.
        Declares an outage when threshold is reached.
        Returns:
            outage_declared: bool (True only on the exact probe that crossed the threshold)
        """
        self.state.consecutive_failures += 1
        now = time.time()
        outage_declared = False

        if active_rev:
            self.state.active_revision = active_rev

        if self.state.consecutive_failures >= failure_threshold and not self.state.in_outage:
            self.state.in_outage = True
            self.state.outage_started_at = now
            outage_declared = True
            logger.warning(
                f"OUTAGE CONFIRMED: {self.state.consecutive_failures} consecutive failed probes "
                f"(threshold: {failure_threshold}). Speda is down."
            )

        self.save()
        return outage_declared

    # ── Quarantine & Revision Controls ───────────────────────────────────────

    def quarantine(self, revision: str, reason: str) -> None:
        """Quarantines a defective build so it cannot be deployed again."""
        short_rev = revision[:7] if len(revision) >= 7 else revision
        timestamp = datetime.now(timezone.utc).isoformat()
        self.state.quarantined_revisions[short_rev] = {
            "full_revision": revision,
            "reason": reason,
            "timestamp": timestamp,
        }
        if self.state.candidate_revision in (revision, short_rev):
            self.state.candidate_revision = None
            self.state.candidate_healthy_since = None
        logger.warning(f"Revision {short_rev} QUARANTINED: {reason}")
        self.save()

    def is_quarantined(self, revision: str | None) -> bool:
        if not revision:
            return False
        short_rev = revision[:7] if len(revision) >= 7 else revision
        return short_rev in self.state.quarantined_revisions or revision in self.state.quarantined_revisions

    def unquarantine(self, revision: str) -> bool:
        short_rev = revision[:7] if len(revision) >= 7 else revision
        removed = False
        if short_rev in self.state.quarantined_revisions:
            del self.state.quarantined_revisions[short_rev]
            removed = True
        if revision in self.state.quarantined_revisions:
            del self.state.quarantined_revisions[revision]
            removed = True
        if removed:
            logger.info(f"Revision {short_rev} removed from quarantine")
            self.save()
        return removed

    def set_lkg(self, revision: str) -> None:
        self.state.last_known_good = revision
        if self.state.candidate_revision == revision:
            self.state.candidate_revision = None
            self.state.candidate_healthy_since = None
        logger.info(f"Last Known Good manually set to {revision}")
        self.save()

    # ── Defibrillation Rate Limiting & Bounds ─────────────────────────────────

    def can_attempt_restart(self, max_attempts: int, cooldown_seconds: float) -> bool:
        if self.state.restart_attempts_count >= max_attempts:
            return False
        now = time.time()
        if self.state.last_recovery_at and (now - self.state.last_recovery_at < cooldown_seconds):
            return False
        return True

    def record_restart_attempt(self) -> None:
        self.state.restart_attempts_count += 1
        self.state.last_recovery_at = time.time()
        self.save()

    def can_attempt_rollback(self, max_attempts: int, cooldown_seconds: float) -> bool:
        if self.state.rollback_attempts_count >= max_attempts:
            return False
        if not self.state.last_known_good:
            return False
        now = time.time()
        if self.state.last_recovery_at and (now - self.state.last_recovery_at < cooldown_seconds):
            return False
        return True

    def record_rollback_attempt(self) -> None:
        self.state.rollback_attempts_count += 1
        self.state.last_recovery_at = time.time()
        self.save()

    def reset_recovery_counters(self) -> None:
        self.state.restart_attempts_count = 0
        self.state.rollback_attempts_count = 0
        self.save()
