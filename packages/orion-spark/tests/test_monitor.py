# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import urllib.error
from unittest.mock import patch, MagicMock
from orion_spark.config import SparkConfig
from orion_spark.monitor import HealthMonitor, ProbeResult


def test_probe_healthy_when_all_pass():
    config = SparkConfig()
    monitor = HealthMonitor(config)

    with patch.object(monitor, "check_container", return_value=(True, "running", 0)), \
         patch.object(monitor, "check_endpoint", return_value=(True, "200 OK")), \
         patch.object(monitor, "get_current_revision", return_value="a1b2c3d"):

        res = monitor.probe()
        assert res.healthy is True
        assert res.container_running is True
        assert res.live_ok is True
        assert res.ready_ok is True
        assert res.revision == "a1b2c3d"


def test_probe_fails_when_ready_fails():
    config = SparkConfig()
    monitor = HealthMonitor(config)

    def mock_endpoint(url, fallback_url=None):
        if "ready" in url:
            return False, "HTTP 503"
        return True, "200 OK"

    with patch.object(monitor, "check_container", return_value=(True, "running", 0)), \
         patch.object(monitor, "check_endpoint", side_effect=mock_endpoint), \
         patch.object(monitor, "get_current_revision", return_value="a1b2c3d"):

        res = monitor.probe()
        assert res.healthy is False
        assert res.container_running is True
        assert res.live_ok is True
        assert res.ready_ok is False
        assert "ready probe failed" in res.details


def test_probe_fails_when_container_not_running():
    config = SparkConfig()
    monitor = HealthMonitor(config)

    with patch.object(monitor, "check_container", return_value=(False, "exited", 3)), \
         patch.object(monitor, "check_endpoint", return_value=(False, "Connection refused")), \
         patch.object(monitor, "get_current_revision", return_value="a1b2c3d"):

        res = monitor.probe()
        assert res.healthy is False
        assert res.container_running is False
        assert res.container_status == "exited"
        assert res.restart_count == 3
        assert "container is exited" in res.details
