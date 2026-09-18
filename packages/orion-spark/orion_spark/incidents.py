# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Incident record schema and storage for Orion Spark.

Every detected outage becomes an incident record persisted to incidents.jsonl.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("orion_spark.incidents")


@dataclass
class IncidentRecord:
    service: str = "speda"
    type: str = "outage"  # "deployment_failure", "process_crash", "unresponsive", "manual"
    failed_revision: str | None = None
    restored_revision: str | None = None
    recovery: str = "none"  # "rollback", "restart", "none"
    result: str = "success"  # "success", "critical", "aborted"
    downtime_seconds: int = 0
    timestamp: str = ""
    details: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IncidentStore:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.file_path = self.state_dir / "incidents.jsonl"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create incident storage dir {self.state_dir}: {e}")

    def record(self, incident: IncidentRecord) -> None:
        """Appends an incident record to incidents.jsonl."""
        self._ensure_dir()
        try:
            line = json.dumps(incident.to_dict()) + "\n"
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(line)
            logger.info(f"Recorded incident: {incident.type} ({incident.recovery} -> {incident.result})")
        except Exception as e:
            logger.error(f"Failed to record incident to {self.file_path}: {e}")

    def get_recent(self, limit: int = 10) -> list[IncidentRecord]:
        """Returns the most recent N incident records, newest first."""
        records: list[IncidentRecord] = []
        if not self.file_path.is_file():
            return records
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            for line in reversed(lines[-limit:]):
                try:
                    data = json.loads(line)
                    records.append(IncidentRecord(**data))
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to read incidents from {self.file_path}: {e}")
        return records

    def get_all(self) -> list[IncidentRecord]:
        """Returns all recorded incidents."""
        records: list[IncidentRecord] = []
        if not self.file_path.is_file():
            return records
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(IncidentRecord(**data))
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Failed to read all incidents from {self.file_path}: {e}")
        return records
