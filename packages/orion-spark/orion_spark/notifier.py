# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Independent notification engine for Orion Spark.

Communicates directly with owner-configured notification channels (Telegram,
Webhooks, Syslog) without importing or relying on Speda runtime code.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from orion_spark.config import SparkConfig
from orion_spark.incidents import IncidentRecord

logger = logging.getLogger("orion_spark.notifier")


class Notifier:
    def __init__(self, config: SparkConfig) -> None:
        self.config = config

    def format_recovery_message(self, record: IncidentRecord) -> str:
        """Formats recovery success notification matching spec."""
        failed_short = (
            record.failed_revision[:7] if record.failed_revision else "unknown"
        )
        restored_short = (
            record.restored_revision[:7] if record.restored_revision else "unknown"
        )

        if record.recovery == "rollback":
            return (
                "ORION SPARK\n\n"
                f"Speda went down after deployment {failed_short}.\n\n"
                "Automatic rollback succeeded.\n"
                f"Restored revision: {restored_short}\n"
                f"Downtime: {record.downtime_seconds} seconds."
            )
        else:
            return (
                "ORION SPARK\n\n"
                "Speda went down.\n\n"
                "Automatic restart succeeded.\n"
                f"Active revision: {restored_short}\n"
                f"Downtime: {record.downtime_seconds} seconds."
            )

    def format_critical_message(
        self,
        current_revision: str | None,
        last_known_good: str | None,
        details: str = "",
    ) -> str:
        """Formats critical failure notification matching spec."""
        curr_short = current_revision[:7] if current_revision else "unknown"
        lkg_short = last_known_good[:7] if last_known_good else "none"

        msg = (
            "ORION SPARK — CRITICAL\n\n"
            "Speda is down.\n\n"
            "Restart failed.\n"
            "Rollback failed.\n\n"
            f"Current revision: {curr_short}\n"
            f"Last Known Good: {lkg_short}\n\n"
            "Manual intervention required."
        )
        if details:
            msg += f"\n\nDiagnostic details:\n{details}"
        return msg

    def send(self, message: str) -> None:
        """Dispatches message through all enabled independent notification paths."""
        logger.info(f"NOTIFICATION:\n{message}")

        if self.config.dry_run:
            logger.info("[DRY RUN] Notification dispatch skipped.")
            return

        # 1. Direct Telegram Bot API
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            self._send_telegram(message)

        # 2. Generic Webhook (Slack / Discord / custom endpoint)
        if self.config.webhook_url:
            self._send_webhook(message)

    def _send_telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.debug("Telegram alert sent successfully.")
                else:
                    logger.warning(f"Telegram API responded with status {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def _send_webhook(self, text: str) -> None:
        payload = {"text": text, "content": text}
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.config.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 204):
                    logger.debug("Webhook alert sent successfully.")
                else:
                    logger.warning(f"Webhook responded with status {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send Webhook alert: {e}")
