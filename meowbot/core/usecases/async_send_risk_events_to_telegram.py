from __future__ import annotations

import logging
import time

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from meowbot.core.configs.strategy_version_test_users import is_strategy_version_test_user


log = logging.getLogger("meowbot")
MAX_NOTIFICATION_RETRIES = 5
RISK_NOTIFICATION_CHANNEL = "telegram_risk"
PLACEHOLDER_TELEGRAM_IDS = {"123456789"}
TERMINAL_TELEGRAM_ERROR_FRAGMENTS = (
    "chat not found",
    "user not found",
    "bot was blocked by the user",
    "user is deactivated",
    "bot was kicked from the group chat",
)

RISK_EVENT_TYPES_FOR_USER = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
}

RISK_EVENT_TYPES_FOR_ADMIN = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
    "ENTRY_BLOCKED_COOLDOWN",
}


class AsyncSendRiskEventsToTelegramUseCase:
    def __init__(
        self,
        *,
        trade_events_repo,
        runtime_repo,
        bot,
        builder,
        admin_chat_ids: list[int] | None = None,
        trades_repo=None,
    ) -> None:
        self.trade_events_repo = trade_events_repo
        self.runtime_repo = runtime_repo
        self.bot = bot
        self.builder = builder
        self.admin_chat_ids = admin_chat_ids or []
        self.trades_repo = trades_repo
        self._missing_entry_logged_keys: set[str] = set()
        self.worker_id = f"risk-telegram:{id(self)}"

    async def run_once(self) -> int:
        """
        Тут очікується, що repo вміє віддавати pending/new events.
        Якщо в тебе метод називається інакше — просто підставиш його.
        """
        if hasattr(self.trade_events_repo, "claim_pending_risk_events"):
            events = await self.trade_events_repo.claim_pending_risk_events(
                channel=RISK_NOTIFICATION_CHANNEL,
                worker_id=self.worker_id,
            )
        else:
            events = await self.trade_events_repo.get_pending_risk_events()
        sent = 0
        failed_temp = 0
        failed_permanent = 0
        skipped_debug = 0
        skipped_invalid_chat = 0
        retrying = 0

        for event in events:
            event_type = str(event.get("event_type") or "")
            trade_id = str(event.get("trade_id") or "")
            payload = dict(event.get("payload") or {})
            user_id = str(event.get("user_id") or "")
            symbol = str(event.get("symbol") or "")
            mode = str(event.get("mode") or "")

            payload["symbol"] = symbol

            if trade_id.startswith("debug:"):
                await self._mark_event_skipped(event, reason="debug_event")
                skipped_debug += 1
                log.info("[notification-flow] skipped debug risk event trade_id=%s user=%s symbol=%s", trade_id, user_id, symbol)
                continue

            if is_strategy_version_test_user(
                user_id=user_id,
                telegram_id=payload.get("telegram_id"),
                email=payload.get("email"),
                username=payload.get("username"),
            ):
                if event_type == "ENTRY_WARNING_RISK":
                    await self._mark_risk_warning_sent(event)
                await self.trade_events_repo.mark_event_delivered(event["id"], channel="telegram_risk")
                log.debug(
                    "[risk-events] skipped test user notification event_type=%s user_id=%s symbol=%s",
                    event_type,
                    user_id,
                    symbol,
                )
                continue

            if event_type == "ENTRY_WARNING_RISK" and not await self._entry_notification_ready(event, payload):
                continue

            user_message_sent = False
            if event_type in RISK_EVENT_TYPES_FOR_USER:
                target = await self.runtime_repo.resolve_notification_target_by_user_id(user_id)
                if not target or not target.get("chat_id") or self._is_placeholder_target(user_id=user_id, target=target):
                    await self._mark_event_skipped(event, reason="invalid_chat")
                    skipped_invalid_chat += 1
                    log.warning(
                        "[telegram-send-skipped-invalid-chat] trade_id=%s user_id=%s chat_id=%s event_type=%s",
                        trade_id,
                        user_id,
                        target.get("chat_id") if target else None,
                        event_type,
                    )
                    continue

                user_notifications_allowed = (
                    target
                    and target.get("chat_id")
                    and bool(target.get("notifications_enabled", True))
                    and bool(target.get("notify_system", True))
                )
                if user_notifications_allowed:
                    text = self.builder.build_user_message(event_type, payload)
                    if text:
                        try:
                            await self.bot.send_message(
                                chat_id=int(target["chat_id"]),
                                text=text,
                                parse_mode="HTML",
                            )
                            user_message_sent = True
                            sent += 1
                        except Exception as exc:
                            if self._is_terminal_telegram_error(exc):
                                await self._mark_event_failed_permanent(event, reason=self._terminal_error_reason(exc))
                                failed_permanent += 1
                                log.warning(
                                    "[telegram-send-failed-permanent] trade_id=%s user_id=%s chat_id=%s reason=%s",
                                    trade_id,
                                    user_id,
                                    target.get("chat_id"),
                                    self._terminal_error_reason(exc),
                                )
                                continue
                            if self._send_attempt_count(event) + 1 >= MAX_NOTIFICATION_RETRIES:
                                await self._mark_event_failed_permanent(event, reason=f"max_retries:{type(exc).__name__}:{exc}")
                                failed_permanent += 1
                                log.warning(
                                    "[telegram-send-failed-permanent] trade_id=%s user_id=%s chat_id=%s reason=max_retries error=%s:%s",
                                    trade_id,
                                    user_id,
                                    target.get("chat_id"),
                                    type(exc).__name__,
                                    exc,
                                )
                                continue
                            await self._mark_event_failed_temp(event, reason=f"{type(exc).__name__}: {exc}")
                            failed_temp += 1
                            retrying += 1
                            log.warning(
                                "[telegram-send-failed-temp] trade_id=%s user_id=%s chat_id=%s attempts=%s error=%s:%s",
                                trade_id,
                                user_id,
                                target.get("chat_id"),
                                self._send_attempt_count(event) + 1,
                                type(exc).__name__,
                                exc,
                            )
                            continue

                if event_type == "ENTRY_WARNING_RISK" and user_message_sent:
                    await self._mark_risk_warning_sent(event)
                    log.info(
                        "[notification-flow] trade_id=%s symbol=%s user=%s open_message_queued=True open_message_sent=True risk_warning_queued=True risk_warning_sent=True",
                        event.get("trade_id"),
                        symbol,
                        user_id,
                    )

            if event_type in RISK_EVENT_TYPES_FOR_ADMIN:
                admin_text = self.builder.build_admin_message(
                    event_type,
                    payload,
                    user_id=user_id,
                    symbol=symbol,
                )
                for admin_chat_id in self.admin_chat_ids:
                    try:
                        await self.bot.send_message(
                            chat_id=int(admin_chat_id),
                            text=admin_text,
                            parse_mode="HTML",
                        )
                    except Exception:
                        log.debug(
                            "[telegram-events] admin risk notification failed event_type=%s user_id=%s symbol=%s",
                            event_type,
                            user_id,
                            symbol,
                            exc_info=True,
                        )

            await self.trade_events_repo.mark_event_delivered(event["id"], channel="telegram_risk")

        oldest_age_sec = max((self._event_age_seconds(event) for event in events), default=0.0)
        log.info(
            "[notification-metrics] queued=%s processing=0 sent=%s failed_temp=%s failed_permanent=%s skipped_debug=%s skipped_invalid_chat=%s retrying=%s oldest_age_sec=%.1f",
            len(events),
            sent,
            failed_temp,
            failed_permanent,
            skipped_debug,
            skipped_invalid_chat,
            retrying,
            oldest_age_sec,
        )
        return sent

    async def _entry_notification_ready(self, event: dict, payload: dict) -> bool:
        if not bool(payload.get("entry_notification_required")):
            return True
        trade_id = str(payload.get("entry_trade_id") or event.get("trade_id") or "")
        if not trade_id or self.trades_repo is None or not hasattr(self.trades_repo, "get_trade_by_trade_id"):
            await self._log_risk_without_entry(event, payload, trade_id=trade_id, reason="tracking_unavailable")
            return False
        try:
            trade = await self.trades_repo.get_trade_by_trade_id(trade_id)
        except Exception as exc:
            await self._log_risk_without_entry(
                event,
                payload,
                trade_id=trade_id,
                reason=f"trade_lookup_failed:{type(exc).__name__}",
            )
            return False
        if trade is not None and bool(getattr(trade, "entry_notification_sent", False)):
            return True

        await self._log_risk_without_entry(
            event,
            payload,
            trade_id=trade_id,
            reason="entry_notification_not_sent",
        )
        return False

    async def _log_risk_without_entry(self, event: dict, payload: dict, *, trade_id: str, reason: str) -> None:
        event_key = str(event.get("id") or event.get("_id") or trade_id or "-")
        if event_key in self._missing_entry_logged_keys:
            return
        self._missing_entry_logged_keys.add(event_key)
        symbol = str(event.get("symbol") or payload.get("symbol") or "-")
        user_id = str(event.get("user_id") or "-")
        mode = str(event.get("mode") or "-")
        log.warning(
            "[notification-flow] RISK_WARNING_WITHOUT_ENTRY_NOTIFICATION trade_id=%s user=%s symbol=%s reason=%s",
            trade_id or "-",
            user_id,
            symbol,
            reason,
        )
        try:
            if hasattr(self.trade_events_repo, "add_event_once"):
                await self.trade_events_repo.add_event_once(
                    idempotency_key=f"risk-warning-without-entry:{event_key}",
                    trade_id=trade_id or f"risk-warning-without-entry:{event_key}",
                    event_type="RISK_WARNING_WITHOUT_ENTRY_NOTIFICATION",
                    ts=self._event_ts_or_now(event),
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload={
                        "reason": reason,
                        "entry_trade_id": trade_id,
                        "original_event_id": event.get("id") or event.get("_id"),
                        "warning_code": payload.get("warning_code"),
                    },
                )
        except Exception:
            log.debug("[notification-flow] failed to emit RISK_WARNING_WITHOUT_ENTRY_NOTIFICATION", exc_info=True)

    async def _mark_risk_warning_sent(self, event: dict) -> None:
        if self.trades_repo is None or not hasattr(self.trades_repo, "mark_risk_warning_sent"):
            return
        trade_id = str(event.get("trade_id") or "")
        if not trade_id:
            return
        try:
            await self.trades_repo.mark_risk_warning_sent(trade_id=trade_id, sent_at=self._event_ts_or_now(event))
        except Exception:
            log.warning(
                "[notification-flow] risk warning tracking failed trade_id=%s",
                trade_id,
                exc_info=True,
            )

    def _event_ts_or_now(self, event: dict) -> int:
        try:
            value = int(event.get("ts") or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else int(time.time() * 1000)

    def _event_age_seconds(self, event: dict) -> float:
        return max(0.0, (int(time.time() * 1000) - self._event_ts_or_now(event)) / 1000.0)

    def _send_attempt_count(self, event: dict) -> int:
        try:
            return int(event.get("send_attempt_count") or 0)
        except (TypeError, ValueError):
            return 0

    def _next_retry_at(self, event: dict) -> int:
        delays = [5, 10, 20, 40, 60]
        attempts = min(self._send_attempt_count(event), len(delays) - 1)
        return int(time.time() * 1000) + delays[attempts] * 1000

    def _is_placeholder_target(self, *, user_id: str, target: dict) -> bool:
        chat_id = str(target.get("chat_id") or "").strip()
        user_suffix = str(user_id or "").replace("tg:", "").strip()
        return chat_id in PLACEHOLDER_TELEGRAM_IDS or user_suffix in PLACEHOLDER_TELEGRAM_IDS

    def _is_terminal_telegram_error(self, exc: Exception) -> bool:
        if isinstance(exc, TelegramForbiddenError):
            return True
        if isinstance(exc, TelegramBadRequest):
            message = str(exc).lower()
            return any(fragment in message for fragment in TERMINAL_TELEGRAM_ERROR_FRAGMENTS)
        return False

    def _terminal_error_reason(self, exc: Exception) -> str:
        message = str(exc).lower()
        for fragment in TERMINAL_TELEGRAM_ERROR_FRAGMENTS:
            if fragment in message:
                return fragment.replace(" ", "_")
        return type(exc).__name__

    async def _mark_event_skipped(self, event: dict, *, reason: str) -> None:
        event_id = str(event.get("id") or event.get("_id") or "")
        if hasattr(self.trade_events_repo, "mark_event_skipped") and event_id:
            await self.trade_events_repo.mark_event_skipped(event_id, reason=reason, channel=RISK_NOTIFICATION_CHANNEL)
            return
        await self.trade_events_repo.mark_event_delivered(event_id, channel=RISK_NOTIFICATION_CHANNEL)

    async def _mark_event_failed_permanent(self, event: dict, *, reason: str) -> None:
        event_id = str(event.get("id") or event.get("_id") or "")
        if hasattr(self.trade_events_repo, "mark_event_failed_permanent") and event_id:
            await self.trade_events_repo.mark_event_failed_permanent(event_id, reason=reason, channel=RISK_NOTIFICATION_CHANNEL)
            return
        await self.trade_events_repo.mark_event_delivered(event_id, channel=RISK_NOTIFICATION_CHANNEL)

    async def _mark_event_failed_temp(self, event: dict, *, reason: str) -> None:
        event_id = str(event.get("id") or event.get("_id") or "")
        if hasattr(self.trade_events_repo, "mark_event_failed_temp") and event_id:
            await self.trade_events_repo.mark_event_failed_temp(
                event_id,
                reason=reason,
                next_retry_at=self._next_retry_at(event),
            )
