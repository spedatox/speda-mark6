# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from unittest.mock import patch, MagicMock
from orion_spark.config import SparkConfig
from orion_spark.incidents import IncidentRecord
from orion_spark.notifier import Notifier


def test_format_rollback_message():
    notifier = Notifier(SparkConfig())
    rec = IncidentRecord(
        service="speda",
        type="deployment_failure",
        failed_revision="a91bc7212345",
        restored_revision="83ce21f67890",
        recovery="rollback",
        result="success",
        downtime_seconds=47,
    )
    msg = notifier.format_recovery_message(rec)
    assert "ORION SPARK" in msg
    assert "Speda went down after deployment a91bc72." in msg
    assert "Automatic rollback succeeded." in msg
    assert "Restored revision: 83ce21f" in msg
    assert "Downtime: 47 seconds." in msg


def test_format_restart_message():
    notifier = Notifier(SparkConfig())
    rec = IncidentRecord(
        service="speda",
        type="process_crash",
        failed_revision="83ce21f",
        restored_revision="83ce21f",
        recovery="restart",
        result="success",
        downtime_seconds=22,
    )
    msg = notifier.format_recovery_message(rec)
    assert "ORION SPARK" in msg
    assert "Speda went down." in msg
    assert "Automatic restart succeeded." in msg
    assert "Active revision: 83ce21f" in msg
    assert "Downtime: 22 seconds." in msg


def test_format_critical_message():
    notifier = Notifier(SparkConfig())
    msg = notifier.format_critical_message(
        current_revision="a91bc7212345",
        last_known_good="83ce21f67890",
    )
    assert "ORION SPARK — CRITICAL" in msg
    assert "Speda is down." in msg
    assert "Restart failed." in msg
    assert "Rollback failed." in msg
    assert "Current revision: a91bc72" in msg
    assert "Last Known Good: 83ce21f" in msg
    assert "Manual intervention required." in msg


@patch("urllib.request.urlopen")
def test_send_telegram_and_webhook(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    config = SparkConfig(
        telegram_bot_token="test-token",
        telegram_chat_id="123456",
        webhook_url="https://hooks.example.com/speda",
        dry_run=False,
    )
    notifier = Notifier(config)
    notifier.send("Test message")

    assert mock_urlopen.call_count == 2
