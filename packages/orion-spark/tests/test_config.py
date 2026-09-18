# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from pathlib import Path
import pytest

from orion_spark.config import SparkConfig


def test_default_config():
    config = SparkConfig()
    assert config.speda_host == "127.0.0.1"
    assert config.speda_port == 8000
    assert config.live_url == "http://127.0.0.1:8000/health/live"
    assert config.ready_url == "http://127.0.0.1:8000/health/ready"
    assert config.fallback_url == "http://127.0.0.1:8000/health"
    assert config.failure_threshold == 3
    assert config.stability_window_seconds == 300.0
    assert config.max_restart_attempts == 1
    assert config.max_rollback_attempts == 1
    assert not config.dry_run


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("SPARK_SPEDA_HOST", "10.0.0.5")
    monkeypatch.setenv("SPARK_SPEDA_PORT", "9000")
    monkeypatch.setenv("SPARK_FAILURE_THRESHOLD", "5")
    monkeypatch.setenv("SPARK_DRY_RUN", "true")

    config = SparkConfig.load()
    assert config.speda_host == "10.0.0.5"
    assert config.speda_port == 9000
    assert config.failure_threshold == 5
    assert config.dry_run is True
    assert config.live_url == "http://10.0.0.5:9000/health/live"


def test_config_from_file(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "SPARK_SPEDA_HOST=192.168.1.100\n"
        "SPARK_SPEDA_PORT=8888\n"
        "SPARK_FAILURE_THRESHOLD=4\n"
        "SPARK_TELEGRAM_BOT_TOKEN=test-token\n",
        encoding="utf-8",
    )
    config = SparkConfig.load(env_file=env_file)
    assert config.speda_host == "192.168.1.100"
    assert config.speda_port == 8888
    assert config.failure_threshold == 4
    assert config.telegram_bot_token == "test-token"
