# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Health monitor for Orion Spark.

Continuously probes Speda using five distinct signals:
1. Container state (running, restarting, exited)
2. /health/live (application process existence)
3. /health/ready (application request handling capacity)
4. Container restart count (crash-loop detection)
5. Current deployed Git revision
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orion_spark.config import SparkConfig

logger = logging.getLogger("orion_spark.monitor")


@dataclass
class ProbeResult:
    healthy: bool
    container_running: bool
    container_status: str
    restart_count: int
    live_ok: bool
    ready_ok: bool
    revision: str | None
    details: str

    def summary(self) -> str:
        status_sym = "✓" if self.healthy else "✗"
        rev_short = self.revision[:7] if self.revision else "unknown"
        return (
            f"[{status_sym}] Container: {self.container_status} (restarts: {self.restart_count}) | "
            f"Live: {'OK' if self.live_ok else 'FAIL'} | "
            f"Ready: {'OK' if self.ready_ok else 'FAIL'} | "
            f"Rev: {rev_short} | Details: {self.details}"
        )


class HealthMonitor:
    def __init__(self, config: SparkConfig) -> None:
        self.config = config
        self._last_restart_count: int = 0

    def probe(self) -> ProbeResult:
        """Executes all primary health probes and synthesizes the verdict."""
        container_running, container_status, restart_count = self.check_container()
        live_ok, live_detail = self.check_endpoint(self.config.live_url, fallback_url=self.config.fallback_url)
        ready_ok, ready_detail = self.check_endpoint(self.config.ready_url, fallback_url=self.config.fallback_url)
        revision = self.get_current_revision()

        failures: list[str] = []
        if not container_running:
            failures.append(f"container is {container_status}")
        if not live_ok:
            failures.append(f"live probe failed ({live_detail})")
        if not ready_ok:
            failures.append(f"ready probe failed ({ready_detail})")

        healthy = container_running and live_ok and ready_ok

        details = "All probes passed" if healthy else "; ".join(failures)

        return ProbeResult(
            healthy=healthy,
            container_running=container_running,
            container_status=container_status,
            restart_count=restart_count,
            live_ok=live_ok,
            ready_ok=ready_ok,
            revision=revision,
            details=details,
        )

    def check_container(self) -> tuple[bool, str, int]:
        """
        Inspects the Speda container status and restart count via Docker CLI.
        Returns: (is_running, status_string, restart_count)
        """
        # Determine target container / service
        target = self.config.docker_container_name
        cmd: list[str] = []

        if target:
            cmd = ["docker", "inspect", "-f", "{{.State.Status}}|{{.RestartCount}}", target]
        else:
            # Locate container ID through docker compose
            service = self.config.docker_service
            compose_cmd = ["docker", "compose", "ps", "-q", service]
            try:
                out = subprocess.check_output(
                    compose_cmd,
                    cwd=self.config.repo_dir,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    text=True,
                ).strip()
                if out:
                    container_id = out.splitlines()[0].strip()
                    cmd = ["docker", "inspect", "-f", "{{.State.Status}}|{{.RestartCount}}", container_id]
                else:
                    return False, "container_not_found", 0
            except Exception as e:
                logger.debug(f"docker compose ps failed: {e}")
                return False, f"docker_error: {e}", 0

        try:
            out = subprocess.check_output(
                cmd,
                cwd=self.config.repo_dir,
                stderr=subprocess.DEVNULL,
                timeout=5,
                text=True,
            ).strip()
            parts = out.split("|")
            status = parts[0].strip().lower() if len(parts) > 0 else "unknown"
            restart_count = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            is_running = (status == "running")
            return is_running, status, restart_count
        except Exception as e:
            logger.debug(f"Container inspection failed: {e}")
            return False, f"inspect_error: {e}", 0

    def check_endpoint(self, url: str, fallback_url: str | None = None) -> tuple[bool, str]:
        """
        Probes an HTTP health endpoint.
        Returns: (success: bool, description: str)
        """
        target_url = url
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "Orion-Spark/0.1"})
            with urllib.request.urlopen(req, timeout=self.config.probe_timeout_seconds) as resp:
                if resp.status == 200:
                    return True, "200 OK"
                return False, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            # If 404 on /health/live or /health/ready, try fallback /health
            if e.code == 404 and fallback_url and target_url != fallback_url:
                try:
                    req = urllib.request.Request(fallback_url, headers={"User-Agent": "Orion-Spark/0.1"})
                    with urllib.request.urlopen(req, timeout=self.config.probe_timeout_seconds) as resp:
                        if resp.status == 200:
                            return True, "200 OK (fallback)"
                        return False, f"HTTP {resp.status} (fallback)"
                except Exception as fb_err:
                    return False, f"Fallback error: {fb_err}"
            return False, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return False, f"Connection refused / {e.reason}"
        except Exception as e:
            return False, str(e)

    def get_current_revision(self) -> str | None:
        """Retrieves the active Git commit hash from the repository directory."""
        try:
            rev = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=self.config.repo_dir,
                stderr=subprocess.DEVNULL,
                timeout=5,
                text=True,
            ).strip()
            return rev if rev else None
        except Exception:
            # Attempt reading directly from .git/HEAD
            git_head = Path(self.config.repo_dir) / ".git" / "HEAD"
            if git_head.is_file():
                try:
                    head_content = git_head.read_text(encoding="utf-8").strip()
                    if head_content.startswith("ref: "):
                        ref_path = Path(self.config.repo_dir) / ".git" / head_content[5:].strip()
                        if ref_path.is_file():
                            return ref_path.read_text(encoding="utf-8").strip()
                    return head_content
                except Exception:
                    pass
        return None
