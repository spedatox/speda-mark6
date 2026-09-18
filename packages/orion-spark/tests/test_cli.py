# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from unittest.mock import patch
from orion_spark.cli import main
from orion_spark.monitor import ProbeResult


def test_cli_help(capsys):
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0
    captured = capsys.readouterr()
    assert "Orion Spark" in captured.out


def test_cli_status(tmp_path, capsys):
    ret = main(["--state-dir", str(tmp_path), "status"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "ORION SPARK STATUS" in captured.out


def test_cli_check(capsys):
    dummy_result = ProbeResult(
        healthy=True,
        container_running=True,
        container_status="running",
        restart_count=0,
        live_ok=True,
        ready_ok=True,
        revision="test1234",
        details="All ok",
    )
    with patch("orion_spark.monitor.HealthMonitor.probe", return_value=dummy_result):
        ret = main(["check"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Orion Spark Health Probe" in captured.out
        assert "Healthy:           YES" in captured.out


def test_cli_lkg_and_quarantine(tmp_path, capsys):
    ret1 = main(["--state-dir", str(tmp_path), "lkg", "--set", "abc1234"])
    assert ret1 == 0

    ret2 = main(["--state-dir", str(tmp_path), "lkg"])
    assert ret2 == 0
    captured = capsys.readouterr()
    assert "abc1234" in captured.out

    ret3 = main(["--state-dir", str(tmp_path), "quarantine", "--add", "bad5678", "--reason", "Test failure"])
    assert ret3 == 0

    ret4 = main(["--state-dir", str(tmp_path), "quarantine"])
    assert ret4 == 0
    captured4 = capsys.readouterr()
    assert "bad5678" in captured4.out
