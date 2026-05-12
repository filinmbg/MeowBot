from __future__ import annotations

import logging
import time
import anyio

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from meowbot.core.configs.strategy_version_test_users import is_strategy_version_test_user

log = logging.getLogger("meowbot")


USER_EVENT_TYPES = {
    "OPENED",
    "TP_HIT",
    "CLOSED",
    "STOP",
    "CLOSED_PNL_PENDING",
    "INVALID_API",
    "SOFT_STOP_ACTIVATED",
    "SOFT_STOP_RAISED",
}
USER_EVENT_PREFERENCE_FLAGS = {
    "OPENED": "notify_trade_opened",
    "TP_HIT": "notify_tp_hit",
    "CLOSED": "notify_trade_closed",
    "STOP": "notify_stop_loss",
    "CLOSED_PNL_PENDING": "notify_trade_closed",
    "INVALID_API": "notify_system",
    "SOFT_STOP_ACTIVATED": "notify_system",
    "SOFT_STOP_RAISED": "notify_system",
}
ADMIN_DEBUG_EVENT_TYPES = {
    "LIVE_MODE_NOT_CONNECTED",
    "LIVE_ORDER_REJECTED",
    "ENTRY_EXECUTION_FAILED",
    "LIVE_SYNC_ERROR",
    "LIVE_SYNC_MISMATCH",
    "LIVE_PROTECTION_SETUP_FAILED",
    "LIVE_POSITION_UNPROTECTED",
    "LIVE_PNL_PENDING",
    "LIVE_PNL_MISMATCH",
    "LIVE_SOFT_STOP_TRIGGERED",
    "INVALID_SYMBOL_SKIPPED",
    "TELEGRAM_ENTRY_NOTIFICATION_FAILED",
    "RISK_WARNING_WITHOUT_ENTRY_NOTIFICATION",
    "TELEGRAM_NOTIFICATION_STUCK",
}


TERMINAL_TELEGRAM_ERROR_FRAGMENTS = (
    "chat not found",
    "user not found",
    "bot was blocked by the user",
    "user is deactivated",
    "bot was kicked from the group chat",
)
MAX_NOTIFICATION_RETRIES = 5


