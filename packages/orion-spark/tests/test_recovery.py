# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from unittest.mock import patch, MagicMock
import pytest

from orion_spark.config import SparkConfig
from orion_spark.incidents import IncidentStore
from orion_spark.monitor import HealthMonitor, ProbeResult
from orion_spark.notifier import Notifier
from orion_spark.recovery import Defibrillator
from orion_spark.state import DeploymentStateManager


@pytest.fixture
def test_setup(tmp_path):
    config = SparkConfig(
        state_dir=tmp_path,
        startup_grace_period_seconds=0.1,
        recovery_cooldown_seconds=0.0,
        dry_run=True,
    )
    state_mgr = DeploymentStateManager(tmp_path)
    store = IncidentStore(tmp_path)
    notifier = Notifier(config)
    monitor = HealthMonitor(config)
    defib = Defibrillator(
        config=config,
        state_manager=state_mgr,
        incident_store=store,
        notifier=notifier,
        monitor=monitor,
    )
    return config, state_mgr, store, notifier, monitor, defib


def test_defibrillation_path_restart_succeeds(test_setup):
    config, state_mgr, store, notifier, monitor, defib = test_setup
    state_mgr.set_lkg("rev_lkg")
    state_mgr.state.active_revision = "rev_lkg"

    healthy_result = ProbeResult(
        healthy=True,
        container_running=True,
        container_status="running",
        restart_count=0,
        live_ok=True,
        ready_ok=True,
        revision="rev_lkg",
        details="All probes passed",
    )

    with patch.object(defib, "_execute_restart", return_value=True), \
         patch.object(defib, "_wait_for_health", return_value=healthy_result), \
         patch.object(notifier, "send") as mock_notify:

        success = defib.defibrillate(reason="Test restart recovery")
        assert success is True
        assert mock_notify.called

        recs = store.get_all()
        assert len(recs) == 1
        assert recs[0].recovery == "restart"
        assert recs[0].result == "success"


def test_defibrillation_path_restart_fails_rollback_succeeds(test_setup):
    config, state_mgr, store, notifier, monitor, defib = test_setup
    state_mgr.set_lkg("83ce21f")
    state_mgr.state.active_revision = "a91bc72"  # New candidate that broke

    unhealthy_result = ProbeResult(
        healthy=False,
        container_running=True,
        container_status="running",
        restart_count=1,
        live_ok=True,
        ready_ok=False,
        revision="a91bc72",
        details="ready probe failed (HTTP 503)",
    )
    healthy_lkg_result = ProbeResult(
        healthy=True,
        container_running=True,
        container_status="running",
        restart_count=1,
        live_ok=True,
        ready_ok=True,
        revision="83ce21f",
        details="All probes passed",
    )

    # First wait_for_health (after restart) fails; second (after rollback) succeeds
    with patch.object(defib, "_execute_restart", return_value=True), \
         patch.object(defib, "_execute_rollback", return_value=True), \
         patch.object(defib, "_wait_for_health", side_effect=[unhealthy_result, healthy_lkg_result]), \
         patch.object(notifier, "send") as mock_notify:

        success = defib.defibrillate(reason="Broken startup import")
        assert success is True
        assert mock_notify.called

        # Candidate should now be in quarantine!
        assert state_mgr.is_quarantined("a91bc72")

        # Incident record check
        recs = store.get_all()
        assert len(recs) == 1
        assert recs[0].recovery == "rollback"
        assert recs[0].result == "success"
        assert recs[0].failed_revision == "a91bc72"
        assert recs[0].restored_revision == "83ce21f"


def test_defibrillation_path_restart_fails_rollback_fails_critical(test_setup):
    config, state_mgr, store, notifier, monitor, defib = test_setup
    state_mgr.set_lkg("83ce21f")
    state_mgr.state.active_revision = "a91bc72"

    unhealthy_result = ProbeResult(
        healthy=False,
        container_running=False,
        container_status="dead",
        restart_count=5,
        live_ok=False,
        ready_ok=False,
        revision="a91bc72",
        details="Container dead",
    )

    with patch.object(defib, "_execute_restart", return_value=True), \
         patch.object(defib, "_execute_rollback", return_value=True), \
         patch.object(defib, "_wait_for_health", return_value=unhealthy_result), \
         patch.object(notifier, "send") as mock_notify:

        success = defib.defibrillate(reason="Host failure")
        assert success is False
        assert mock_notify.called

        recs = store.get_all()
        assert len(recs) == 1
        assert recs[0].recovery == "rollback"
        assert recs[0].result == "critical"
