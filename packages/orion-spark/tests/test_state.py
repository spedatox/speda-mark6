# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
import pytest
from pathlib import Path

from orion_spark.state import DeploymentStateManager


def test_initial_state(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    state = mgr.state
    assert state.active_revision is None
    assert state.last_known_good is None
    assert state.consecutive_failures == 0
    assert not state.in_outage


def test_first_probe_sets_initial_lkg(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    recovered, promoted, downtime = mgr.record_probe_success("rev1", stability_window_seconds=10.0)
    assert promoted == "rev1"
    assert mgr.state.last_known_good == "rev1"
    assert mgr.state.active_revision == "rev1"
    assert mgr.state.candidate_revision is None


def test_candidate_stability_window_promotion(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    mgr.record_probe_success("rev1", stability_window_seconds=1.0)
    assert mgr.state.last_known_good == "rev1"

    # New candidate deployed
    recovered, promoted, downtime = mgr.record_probe_success("rev2", stability_window_seconds=1.0)
    assert promoted is None
    assert mgr.state.candidate_revision == "rev2"
    assert mgr.state.last_known_good == "rev1"

    # Sleep past stability window
    time.sleep(1.1)
    recovered, promoted, downtime = mgr.record_probe_success("rev2", stability_window_seconds=1.0)
    assert promoted == "rev2"
    assert mgr.state.last_known_good == "rev2"
    assert mgr.state.candidate_revision is None


def test_outage_trigger_and_recovery(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    mgr.record_probe_success("rev1", stability_window_seconds=10.0)

    # First two failures do not confirm outage (threshold = 3)
    assert not mgr.record_probe_failure("rev1", failure_threshold=3)
    assert not mgr.record_probe_failure("rev1", failure_threshold=3)
    assert mgr.state.consecutive_failures == 2
    assert not mgr.state.in_outage

    # Third failure confirms outage
    assert mgr.record_probe_failure("rev1", failure_threshold=3)
    assert mgr.state.in_outage is True
    assert mgr.state.outage_started_at is not None

    # Subsequent failure during outage does not re-declare outage
    assert not mgr.record_probe_failure("rev1", failure_threshold=3)

    # Success recovers outage
    time.sleep(0.05)
    recovered, promoted, downtime = mgr.record_probe_success("rev1", stability_window_seconds=10.0)
    assert recovered is True
    assert downtime is not None and downtime >= 0
    assert mgr.state.in_outage is False
    assert mgr.state.consecutive_failures == 0


def test_quarantine_management(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    assert not mgr.is_quarantined("bad_rev_1234567")

    mgr.quarantine("bad_rev_1234567", reason="FastAPI import crash")
    assert mgr.is_quarantined("bad_rev_1234567")
    assert mgr.is_quarantined("bad_rev")  # short prefix check

    # Unquarantine
    assert mgr.unquarantine("bad_rev_1234567")
    assert not mgr.is_quarantined("bad_rev_1234567")


def test_recovery_attempt_bounds(tmp_path):
    mgr = DeploymentStateManager(tmp_path)
    mgr.set_lkg("rev1")

    assert mgr.can_attempt_restart(max_attempts=1, cooldown_seconds=10.0)
    mgr.record_restart_attempt()
    assert not mgr.can_attempt_restart(max_attempts=1, cooldown_seconds=10.0)

    assert mgr.can_attempt_rollback(max_attempts=1, cooldown_seconds=0.0)
    mgr.record_rollback_attempt()
    assert not mgr.can_attempt_rollback(max_attempts=1, cooldown_seconds=0.0)

    mgr.reset_recovery_counters()
    assert mgr.can_attempt_restart(max_attempts=1, cooldown_seconds=0.0)
