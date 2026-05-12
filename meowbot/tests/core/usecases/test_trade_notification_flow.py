from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from meowbot.core.services.notifications.risk_subscription_message_builder import (
    RiskSubscriptionMessageBuilder,
)
from meowbot.core.services.notifications.trade_event_message_builder import (
    TradeEventMessageBuilder,
)
from meowbot.core.usecases.async_send_risk_events_to_telegram import (
    AsyncSendRiskEventsToTelegramUseCase,
)
from meowbot.core.usecases.async_send_trade_events_to_telegram import (
    AsyncSendTradeEventsToTelegramUseCase,
)


class FakeBot:
    def __init__(self, exc: Exception | None = None) -> None:
        self.messages: list[dict] = []
        self.exc = exc

    async def send_message(self, **kwargs) -> None:
        if self.exc is not None:
            raise self.exc
        self.messages.append(kwargs)


class FakeTradeEventsRepo:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = list(events or [])
        self.sent: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str, str | None]] = []
        self.failed_permanent: list[tuple[str, str, str | None]] = []
        self.failed_temp: list[tuple[str, str, int]] = []
        self.added: list[dict] = []

    async def get_unsent_events(self, limit: int = 100, **kwargs) -> list[dict]:
        return [
            event for event in self.events
            if event.get("notification_status") not in {"FAILED_PERMANENT", "SKIPPED_DEBUG", "SKIPPED_INVALID_CHAT", "SKIPPED", "SENT"}
        ][:limit]

    async def get_pending_risk_events(self) -> list[dict]:
        return [
            event for event in self.events
            if event.get("notification_status") not in {"FAILED_PERMANENT", "SKIPPED_DEBUG", "SKIPPED_INVALID_CHAT", "SKIPPED", "SENT"}
        ]

    async def mark_sent(self, event_id: str) -> None:
        self.sent.append(str(event_id))

    async def mark_event_delivered(self, event_id: str, *, channel: str) -> None:
        self.delivered.append((str(event_id), channel))
        for event in self.events:
            if str(event.get("id") or event.get("_id")) == str(event_id):
                event["notification_status"] = "SENT"

    async def mark_event_skipped(self, event_id: str, *, reason: str, channel: str | None = None) -> None:
        self.skipped.append((str(event_id), reason, channel))
        for event in self.events:
            if str(event.get("id") or event.get("_id")) == str(event_id):
                event["notification_status"] = "SKIPPED_DEBUG" if reason == "debug_event" else "SKIPPED_INVALID_CHAT" if reason == "invalid_chat" else "SKIPPED"

    async def mark_event_failed_permanent(self, event_id: str, *, reason: str, channel: str | None = None) -> None:
        self.failed_permanent.append((str(event_id), reason, channel))
        for event in self.events:
            if str(event.get("id") or event.get("_id")) == str(event_id):
                event["notification_status"] = "FAILED_PERMANENT"
                event["retryable"] = False

    async def mark_event_failed_temp(self, event_id: str, *, reason: str, next_retry_at: int) -> None:
        self.failed_temp.append((str(event_id), reason, int(next_retry_at)))
        for event in self.events:
            if str(event.get("id") or event.get("_id")) == str(event_id):
                event["notification_status"] = "FAILED_TEMP"
                event["retryable"] = True
                event["next_retry_at"] = int(next_retry_at)

    async def add_event_once(self, **kwargs):
        self.added.append(kwargs)
        return "diagnostic-id", True


class FakeTargetsRepo:
    def __init__(self, target: dict | None = None) -> None:
        self.target = target or {}

    async def resolve_notification_target_by_user_id(self, user_id: str) -> dict:
        row = {
            "chat_id": 123,
            "telegram_id": 123,
            "preferred_language": "uk",
            "notifications_enabled": True,
            "notify_trade_opened": True,
            "notify_system": True,
        }
        row.update(self.target)
        return row


class FakeTradesRepo:
    def __init__(self, *, entry_notification_sent: bool = False) -> None:
        self.trade = SimpleNamespace(entry_notification_sent=entry_notification_sent)
        self.entry_sent: list[tuple[str, int]] = []
        self.risk_sent: list[tuple[str, int]] = []

    async def get_trade_by_trade_id(self, trade_id: str):
        return self.trade

    async def mark_entry_notification_sent(self, *, trade_id: str, sent_at: int) -> None:
        self.entry_sent.append((trade_id, sent_at))
        self.trade.entry_notification_sent = True

    async def mark_risk_warning_sent(self, *, trade_id: str, sent_at: int) -> None:
        self.risk_sent.append((trade_id, sent_at))