class AsyncSendTradeEventsToTelegramUseCase:
    def __init__(
        self,
        *,
        trade_events_repo,
        telegram_users_repo,
        bot,
        message_builder,
        admin_chat_id: str | None,
        trades_repo=None,
        max_send_concurrency: int = 5,
        stuck_after_seconds: float = 30.0,
    ) -> None:
        self.trade_events_repo = trade_events_repo
        self.telegram_users_repo = telegram_users_repo
        self.bot = bot
        self.message_builder = message_builder
        self.admin_chat_id = str(admin_chat_id).strip() if admin_chat_id else None
        self.trades_repo = trades_repo
        self.max_send_concurrency = max(1, int(max_send_concurrency))
        self.stuck_after_seconds = float(stuck_after_seconds)
        self.worker_id = f"trade-telegram:{id(self)}"

    async def run_once(self) -> int:
        started = time.perf_counter()
        events = await self._get_prioritized_unsent_events(limit=100)
        if not events:
            return 0

        await self._emit_stuck_open_notifications_if_needed(events)

        semaphore = anyio.Semaphore(self.max_send_concurrency)
        results: list[bool | Exception] = []

        async def _runner(event: dict) -> None:
            try:
                results.append(await self._process_and_mark_event(event, semaphore=semaphore))
            except Exception as exc:  # pragma: no cover - defensive worker isolation
                log.exception(
                    "[notification-worker] task failed trade_id=%s event_type=%s",
                    event.get("trade_id"),
                    event.get("event_type"),
                )
                results.append(exc)

        async with anyio.create_task_group() as tg:
            for event in events:
                tg.start_soon(_runner, event)
        sent_count = sum(1 for item in results if item is True)
        failed_count = sum(1 for item in results if item is False or isinstance(item, Exception))
        duration_ms = int((time.perf_counter() - started) * 1000)
        avg_send_ms = int(duration_ms / max(len(events), 1))
        delivered_count = sum(1 for event in events if event.get("_notification_message_sent"))
        failed_temp = sum(1 for event in events if event.get("_notification_failed_temp"))
        failed_permanent = sum(1 for event in events if event.get("_notification_terminal_handled"))
        skipped_debug = sum(1 for event in events if event.get("_notification_skip_reason") == "debug_event")
        skipped_invalid_chat = sum(1 for event in events if event.get("_notification_skip_reason") == "invalid_chat")
        log.info(
            "[notification-metrics] queued=%s processing=0 sent=%s failed_temp=%s failed_permanent=%s skipped_debug=%s skipped_invalid_chat=%s retrying=%s stuck=%s oldest_age_sec=%.1f avg_send_ms=%s duration_ms=%s",
            len(events),
            delivered_count,
            failed_temp,
            failed_permanent,
            skipped_debug,
            skipped_invalid_chat,
            failed_temp,
            await self._count_stuck_open_events(events),
            max((self._event_age_seconds(event) for event in events), default=0.0),
            avg_send_ms,
            duration_ms,
        )
        return sent_count

    async def _get_prioritized_unsent_events(self, *, limit: int) -> list[dict]:
        opened = await self._repo_get_unsent(limit=limit, event_types=("OPENED",))
        remaining_limit = max(0, int(limit) - len(opened))
        if remaining_limit <= 0:
            return opened
        other = await self._repo_get_unsent(limit=remaining_limit, exclude_event_types=("OPENED",))
        by_id: dict[str, dict] = {}
        for event in [*opened, *other]:
            event_id = str(event.get("_id") or event.get("id") or f"{event.get('trade_id')}:{event.get('event_type')}:{event.get('ts')}")
            by_id[event_id] = event
        return list(by_id.values())

    async def _repo_get_unsent(
        self,
        *,
        limit: int,
        event_types: tuple[str, ...] | None = None,
        exclude_event_types: tuple[str, ...] | None = None,
    ) -> list[dict]:
        try:
            if hasattr(self.trade_events_repo, "claim_unsent_events"):
                return await self.trade_events_repo.claim_unsent_events(
                    limit=limit,
                    worker_id=self.worker_id,
                    event_types=event_types,
                    exclude_event_types=exclude_event_types,
                )
            return await self.trade_events_repo.get_unsent_events(
                limit=limit,
                event_types=event_types,
                exclude_event_types=exclude_event_types,
            )
        except TypeError:
            events = await self.trade_events_repo.get_unsent_events(limit=limit)
            if event_types:
                return [event for event in events if str(event.get("event_type") or "") in event_types]
            if exclude_event_types:
                return [event for event in events if str(event.get("event_type") or "") not in exclude_event_types]
            return events

    async def _process_and_mark_event(self, event: dict, *, semaphore: anyio.Semaphore) -> bool:
        async with semaphore:
            event_type = str(event.get("event_type") or "")
            log.info(
                "[notification-worker] picked_job=%s trade_id=%s event_id=%s symbol=%s user=%s",
                event_type,
                event.get("trade_id"),
                event.get("_id") or event.get("id"),
                event.get("symbol"),
                event.get("user_id"),
            )
            ok = await self._process_event(event)
            if ok:
                if not event.get("_notification_status_already_marked"):
                    await self.trade_events_repo.mark_sent(event["_id"])
                return True
            return False

    async def _process_event(self, event: dict) -> bool:
        event_type = str(event.get("event_type") or "")
        symbol = str(event.get("symbol") or "-")
        user_id = str(event.get("user_id") or "system")
        mode = str(event.get("mode") or "sandbox")
        payload = event.get("payload") or {}
        trade_id = str(event.get("trade_id") or "")

        try:
            if trade_id.startswith("debug:"):
                await self._mark_event_skipped(event, reason="debug_event")
                event["_notification_status_already_marked"] = True
                log.info("[notification-flow] skipped debug event trade_id=%s event_type=%s user=%s symbol=%s", trade_id, event_type, user_id, symbol)
                return True

            if event_type in USER_EVENT_TYPES:
                target = await self.telegram_users_repo.resolve_notification_target_by_user_id(user_id)
                if not target:
                    log.warning("[telegram-events] no target found for user_id=%s event_type=%s", user_id, event_type)
                    await self._mark_event_skipped(event, reason="invalid_chat")
                    event["_notification_status_already_marked"] = True
                    if event_type == "OPENED":
                        exc = RuntimeError("notification_target_not_found")
                        await self._record_entry_notification_failure(event, exc)
                        await self._emit_entry_notification_failed(
                            event=event,
                            symbol=symbol,
                            user_id=user_id,
                            mode=mode,
                            exc=exc,
                        )
                        await self._mark_entry_notification_sent(event)
                    return True

                if self._is_test_user_notification(user_id=user_id, target=target, payload=payload):
                    if event_type == "OPENED":
                        await self._mark_entry_notification_sent(event)
                    log.debug(
                        "[telegram-events] skipped test user notification event_type=%s user_id=%s symbol=%s",
                        event_type,
                        user_id,
                        symbol,
                    )
                    return True

                if not bool(target.get("notifications_enabled", True)):
                    log.info(
                        "[notification-worker] skipped_notifications_disabled trade_id=%s event_type=%s user=%s",
                        event.get("trade_id"),
                        event_type,
                        user_id,
                    )
                    if event_type == "OPENED":
                        await self._mark_entry_notification_sent(event)
                    await self._mark_event_skipped(event, reason="skipped")
                    event["_notification_status_already_marked"] = True
                    return True
                preference_flag = USER_EVENT_PREFERENCE_FLAGS.get(event_type)
                if preference_flag and not bool(target.get(preference_flag, True)):
                    log.info(
                        "[notification-worker] skipped_preference_disabled trade_id=%s event_type=%s user=%s preference=%s",
                        event.get("trade_id"),
                        event_type,
                        user_id,
                        preference_flag,
                    )
                    if event_type == "OPENED":
                        await self._mark_entry_notification_sent(event)
                    await self._mark_event_skipped(event, reason="skipped")
                    event["_notification_status_already_marked"] = True
                    return True

                chat_id = target.get("chat_id")
                if not chat_id:
                    log.warning("[telegram-events] no chat_id for user_id=%s event_type=%s", user_id, event_type)
                    await self._mark_event_skipped(event, reason="invalid_chat")
                    event["_notification_status_already_marked"] = True
                    if event_type == "OPENED":
                        exc = RuntimeError("notification_chat_id_missing")
                        await self._record_entry_notification_failure(event, exc)
                        await self._emit_entry_notification_failed(
                            event=event,
                            symbol=symbol,
                            user_id=user_id,
                            mode=mode,
                            exc=exc,
                        )
                        await self._mark_entry_notification_sent(event)
                    return True

                lang = str(target.get("preferred_language") or "uk")
                text = self.message_builder.build(
                    event_type=event_type,
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload=payload,
                    lang=lang,
                    is_admin=self._is_admin_target(target=target, chat_id=chat_id),
                )
                try:
                    resolved_chat_id = int(chat_id)
                except (TypeError, ValueError):
                    log.warning(
                        "[telegram-events] invalid chat_id=%r for user_id=%s event_type=%s",
                        chat_id,
                        user_id,
                        event_type,
                    )
                    await self._mark_event_skipped(event, reason="invalid_chat")
                    event["_notification_status_already_marked"] = True
                    if event_type == "OPENED":
                        exc = ValueError(f"invalid_notification_chat_id:{chat_id!r}")
                        await self._record_entry_notification_failure(event, exc)
                        await self._emit_entry_notification_failed(
                            event=event,
                            symbol=symbol,
                            user_id=user_id,
                            mode=mode,
                            exc=exc,
                        )
                        await self._mark_entry_notification_sent(event)
                    return True

                message_type = "open_trade" if event_type == "OPENED" else event_type.lower()
                send_started = time.perf_counter()
                if event_type == "OPENED":
                    await self._record_entry_notification_attempt(event)
                log.info(
                    "[telegram-send] trade_id=%s chat_id=%s message_type=%s event_id=%s",
                    event.get("trade_id"),
                    resolved_chat_id,
                    message_type,
                    event.get("_id") or event.get("id"),
                )
                message = await self.bot.send_message(chat_id=resolved_chat_id, text=text, parse_mode="HTML")
                event["_notification_message_sent"] = True
                log.info(
                    "[telegram-send-success] trade_id=%s chat_id=%s message_type=%s message_id=%s duration_ms=%s",
                    event.get("trade_id"),
                    resolved_chat_id,
                    message_type,
                    getattr(message, "message_id", None),
                    int((time.perf_counter() - send_started) * 1000),
                )
                await self._send_admin_open_copy_if_needed(
                    event_type=event_type,
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload=payload,
                    lang=lang,
                    user_chat_id=resolved_chat_id,
                )
                if event_type == "OPENED":
                    await self._mark_entry_notification_sent(event)
                    log.info(
                        "[notification-flow] trade_id=%s symbol=%s user=%s open_message_queued=True open_message_sent=True risk_warning_queued=%s risk_warning_sent=False",
                        event.get("trade_id"),
                        symbol,
                        user_id,
                        bool(payload.get("risk_warning_pending")),
                    )
                return True

            if event_type in ADMIN_DEBUG_EVENT_TYPES:
                if not self.admin_chat_id:
                    return True
                text = self.message_builder.build_admin_debug(
                    event_type=event_type,
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload=payload,
                )
                log.info(
                    "[telegram-send] trade_id=%s chat_id=%s message_type=admin_debug event_type=%s",
                    event.get("trade_id"),
                    self.admin_chat_id,
                    event_type,
                )
                message = await self.bot.send_message(chat_id=int(self.admin_chat_id), text=text, parse_mode="HTML")
                event["_notification_message_sent"] = True
                log.info(
                    "[telegram-send-success] trade_id=%s chat_id=%s message_type=admin_debug message_id=%s",
                    event.get("trade_id"),
                    self.admin_chat_id,
                    getattr(message, "message_id", None),
                )
                return True

            return True
        except Exception as exc:
            if event_type == "OPENED":
                await self._record_entry_notification_failure(event, exc)
                await self._emit_entry_notification_failed(event=event, symbol=symbol, user_id=user_id, mode=mode, exc=exc)
            if self._is_terminal_telegram_error(exc):
                await self._mark_event_failed_permanent(event, reason=self._terminal_error_reason(exc))
                event["_notification_status_already_marked"] = True
                log.warning(
                    "[telegram-send-failed-permanent] trade_id=%s event_type=%s user_id=%s symbol=%s reason=%s",
                    event.get("trade_id"),
                    event_type,
                    user_id,
                    symbol,
                    self._terminal_error_reason(exc),
                )
                return True
            if self._send_attempt_count(event) + 1 >= MAX_NOTIFICATION_RETRIES:
                await self._mark_event_failed_permanent(event, reason=f"max_retries:{type(exc).__name__}:{exc}")
                event["_notification_status_already_marked"] = True
                log.warning(
                    "[telegram-send-failed-permanent] trade_id=%s event_type=%s user_id=%s symbol=%s reason=max_retries error=%s:%s",
                    event.get("trade_id"),
                    event_type,
                    user_id,
                    symbol,
                    type(exc).__name__,
                    exc,
                )
                return True
            await self._mark_event_failed_temp(event, reason=f"{type(exc).__name__}: {exc}")
            event["_notification_failed_temp"] = True
            log.warning(
                "[telegram-send-failed-temp] trade_id=%s event_type=%s user=%s symbol=%s attempts=%s exception=%s:%s",
                event.get("trade_id"),
                event_type,
                user_id,
                symbol,
                self._send_attempt_count(event) + 1,
                type(exc).__name__,
                exc,
            )
            log.exception("[telegram-events] failed event_type=%s user_id=%s symbol=%s", event_type, user_id, symbol)
            return False

    async def _emit_stuck_open_notifications_if_needed(self, events: list[dict]) -> None:
        for event in events:
            if not self._is_stuck_open_event(event):
                continue
            if is_strategy_version_test_user(
                user_id=event.get("user_id"),
                telegram_id=(event.get("payload") or {}).get("telegram_id") if isinstance(event.get("payload"), dict) else None,
            ):
                continue
            trade_id = str(event.get("trade_id") or "")
            try:
                if hasattr(self.trade_events_repo, "add_event_once"):
                    await self.trade_events_repo.add_event_once(
                        idempotency_key=f"telegram-notification-stuck:{event.get('_id') or event.get('id') or trade_id}",
                        trade_id=trade_id or f"telegram-notification-stuck:{event.get('_id') or event.get('id')}",
                        event_type="TELEGRAM_NOTIFICATION_STUCK",
                        ts=self._now_ms(),
                        symbol=str(event.get("symbol") or "-"),
                        user_id=str(event.get("user_id") or "system"),
                        mode=str(event.get("mode") or "sandbox"),
                        payload={
                            "severity": "CRITICAL",
                            "reason": "open_message_queued_but_not_sent",
                            "open_message_queued": True,
                            "open_message_sent": False,
                            "queued_age_seconds": self._event_age_seconds(event),
                            "original_event_id": event.get("_id") or event.get("id"),
                            "trade_id": trade_id,
                        },
                    )
            except Exception:
                log.debug("[notification-worker] failed to emit TELEGRAM_NOTIFICATION_STUCK", exc_info=True)

    async def _count_stuck_open_events(self, events: list[dict]) -> int:
        return sum(1 for event in events if self._is_stuck_open_event(event))

    def _is_stuck_open_event(self, event: dict) -> bool:
        return str(event.get("event_type") or "") == "OPENED" and self._event_age_seconds(event) >= self.stuck_after_seconds

    def _event_age_seconds(self, event: dict) -> float:
        ts = self._event_ts_or_now(event)
        return max(0.0, (self._now_ms() - ts) / 1000.0)

    def _send_attempt_count(self, event: dict) -> int:
        try:
            return int(event.get("send_attempt_count") or 0)
        except (TypeError, ValueError):
            return 0

    def _next_retry_at(self, event: dict) -> int:
        delays = [5, 10, 20, 40, 60]
        index = min(self._send_attempt_count(event), len(delays) - 1)
        return self._now_ms() + delays[index] * 1000

    async def _mark_event_skipped(self, event: dict, *, reason: str) -> None:
        event["_notification_status_already_marked"] = True
        event["_notification_skip_reason"] = reason
        event_id = str(event.get("_id") or event.get("id") or "")
        if hasattr(self.trade_events_repo, "mark_event_skipped") and event_id:
            await self.trade_events_repo.mark_event_skipped(event_id, reason=reason)
        elif hasattr(self.trade_events_repo, "mark_sent") and event_id:
            await self.trade_events_repo.mark_sent(event_id)

    async def _mark_event_failed_permanent(self, event: dict, *, reason: str) -> None:
        event["_notification_status_already_marked"] = True
        event["_notification_terminal_handled"] = True
        event_id = str(event.get("_id") or event.get("id") or "")
        if hasattr(self.trade_events_repo, "mark_event_failed_permanent") and event_id:
            await self.trade_events_repo.mark_event_failed_permanent(event_id, reason=reason)
        elif hasattr(self.trade_events_repo, "mark_sent") and event_id:
            await self.trade_events_repo.mark_sent(event_id)

    async def _mark_event_failed_temp(self, event: dict, *, reason: str) -> None:
        event_id = str(event.get("_id") or event.get("id") or "")
        if hasattr(self.trade_events_repo, "mark_event_failed_temp") and event_id:
            await self.trade_events_repo.mark_event_failed_temp(
                event_id,
                reason=reason,
                next_retry_at=self._next_retry_at(event),
            )

    async def _record_entry_notification_attempt(self, event: dict) -> None:
        if self.trades_repo is None or not hasattr(self.trades_repo, "record_entry_notification_attempt"):
            return
        trade_id = str(event.get("trade_id") or "")
        if not trade_id:
            return
        try:
            await self.trades_repo.record_entry_notification_attempt(trade_id=trade_id, attempted_at=self._now_ms())
        except Exception:
            log.debug("[notification-flow] entry notification attempt tracking failed", exc_info=True)

    async def _record_entry_notification_failure(self, event: dict, exc: Exception) -> None:
        if self.trades_repo is None or not hasattr(self.trades_repo, "record_entry_notification_failure"):
            return
        trade_id = str(event.get("trade_id") or "")
        if not trade_id:
            return
        try:
            await self.trades_repo.record_entry_notification_failure(
                trade_id=trade_id,
                attempted_at=self._now_ms(),
                exception=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            log.debug("[notification-flow] entry notification failure tracking failed", exc_info=True)

    async def _mark_entry_notification_sent(self, event: dict) -> None:
        if self.trades_repo is None or not hasattr(self.trades_repo, "mark_entry_notification_sent"):
            return
        trade_id = str(event.get("trade_id") or "")
        if not trade_id:
            return
        sent_at = self._event_ts_or_now(event)
        try:
            await self.trades_repo.mark_entry_notification_sent(trade_id=trade_id, sent_at=sent_at)
        except Exception as exc:
            log.warning(
                "[notification-flow] entry notification tracking failed trade_id=%s error=%s:%s",
                trade_id,
                type(exc).__name__,
                exc,
            )

    async def _emit_entry_notification_failed(
        self,
        *,
        event: dict,
        symbol: str,
        user_id: str,
        mode: str,
        exc: Exception,
    ) -> None:
        trade_id = str(event.get("trade_id") or f"telegram-entry-failed:{symbol}:{user_id}")
        try:
            if hasattr(self.trade_events_repo, "add_event_once"):
                await self.trade_events_repo.add_event_once(
                    idempotency_key=f"telegram-entry-notification-failed:{event.get('id') or event.get('_id') or trade_id}",
                    trade_id=trade_id,
                    event_type="TELEGRAM_ENTRY_NOTIFICATION_FAILED",
                    ts=self._event_ts_or_now(event),
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload={
                        "trade_id": trade_id,
                        "original_event_id": event.get("id") or event.get("_id"),
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                    },
                )
            else:
                await self.trade_events_repo.add_event(
                    trade_id=trade_id,
                    event_type="TELEGRAM_ENTRY_NOTIFICATION_FAILED",
                    ts=self._event_ts_or_now(event),
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload={
                        "trade_id": trade_id,
                        "original_event_id": event.get("id") or event.get("_id"),
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                    },
                )
        except Exception as emit_exc:
            log.warning(
                "[notification-flow] failed to emit TELEGRAM_ENTRY_NOTIFICATION_FAILED trade_id=%s error=%s:%s",
                trade_id,
                type(emit_exc).__name__,
                emit_exc,
            )

    def _event_ts_or_now(self, event: dict) -> int:
        try:
            value = int(event.get("ts") or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else int(time.time() * 1000)

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

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

    def _is_admin_target(self, *, target: dict, chat_id) -> bool:
        if bool(target.get("is_admin")):
            return True
        if not self.admin_chat_id:
            return False
        candidates = {
            str(chat_id or "").strip(),
            str(target.get("telegram_id") or "").strip(),
        }
        return str(self.admin_chat_id).strip() in candidates

    def _is_test_user_notification(self, *, user_id: str, target: dict, payload: dict) -> bool:
        return is_strategy_version_test_user(
            user_id=user_id,
            telegram_id=target.get("telegram_id") or payload.get("telegram_id"),
            email=target.get("email") or payload.get("email"),
            username=target.get("username") or payload.get("username"),
        )

    async def _send_admin_open_copy_if_needed(
        self,
        *,
        event_type: str,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict,
        lang: str,
        user_chat_id: int,
    ) -> None:
        if event_type != "OPENED" or str(mode).lower() != "live" or not self.admin_chat_id:
            return
        try:
            admin_chat_id = int(self.admin_chat_id)
        except (TypeError, ValueError):
            return
        if admin_chat_id == int(user_chat_id):
            return

        text = self.message_builder.build(
            event_type=event_type,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
            lang=lang,
            is_admin=True,
        )
        try:
            await self.bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="HTML")
        except Exception as exc:
            if self._is_terminal_telegram_error(exc):
                log.warning(
                    "[telegram-events] terminal admin delivery failure event_type=%s user_id=%s symbol=%s error=%s",
                    event_type,
                    user_id,
                    symbol,
                    exc,
                )
                return
            log.warning(
                "[telegram-events] admin copy failed event_type=%s user_id=%s symbol=%s error=%s:%s",
                event_type,
                user_id,
                symbol,
                type(exc).__name__,
                exc,
            )
