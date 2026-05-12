from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger("meowbot")


@dataclass(slots=True)
class AdminAlertRecord:
    last_sent_at: float = 0.0
    suppressed_count: int = 0
    last_payload: dict[str, Any] = field(default_factory=dict)


class AdminAlertService:
    def __init__(
        self,
        *,
        notifier,
        throttle_seconds: float = 600.0,
    ) -> None:
        self.notifier = notifier
        self.throttle_seconds = float(throttle_seconds)
        self._records: dict[str, AdminAlertRecord] = {}

    async def send_alert(
        self,
        *,
        alert_key: str,
        component: str,
        severity: str,
        error: str,
        action_taken: str,
        symbol: str | None = None,
        user_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> bool:
        if not getattr(self.notifier, "enabled", False):
            return False

        now = time.time()
        record = self._records.setdefault(str(alert_key), AdminAlertRecord())
        payload = {
            "component": component,
            "severity": severity.upper(),
            "error": error,
            "action_taken": action_taken,
            "symbol": symbol,
            "user_id": user_id,
            "details": details or {},
        }

        if record.last_sent_at and (now - record.last_sent_at) < self.throttle_seconds:
            record.suppressed_count += 1
            record.last_payload = payload
            return False

        repeats = record.suppressed_count
        record.last_sent_at = now
        record.suppressed_count = 0
        record.last_payload = payload

        lines = [
            "<b>MeowBot problem</b>",
            f"component: {component}",
            f"severity: {severity.upper()}",
            f"error: {error}",
            f"action: {action_taken}",
            f"time: {int(now)}",
        ]
        if symbol:
            lines.append(f"symbol: {symbol}")
        if user_id:
            lines.append(f"user: {user_id}")
        if repeats:
            lines.append(f"repeats_suppressed: {repeats}")
        for key, value in (details or {}).items():
            lines.append(f"{key}: {value}")
        text = "\n".join(lines)

        try:
            await self.notifier.send_message(text)
            log.warning(
                "[admin-alert] sent key=%s component=%s severity=%s symbol=%s user_id=%s",
                alert_key,
                component,
                severity.upper(),
                symbol,
                user_id,
            )
            return True
        except Exception as exc:
            log.warning(
                "[admin-alert] delivery failed key=%s component=%s error=%s:%s",
                alert_key,
                component,
                type(exc).__name__,
                exc,
            )
            return False

    def notify_later(self, **kwargs: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.send_alert(**kwargs))