@pytest.mark.anyio
async def test_open_event_marks_entry_notification_sent() -> None:
    event = {
        "_id": "event-open",
        "id": "event-open",
        "trade_id": "trade-1",
        "event_type": "OPENED",
        "ts": 123_456,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
            "tp_count": 1,
            "tp_levels": [0.202],
            "tp_close_fractions": [1.0],
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo()
    bot = FakeBot()
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=trades_repo,
    )

    sent = await usecase.run_once()

    assert sent == 1
    assert bot.messages
    assert events_repo.sent == ["event-open"]
    assert trades_repo.entry_sent == [("trade-1", 123_456)]


@pytest.mark.anyio
async def test_open_event_skips_synthetic_test_user_without_telegram_send() -> None:
    event = {
        "_id": "event-open",
        "id": "event-open",
        "trade_id": "trade-test",
        "event_type": "OPENED",
        "ts": 123_456,
        "symbol": "HUSDT",
        "user_id": "tg:900011301",
        "mode": "sandbox",
        "payload": {
            "telegram_id": 900011301,
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo()
    bot = FakeBot()
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo({"telegram_id": 900011301, "chat_id": 900011301}),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=trades_repo,
    )

    sent = await usecase.run_once()

    assert sent == 1
    assert bot.messages == []
    assert events_repo.sent == ["event-open"]
    assert events_repo.added == []
    assert trades_repo.entry_sent == [("trade-test", 123_456)]


@pytest.mark.anyio
async def test_stuck_open_event_emits_diagnostic_and_still_sends() -> None:
    old_ts = int(time.time() * 1000) - 31_000
    event = {
        "_id": "event-open-stuck",
        "id": "event-open-stuck",
        "trade_id": "trade-stuck",
        "event_type": "OPENED",
        "ts": old_ts,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo()
    bot = FakeBot()
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=trades_repo,
        stuck_after_seconds=30,
    )

    sent = await usecase.run_once()

    assert sent == 1
    assert bot.messages
    assert events_repo.sent == ["event-open-stuck"]
    assert events_repo.added[0]["event_type"] == "TELEGRAM_NOTIFICATION_STUCK"
    assert events_repo.added[0]["payload"]["severity"] == "CRITICAL"
    assert trades_repo.entry_sent == [("trade-stuck", old_ts)]


@pytest.mark.anyio
async def test_risk_warning_waits_for_entry_notification_sent() -> None:
    event = {
        "id": "event-risk",
        "trade_id": "trade-1",
        "event_type": "ENTRY_WARNING_RISK",
        "ts": 123_457,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {
            "entry_trade_id": "trade-1",
            "entry_notification_required": True,
            "tf_entry": "15m",
            "current_margin_ratio_pct": 7.5,
            "warning_code": "margin_ratio_warning",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo(entry_notification_sent=False)
    bot = FakeBot()
    usecase = AsyncSendRiskEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        runtime_repo=FakeTargetsRepo(),
        bot=bot,
        builder=RiskSubscriptionMessageBuilder(),
        trades_repo=trades_repo,
    )

    assert await usecase.run_once() == 0
    assert bot.messages == []
    assert events_repo.delivered == []
    assert events_repo.added[0]["event_type"] == "RISK_WARNING_WITHOUT_ENTRY_NOTIFICATION"

    trades_repo.trade.entry_notification_sent = True
    assert await usecase.run_once() == 1
    assert bot.messages
    assert events_repo.delivered == [("event-risk", "telegram_risk")]
    assert trades_repo.risk_sent == [("trade-1", 123_457)]


@pytest.mark.anyio
async def test_risk_warning_skips_synthetic_test_user() -> None:
    event = {
        "id": "event-risk",
        "trade_id": "trade-test",
        "event_type": "ENTRY_WARNING_RISK",
        "ts": 123_457,
        "symbol": "HUSDT",
        "user_id": "tg:900011301",
        "mode": "sandbox",
        "payload": {
            "telegram_id": 900011301,
            "entry_trade_id": "trade-test",
            "entry_notification_required": True,
            "tf_entry": "15m",
            "current_margin_ratio_pct": 7.5,
            "warning_code": "margin_ratio_warning",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo(entry_notification_sent=False)
    bot = FakeBot()
    usecase = AsyncSendRiskEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        runtime_repo=FakeTargetsRepo({"telegram_id": 900011301, "chat_id": 900011301}),
        bot=bot,
        builder=RiskSubscriptionMessageBuilder(),
        trades_repo=trades_repo,
    )

    assert await usecase.run_once() == 0
    assert bot.messages == []
    assert events_repo.added == []
    assert events_repo.delivered == [("event-risk", "telegram_risk")]
    assert trades_repo.risk_sent == [("trade-test", 123_457)]


@pytest.mark.anyio
async def test_debug_risk_event_is_skipped_without_telegram_send() -> None:
    event = {
        "id": "event-debug-risk",
        "trade_id": "debug:global:ETHUSDT:30m:1:tg:123456789",
        "event_type": "ENTRY_WARNING_RISK",
        "ts": 123_457,
        "symbol": "ETHUSDT",
        "user_id": "tg:123456789",
        "mode": "sandbox",
        "payload": {"tf_entry": "30m"},
    }
    events_repo = FakeTradeEventsRepo([event])
    bot = FakeBot()
    usecase = AsyncSendRiskEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        runtime_repo=FakeTargetsRepo({"telegram_id": 123456789, "chat_id": 123456789}),
        bot=bot,
        builder=RiskSubscriptionMessageBuilder(),
    )

    assert await usecase.run_once() == 0
    assert bot.messages == []
    assert events_repo.skipped == [("event-debug-risk", "debug_event", "telegram_risk")]


@pytest.mark.anyio
async def test_risk_event_invalid_placeholder_chat_is_skipped() -> None:
    event = {
        "id": "event-placeholder-risk",
        "trade_id": "trade-risk",
        "event_type": "ENTRY_BLOCKED_RISK",
        "ts": 123_457,
        "symbol": "ETHUSDT",
        "user_id": "tg:123456789",
        "mode": "sandbox",
        "payload": {"tf_entry": "30m"},
    }
    events_repo = FakeTradeEventsRepo([event])
    bot = FakeBot()
    usecase = AsyncSendRiskEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        runtime_repo=FakeTargetsRepo({"telegram_id": 123456789, "chat_id": 123456789}),
        bot=bot,
        builder=RiskSubscriptionMessageBuilder(),
    )

    assert await usecase.run_once() == 0
    assert bot.messages == []
    assert events_repo.skipped == [("event-placeholder-risk", "invalid_chat", "telegram_risk")]


@pytest.mark.anyio
async def test_trade_event_chat_not_found_is_failed_permanent() -> None:
    event = {
        "_id": "event-open-bad-chat",
        "id": "event-open-bad-chat",
        "trade_id": "trade-bad-chat",
        "event_type": "OPENED",
        "ts": 123_456,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    trades_repo = FakeTradesRepo()
    bot = FakeBot(TelegramBadRequest(method=None, message="Bad Request: chat not found"))
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=trades_repo,
    )

    assert await usecase.run_once() == 1
    assert events_repo.sent == []
    assert events_repo.failed_permanent == [("event-open-bad-chat", "chat_not_found", None)]


@pytest.mark.anyio
async def test_trade_event_forbidden_is_failed_permanent() -> None:
    event = {
        "_id": "event-stop-blocked",
        "id": "event-stop-blocked",
        "trade_id": "trade-blocked",
        "event_type": "STOP",
        "ts": 123_456,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {"realized_pnl_usd": -0.1},
    }
    events_repo = FakeTradeEventsRepo([event])
    bot = FakeBot(TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"))
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
    )

    assert await usecase.run_once() == 1
    assert events_repo.sent == []
    assert events_repo.failed_permanent == [("event-stop-blocked", "bot_was_blocked_by_the_user", None)]


@pytest.mark.anyio
async def test_trade_event_temporary_error_gets_failed_temp_retry() -> None:
    event = {
        "_id": "event-open-temp",
        "id": "event-open-temp",
        "trade_id": "trade-temp",
        "event_type": "OPENED",
        "ts": 123_456,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "payload": {
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    bot = FakeBot(RuntimeError("temporary telegram outage"))
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=FakeTradesRepo(),
    )

    assert await usecase.run_once() == 0
    assert events_repo.sent == []
    assert len(events_repo.failed_temp) == 1
    assert events_repo.failed_temp[0][0] == "event-open-temp"


@pytest.mark.anyio
async def test_trade_event_max_retries_becomes_failed_permanent() -> None:
    event = {
        "_id": "event-open-max",
        "id": "event-open-max",
        "trade_id": "trade-max",
        "event_type": "OPENED",
        "ts": 123_456,
        "symbol": "SEIUSDT",
        "user_id": "tg:1",
        "mode": "live",
        "send_attempt_count": 4,
        "payload": {
            "side": "LONG",
            "entry_price": 0.2,
            "sl_price": 0.19,
            "qty": 10,
            "stake_usd": 1,
            "leverage": 5,
            "tf_entry": "15m",
        },
    }
    events_repo = FakeTradeEventsRepo([event])
    bot = FakeBot(RuntimeError("temporary telegram outage"))
    usecase = AsyncSendTradeEventsToTelegramUseCase(
        trade_events_repo=events_repo,
        telegram_users_repo=FakeTargetsRepo(),
        bot=bot,
        message_builder=TradeEventMessageBuilder(),
        admin_chat_id=None,
        trades_repo=FakeTradesRepo(),
    )

    assert await usecase.run_once() == 1
    assert events_repo.failed_temp == []
    assert events_repo.failed_permanent[0][0] == "event-open-max"
    assert events_repo.events[0]["retryable"] is False
