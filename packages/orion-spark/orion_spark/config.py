# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration for Orion Spark.

All settings can be configured via environment variables (prefixed with SPARK_)
or via a .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on", "t")


def _find_default_state_dir() -> Path:
    # Prefer /opt/speda/spark on Linux server if /opt/speda exists
    opt_speda = Path("/opt/speda/spark")
    if Path("/opt/speda").exists():
        return opt_speda
    # Otherwise ~/.speda/spark
    home_speda = Path.home() / ".speda" / "spark"
    return home_speda


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                env[k] = v
    except Exception:
        pass
    return env


@dataclass
class SparkConfig:
    # Target Speda probe settings
    speda_host: str = "127.0.0.1"
    speda_port: int = 8000
    live_path: str = "/health/live"
    ready_path: str = "/health/ready"
    fallback_path: str = "/health"
    probe_interval_seconds: float = 15.0
    probe_timeout_seconds: float = 5.0

    # Defibrillation thresholds and stability window
    failure_threshold: int = 3
    stability_window_seconds: float = 300.0  # 5 minutes healthy to promote candidate to LKG
    startup_grace_period_seconds: float = 30.0  # Wait after restart/rollback before evaluating health

    # Docker & Git target parameters
    docker_compose_file: str = "docker-compose.yml"
    docker_service: str = "app"
    docker_container_name: str = ""  # If empty, targeted by compose service
    repo_dir: str = "."

    # Anti-loop & recovery bounds
    recovery_cooldown_seconds: float = 60.0
    max_restart_attempts: int = 1
    max_rollback_attempts: int = 1

    # Storage
    state_dir: Path = field(default_factory=_find_default_state_dir)

    # Independent notification settings
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""

    # Commands (empty defaults will use standard docker compose / git commands)
    restart_command: str = ""
    deploy_command: str = ""
    rollback_command: str = ""

    # Operational flags
    dry_run: bool = False
    log_level: str = "INFO"

    @property
    def live_url(self) -> str:
        return f"http://{self.speda_host}:{self.speda_port}{self.live_path}"

    @property
    def ready_url(self) -> str:
        return f"http://{self.speda_host}:{self.speda_port}{self.ready_path}"

    @property
    def fallback_url(self) -> str:
        return f"http://{self.speda_host}:{self.speda_port}{self.fallback_path}"

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> SparkConfig:
        file_env: dict[str, str] = {}
        if env_file:
            file_env = _load_env_file(Path(env_file))
        else:
            # Check default candidate env locations
            candidates = [
                Path("spark.env"),
                Path("packages/orion-spark/spark.env"),
                Path("/etc/orion-spark/spark.env"),
                Path.home() / ".speda" / "spark.env",
            ]
            for cand in candidates:
                if cand.is_file():
                    file_env = _load_env_file(cand)
                    break

        def get_val(key: str, default: Any) -> Any:
            # Environment variable takes priority, then env file, then default
            spark_key = f"SPARK_{key.upper()}"
            if spark_key in os.environ:
                return os.environ[spark_key]
            if key.upper() in os.environ:
                return os.environ[key.upper()]
            if spark_key in file_env:
                return file_env[spark_key]
            if key.upper() in file_env:
                return file_env[key.upper()]
            return default

        state_dir_raw = get_val("state_dir", None)
        state_dir = Path(state_dir_raw).expanduser() if state_dir_raw else _find_default_state_dir()

        return cls(
            speda_host=str(get_val("speda_host", "127.0.0.1")),
            speda_port=int(get_val("speda_port", 8000)),
            live_path=str(get_val("live_path", "/health/live")),
            ready_path=str(get_val("ready_path", "/health/ready")),
            fallback_path=str(get_val("fallback_path", "/health")),
            probe_interval_seconds=float(get_val("probe_interval_seconds", 15.0)),
            probe_timeout_seconds=float(get_val("probe_timeout_seconds", 5.0)),
            failure_threshold=int(get_val("failure_threshold", 3)),
            stability_window_seconds=float(get_val("stability_window_seconds", 300.0)),
            startup_grace_period_seconds=float(get_val("startup_grace_period_seconds", 30.0)),
            docker_compose_file=str(get_val("docker_compose_file", "docker-compose.yml")),
            docker_service=str(get_val("docker_service", "app")),
            docker_container_name=str(get_val("docker_container_name", "")),
            repo_dir=str(get_val("repo_dir", ".")),
            recovery_cooldown_seconds=float(get_val("recovery_cooldown_seconds", 60.0)),
            max_restart_attempts=int(get_val("max_restart_attempts", 1)),
            max_rollback_attempts=int(get_val("max_rollback_attempts", 1)),
            state_dir=state_dir,
            telegram_bot_token=str(get_val("telegram_bot_token", "")),
            telegram_chat_id=str(get_val("telegram_chat_id", "")),
            webhook_url=str(get_val("webhook_url", "")),
            restart_command=str(get_val("restart_command", "")),
            deploy_command=str(get_val("deploy_command", "")),
            rollback_command=str(get_val("rollback_command", "")),
            dry_run=_parse_bool(get_val("dry_run", False)),
            log_level=str(get_val("log_level", "INFO")).upper(),
        )
