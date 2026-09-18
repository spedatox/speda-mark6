# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Defibrillation Protocol implementation for Orion Spark.

Deterministic, bounded recovery engine:
1. Restart Speda
2. Verify health
   - If healthy -> DONE (recovery=restart, result=success)
   - If failed  -> Check deployment
3. Restore Last Known Good (LKG) revision
4. Verify health again
   - If healthy -> DONE (recovery=rollback, result=success)
   - If failed  -> CRITICAL escalation

Recovery attempts are strictly bounded to prevent restart or rollback loops.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from typing import Callable

from orion_spark.config import SparkConfig
from orion_spark.incidents import IncidentRecord, IncidentStore
from orion_spark.monitor import HealthMonitor, ProbeResult
from orion_spark.notifier import Notifier
from orion_spark.state import DeploymentStateManager

logger = logging.getLogger("orion_spark.recovery")


class Defibrillator:
    def __init__(
        self,
        config: SparkConfig,
        state_manager: DeploymentStateManager,
        incident_store: IncidentStore,
        notifier: Notifier,
        monitor: HealthMonitor,
    ) -> None:
        self.config = config
        self.state_manager = state_manager
        self.incident_store = incident_store
        self.notifier = notifier
        self.monitor = monitor

    def defibrillate(self, reason: str = "Outage detected") -> bool:
        """
        Executes the Defibrillation Protocol.
        Returns True if Speda was recovered to a healthy state, False otherwise.
        """
        logger.info(f"=== INITIATING DEFIBRILLATION PROTOCOL ({reason}) ===")
        state = self.state_manager.state
        active_rev = state.active_revision or self.monitor.get_current_revision()
        lkg_rev = state.last_known_good

        start_time = state.outage_started_at or time.time()

        # Check restart limits
        if not self.state_manager.can_attempt_restart(
            self.config.max_restart_attempts,
            self.config.recovery_cooldown_seconds,
        ):
            logger.warning(
                f"Restart attempt suppressed: attempts={state.restart_attempts_count}/"
                f"{self.config.max_restart_attempts}, cooldown={self.config.recovery_cooldown_seconds}s"
            )
            # If restart attempts exhausted, jump to rollback check or critical
            return self._attempt_rollback_phase(active_rev, lkg_rev, start_time, "Restart attempts exhausted")

        # ── Step 1: Restart Speda ────────────────────────────────────────────
        logger.info(f"Defibrillation Step 1: Restarting Speda service '{self.config.docker_service}'...")
        self.state_manager.record_restart_attempt()
        restart_ok = self._execute_restart()
        if not restart_ok:
            logger.error("Restart command execution failed.")

        # ── Step 2: Verify Health ────────────────────────────────────────────
        logger.info(f"Defibrillation Step 2: Verifying health after restart (waiting up to {self.config.startup_grace_period_seconds}s)...")
        probe_result = self._wait_for_health(timeout_seconds=self.config.startup_grace_period_seconds)

        if probe_result.healthy:
            logger.info("Restart recovery SUCCEEDED. Speda is healthy.")
            downtime = max(1, int(time.time() - start_time))
            record = IncidentRecord(
                service="speda",
                type="process_crash",
                failed_revision=active_rev,
                restored_revision=active_rev,
                recovery="restart",
                result="success",
                downtime_seconds=downtime,
                details=f"Restarted after {reason}. Health probes verified.",
            )
            self.incident_store.record(record)
            self.notifier.send(self.notifier.format_recovery_message(record))
            self.state_manager.record_probe_success(active_rev, self.config.stability_window_seconds)
            return True

        logger.warning(f"Restart verification FAILED: {probe_result.details}")

        # ── Step 3: Check Deployment & Restore LKG ───────────────────────────
        return self._attempt_rollback_phase(active_rev, lkg_rev, start_time, probe_result.details)

    def _attempt_rollback_phase(
        self,
        active_rev: str | None,
        lkg_rev: str | None,
        start_time: float,
        failure_details: str,
    ) -> bool:
        logger.info("Defibrillation Step 3: Evaluating deployment rollback...")

        if not lkg_rev:
            logger.error("No Last Known Good (LKG) revision available to roll back to. CRITICAL.")
            self._escalate_critical(
                active_rev=active_rev,
                lkg_rev=None,
                start_time=start_time,
                details=f"Restart failed and no LKG revision is known. ({failure_details})",
            )
            return False

        if active_rev and active_rev == lkg_rev:
            logger.error(f"Active revision {active_rev[:7]} is already the Last Known Good. Cannot rollback to self. CRITICAL.")
            self._escalate_critical(
                active_rev=active_rev,
                lkg_rev=lkg_rev,
                start_time=start_time,
                details=f"Active revision is already LKG and failed to recover. ({failure_details})",
            )
            return False

        if not self.state_manager.can_attempt_rollback(
            self.config.max_rollback_attempts,
            self.config.recovery_cooldown_seconds,
        ):
            logger.error("Rollback attempts limit reached or within cooldown. CRITICAL.")
            self._escalate_critical(
                active_rev=active_rev,
                lkg_rev=lkg_rev,
                start_time=start_time,
                details=f"Rollback attempts exhausted ({self.state_manager.state.rollback_attempts_count}). ({failure_details})",
            )
            return False

        # Quarantine candidate
        if active_rev:
            self.state_manager.quarantine(active_rev, f"Failed health probes during defibrillation: {failure_details}")

        logger.info(f"Restoring Last Known Good (LKG) revision: {lkg_rev[:7]} (from failed {active_rev[:7] if active_rev else 'unknown'})...")
        self.state_manager.record_rollback_attempt()
        rollback_ok = self._execute_rollback(lkg_rev)
        if not rollback_ok:
            logger.error("Rollback execution returned an error.")

        # ── Step 4: Verify Health Again ──────────────────────────────────────
        logger.info(f"Defibrillation Step 4: Verifying health after rollback to {lkg_rev[:7]}...")
        probe_result = self._wait_for_health(timeout_seconds=self.config.startup_grace_period_seconds)

        if probe_result.healthy:
            logger.info("Rollback recovery SUCCEEDED. Speda is healthy.")
            downtime = max(1, int(time.time() - start_time))
            record = IncidentRecord(
                service="speda",
                type="deployment_failure",
                failed_revision=active_rev,
                restored_revision=lkg_rev,
                recovery="rollback",
                result="success",
                downtime_seconds=downtime,
                details=f"Automatic rollback to LKG {lkg_rev[:7]} succeeded after restart failed.",
            )
            self.incident_store.record(record)
            self.notifier.send(self.notifier.format_recovery_message(record))
            self.state_manager.record_probe_success(lkg_rev, self.config.stability_window_seconds)
            return True

        # Rollback failed too -> CRITICAL
        logger.error(f"Rollback health verification FAILED: {probe_result.details}")
        self._escalate_critical(
            active_rev=active_rev,
            lkg_rev=lkg_rev,
            start_time=start_time,
            details=f"Restart failed. Rollback to {lkg_rev[:7]} failed health probe ({probe_result.details}).",
        )
        return False

    def _escalate_critical(
        self,
        active_rev: str | None,
        lkg_rev: str | None,
        start_time: float,
        details: str,
    ) -> None:
        downtime = max(1, int(time.time() - start_time))
        record = IncidentRecord(
            service="speda",
            type="deployment_failure" if active_rev != lkg_rev else "system_outage",
            failed_revision=active_rev,
            restored_revision=lkg_rev,
            recovery="rollback" if self.state_manager.state.rollback_attempts_count > 0 else "restart",
            result="critical",
            downtime_seconds=downtime,
            details=details,
        )
        self.incident_store.record(record)
        critical_msg = self.notifier.format_critical_message(
            current_revision=active_rev,
            last_known_good=lkg_rev,
            details=details,
        )
        self.notifier.send(critical_msg)

    def _wait_for_health(self, timeout_seconds: float) -> ProbeResult:
        """Polls health until healthy or timeout expires."""
        deadline = time.time() + timeout_seconds
        last_result = self.monitor.probe()

        while time.time() < deadline:
            if last_result.healthy:
                return last_result
            time.sleep(min(3.0, max(0.5, deadline - time.time())))
            last_result = self.monitor.probe()

        return last_result

    def _execute_restart(self) -> bool:
        if self.config.dry_run:
            logger.info("[DRY RUN] Would execute restart command.")
            return True

        cmd: list[str]
        if self.config.restart_command:
            cmd = shlex.split(self.config.restart_command)
        else:
            cmd = ["docker", "compose", "restart", self.config.docker_service]

        try:
            subprocess.check_call(cmd, cwd=self.config.repo_dir, timeout=60)
            return True
        except Exception as e:
            logger.error(f"Restart command failed: {e}")
            return False

    def _execute_rollback(self, lkg_rev: str) -> bool:
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would rollback Git repo to {lkg_rev} and redeploy.")
            return True

        if self.config.rollback_command:
            cmd = shlex.split(self.config.rollback_command.replace("{lkg}", lkg_rev))
            try:
                subprocess.check_call(cmd, cwd=self.config.repo_dir, timeout=120)
                return True
            except Exception as e:
                logger.error(f"Custom rollback command failed: {e}")
                return False

        # Default rollback sequence: git checkout <lkg_rev> then docker compose up -d --build
        try:
            logger.info(f"Checking out LKG Git revision: {lkg_rev}")
            subprocess.check_call(["git", "checkout", lkg_rev], cwd=self.config.repo_dir, timeout=30)
        except Exception as e:
            logger.error(f"Git checkout {lkg_rev} failed: {e}")
            return False

        try:
            logger.info(f"Re-deploying stack with compose up -d --build {self.config.docker_service}...")
            deploy_cmd = (
                shlex.split(self.config.deploy_command)
                if self.config.deploy_command
                else ["docker", "compose", "up", "-d", "--build", self.config.docker_service]
            )
            subprocess.check_call(deploy_cmd, cwd=self.config.repo_dir, timeout=300)
            return True
        except Exception as e:
            logger.error(f"Redeploy failed after rollback: {e}")
            return False
