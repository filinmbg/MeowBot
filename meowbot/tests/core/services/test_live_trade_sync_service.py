from __future__ import annotations

from dataclasses import replace

import pytest

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.execution.live_trade_sync_service import LiveTradeSyncService
from meowbot.infra.exchange.binance_futures_account_async import ExchangeStateFetchError


def _trade(*, position_mode: str = "hedge", position_side: str = "LONG") -> Trade:
    return Trade(
        trade_id="tg:1:INJUSDT:15m:1:rule",
        user_id="tg:1",
        symbol="INJUSDT",
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=10.0,
        qty=1.0,
        leverage=5,
        stake_usd=2.0,
        tf_entry="15m",
        model_id="rule",
        entry_bar_close_time=1,
        sl_price=9.8,
        mode="live",
        exchange_tp_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "clientOrderId": "mb-t1",
                "stage": 1,
                "status": "NEW",
                "origQty": 0.7,
                "triggerPrice": 10.05,
            }
        ],
        exchange_stop_order={
            "algoId": "21",
            "clientAlgoId": "mb-s0",
            "clientOrderId": "mb-s0",
            "status": "NEW",
            "origQty": 1.0,
            "triggerPrice": 9.8,
        },
        exchange_order_ids=["11", "21"],
        exchange_position_mode=position_mode,
        exchange_position_side=position_side,
    )


def _service() -> LiveTradeSyncService:
    return LiveTradeSyncService(
        trades_repo=object(),
        trade_events_repo=object(),
        account_provider=object(),
        live_broker=object(),
    )


def test_refresh_tp_orders_matches_algo_order_snapshots() -> None:
    service = _service()
    trade = _trade()

    refreshed = service._refresh_tp_orders(
        trade,
        all_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "0.7",
                "actualPrice": "10.05",
                "triggerPrice": "10.05",
            }
        ],
        open_orders=[],
    )

    assert refreshed[0]["status"] == "FILLED"
    assert refreshed[0]["type"] == "TAKE_PROFIT_MARKET"
    assert refreshed[0]["executedQty"] == 0.7
    assert refreshed[0]["avgPrice"] == 10.05
    assert refreshed[0]["stopPrice"] == 10.05
    assert refreshed[0]["clientOrderId"] == "mb-t1"


def test_open_stop_detection_matches_algo_ids() -> None:
    service = _service()
    trade = _trade()

    assert service._has_open_stop_order(
        trade,
        [
            {
                "algoId": "21",
                "clientAlgoId": "mb-s0",
                "algoStatus": "NEW",
            }
        ],
    )


def test_refresh_order_ids_keeps_algo_identifiers() -> None:
    service = _service()
    trade = _trade()

    order_ids = service._refresh_order_ids(
        trade,
        all_orders=[
            {"algoId": "33", "actualOrderId": "333", "clientAlgoId": "mb-t1"},
            {"algoId": "99", "actualOrderId": "999", "clientAlgoId": "mb-other-trade"},
        ],
        open_orders=[{"algoId": "44", "clientAlgoId": "mb-other-trade"}],
    )

    assert order_ids == ["11", "21", "33", "333"]


def test_position_match_uses_expected_hedge_side() -> None:
    service = _service()
    trade = _trade(position_mode="hedge", position_side="LONG")

    match = service._match_exchange_position(
        trade,
        [
            {"symbol": "INJUSDT", "positionSide": "SHORT", "positionAmt": "0"},
            {"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "2.5"},
        ],
    )

    assert match["ambiguous"] is False
    assert match["confident_flat"] is False
    assert match["position_qty"] == 2.5
    assert match["matched_position_side"] == "LONG"


def test_position_match_is_ambiguous_without_expected_side() -> None:
    service = _service()
    trade = _trade(position_mode="", position_side="")

    match = service._match_exchange_position(
        trade,
        [{"symbol": "INJUSDT", "positionAmt": "0"}],
    )

    assert match["ambiguous"] is True
    assert match["confident_flat"] is False
    assert match["reason"] == "expected_position_side_missing"


class FakeTradeEventsRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def add_event_once(self, **kwargs) -> None:
        self.events.append(dict(kwargs))


class FakeClient:
    def __init__(
        self,
        *,
        positions: list[dict],
        open_orders: list[dict] | None = None,
        all_orders: list[dict] | None = None,
        open_algo_orders: list[dict] | None = None,
        all_algo_orders: list[dict] | None = None,
        income_history: list[dict] | None = None,
    ) -> None:
        self.positions = positions
        self.open_orders = open_orders or []
        self.all_orders = all_orders or []
        self.open_algo_orders = open_algo_orders
        self.all_algo_orders = all_algo_orders or []
        self.income_history = income_history
        self.cancel_all_orders_calls: list[dict] = []
        self.cancel_all_algo_orders_calls: list[dict] = []
        self.income_history_calls: list[dict] = []
        self.closed = False

    async def get_open_orders(self, *, symbol: str, raise_on_error: bool = False):
        return self.open_orders

    async def get_all_orders(self, *, symbol: str, limit: int = 50, raise_on_error: bool = False):
        return self.all_orders

    async def get_open_algo_orders(self, *, symbol: str, raise_on_error: bool = False):
        if self.open_algo_orders is not None:
            return self.open_algo_orders
        return [{"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW"}]

    async def get_all_algo_orders(self, *, symbol: str, limit: int = 50, raise_on_error: bool = False):
        return self.all_algo_orders

    async def get_position_risk(self, *, symbol: str, raise_on_error: bool = False):
        return self.positions

    async def get_income_history(
        self,
        *,
        symbol: str,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
        raise_on_error: bool = False,
    ):
        self.income_history_calls.append(
            {
                "symbol": symbol,
                "income_type": income_type,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            }
        )
        rows = self.income_history
        if rows is None:
            rows = [
                {
                    "symbol": symbol,
                    "incomeType": "REALIZED_PNL",
                    "income": "0.12",
                    "time": int(end_time or start_time or 1000),
                }
            ]
        result = []
        for row in rows:
            if str(row.get("symbol") or "").upper() != str(symbol).upper():
                continue
            if income_type and str(row.get("incomeType") or "").upper() != str(income_type).upper():
                continue
            result.append(dict(row))
        return result

    async def cancel_all_orders(self, *, symbol: str):
        self.cancel_all_orders_calls.append({"symbol": symbol})
        return {"status": "ok"}

    async def cancel_all_algo_orders(self, *, symbol: str):
        self.cancel_all_algo_orders_calls.append({"symbol": symbol})
        return {"status": "ok"}

    async def close(self):
        self.closed = True


class FakeAccountProvider:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.build_calls = 0

    async def build_client(self, *, runtime_user_id: str, require_active: bool):
        self.build_calls += 1
        return self.client, {"validation_status": "valid"}, None


class FakeTradesRepo:
    pass


class FakeLiveBroker:
    def __init__(self) -> None:
        self.replace_stop_order_calls: list[dict] = []
        self.close_position_market_calls: list[dict] = []

    async def replace_stop_order(self, trade, **kwargs):
        self.replace_stop_order_calls.append({"trade_id": trade.trade_id, **kwargs})
        return replace(
            trade,
            exchange_stop_order={
                "algoId": "replaced-stop",
                "clientAlgoId": "replaced-stop",
                "status": "NEW",
                "origQty": trade.qty_remaining,
                "triggerPrice": kwargs.get("new_stop_price"),
            },
        )

    async def close_position_market(self, trade, *, now_ms: int, reason: str):
        self.close_position_market_calls.append(
            {"trade_id": trade.trade_id, "now_ms": now_ms, "reason": reason}
        )
        return replace(
            trade,
            exchange_last_sync_at=now_ms,
            exchange_last_sync_reason=reason,
            exchange_sync_status="manual_close_sent",
        )


class FailingPositionRefreshClient(FakeClient):
    def __init__(self, *, positions: list[dict], refresh_error: Exception, **kwargs) -> None:
        super().__init__(positions=positions, **kwargs)
        self.refresh_error = refresh_error
        self.position_call_count = 0

    async def get_position_risk(self, *, symbol: str, raise_on_error: bool = False):
        self.position_call_count += 1
        if self.position_call_count >= 2 and raise_on_error:
            raise self.refresh_error
        return self.positions


class FailingOpenOrdersClient(FakeClient):
    async def get_open_orders(self, *, symbol: str, raise_on_error: bool = False):
        if raise_on_error:
            raise ExchangeStateFetchError(
                "get_open_orders_failed:INJUSDT:HTTPStatusError:HTTP 400",
                details={
                    "operation": "get_open_orders_failed",
                    "symbol": symbol,
                    "http_status": 400,
                    "http_path": "/fapi/v1/openOrders",
                    "exchange_error_code": -1021,
                    "exchange_error_message": "Timestamp for this request is outside of the recvWindow.",
                    "response_body": '{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}',
                },
            )
        return self.open_orders


class SoftStopCloseClient(FakeClient):
    def __init__(self, *, open_position: dict, flat_position: dict, **kwargs) -> None:
        super().__init__(positions=[open_position], **kwargs)
        self.open_position = open_position
        self.flat_position = flat_position
        self.position_call_count = 0

    async def get_position_risk(self, *, symbol: str, raise_on_error: bool = False):
        self.position_call_count += 1
        if self.position_call_count >= 2:
            return [self.flat_position]
        return [self.open_position]


async def _sync_with_positions(positions: list[dict]):
    client = FakeClient(positions=positions)
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )
    current = await service.sync_trade(
        _trade(position_mode="hedge", position_side="LONG"),
        now_ms=1000,
        reason="poll",
        force=True,
    )
    return current, events.events, client


@pytest.mark.anyio
async def test_sync_error_includes_binance_response_details_and_skips_update() -> None:
    client = FailingOpenOrdersClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        _trade(position_mode="hedge", position_side="LONG"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.exchange_sync_status == "error"
    sync_errors = [event for event in events.events if event["event_type"] == "LIVE_SYNC_ERROR"]
    assert sync_errors
    payload = sync_errors[0]["payload"]
    assert payload["decision"] == "skip_update"
    assert payload["http_status"] == 400
    assert payload["http_path"] == "/fapi/v1/openOrders"
    assert payload["exchange_error_code"] == -1021
    assert payload["exchange_error_message"] == "Timestamp for this request is outside of the recvWindow."
    assert payload["response_body"] == '{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}'


@pytest.mark.anyio
async def test_sync_trade_does_not_close_when_other_hedge_side_is_flat() -> None:
    current, events, client = await _sync_with_positions(
        [
            {"symbol": "INJUSDT", "positionSide": "SHORT", "positionAmt": "0"},
            {"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "2.5"},
        ]
    )

    assert current.status == TradeStatus.OPEN
    assert current.exchange_position_amt == 2.5
    assert client.closed is True
    assert not [event for event in events if event["event_type"] == "CLOSED"]


@pytest.mark.anyio
async def test_sync_trade_keeps_open_when_position_match_is_ambiguous() -> None:
    current, events, _ = await _sync_with_positions(
        [{"symbol": "INJUSDT", "positionAmt": "0"}]
    )

    assert current.status == TradeStatus.OPEN
    assert current.exchange_sync_status == "position_match_ambiguous"
    assert not [event for event in events if event["event_type"] == "CLOSED"]
    mismatch_events = [event for event in events if event["event_type"] == "LIVE_SYNC_MISMATCH"]
    assert mismatch_events
    assert mismatch_events[0]["payload"]["reason"] == "position_match_ambiguous"


@pytest.mark.anyio
async def test_sync_trade_marks_confirmed_flat_position_as_reconciled_manual_close() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
        all_orders=[
            {
                "symbol": "INJUSDT",
                "side": "SELL",
                "status": "FILLED",
                "type": "MARKET",
                "avgPrice": "10.12",
                "executedQty": "1",
                "positionSide": "BOTH",
                "updateTime": 999,
            }
        ],
        open_algo_orders=[
            {"algoId": "11", "clientAlgoId": "mb-t1", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        _trade(position_mode="one_way", position_side="BOTH"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "EXCHANGE_POSITION_FLAT"
    assert current.close_price == pytest.approx(10.12)
    assert current.realized_pnl_usd == pytest.approx(0.12)
    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]
    assert not [event for event in events.events if event["event_type"] == "LIVE_SYNC_MISMATCH"]
    reconciled_events = [event for event in events.events if event["event_type"] == "LIVE_SYNC_RECONCILED"]
    assert reconciled_events
    assert reconciled_events[0]["payload"]["reason"] == "exchange_position_flat"
    assert reconciled_events[0]["payload"]["detected_close_source"] == "exchange"
    assert reconciled_events[0]["payload"]["close_price_source"] == "exchange_filled_close_order"


@pytest.mark.anyio
async def test_sync_trade_uses_exchange_net_pnl_for_live_close() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
        all_orders=[
            {
                "symbol": "INJUSDT",
                "side": "SELL",
                "status": "FILLED",
                "type": "MARKET",
                "avgPrice": "10.12",
                "executedQty": "1",
                "positionSide": "BOTH",
                "updateTime": 999,
            }
        ],
        income_history=[
            {"symbol": "INJUSDT", "incomeType": "REALIZED_PNL", "income": "0.0385", "time": 1000},
            {"symbol": "INJUSDT", "incomeType": "COMMISSION", "income": "-0.00256", "time": 1000},
            {"symbol": "INJUSDT", "incomeType": "COMMISSION", "income": "-0.00258", "time": 1000},
        ],
    )
    events = FakeTradeEventsRepo()
    provider = FakeAccountProvider(client)
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )

    current = await service.sync_trade(
        _trade(position_mode="one_way", position_side="BOTH"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.CLOSED
    assert current.local_realized_pnl_usd == pytest.approx(0.12)
    assert current.exchange_realized_pnl_usd == pytest.approx(0.03336)
    assert current.exchange_gross_realized_pnl_usd == pytest.approx(0.0385)
    assert current.exchange_net_realized_pnl_usd == pytest.approx(0.03336)
    assert current.exchange_commission_usd == pytest.approx(-0.00514)
    assert current.realized_pnl_usd == pytest.approx(0.03336)
    assert current.pnl_source == "exchange"
    closed_events = [event for event in events.events if event["event_type"] == "CLOSED"]
    assert closed_events
    assert closed_events[0]["payload"]["realized_pnl_usd"] == pytest.approx(0.03336)
    assert closed_events[0]["payload"]["exchange_realized_pnl_usd"] == pytest.approx(0.03336)
    assert closed_events[0]["payload"]["exchange_net_realized_pnl_usd"] == pytest.approx(0.03336)
    assert closed_events[0]["payload"]["exchange_gross_realized_pnl_usd"] == pytest.approx(0.0385)
    assert closed_events[0]["payload"]["exchange_commission_usd"] == pytest.approx(-0.00514)
    mismatch_events = [event for event in events.events if event["event_type"] == "LIVE_PNL_MISMATCH"]
    assert mismatch_events
    assert mismatch_events[0]["payload"]["local_pnl"] == pytest.approx(0.12)
    assert mismatch_events[0]["payload"]["exchange_realized_pnl"] == pytest.approx(0.0385)
    assert mismatch_events[0]["payload"]["exchange_net_realized_pnl"] == pytest.approx(0.03336)
    assert mismatch_events[0]["payload"]["commission"] == pytest.approx(-0.00514)
    assert mismatch_events[0]["payload"]["final_pnl"] == pytest.approx(0.03336)
    assert mismatch_events[0]["payload"]["exchange_pnl"] == pytest.approx(0.03336)
    assert mismatch_events[0]["payload"]["gross_difference"] == pytest.approx(abs(0.0385 - 0.12))
    assert mismatch_events[0]["payload"]["net_difference"] == pytest.approx(abs(0.03336 - 0.12))
    assert mismatch_events[0]["payload"]["difference_local_vs_gross"] == pytest.approx(0.0385 - 0.12)
    assert mismatch_events[0]["payload"]["difference_local_vs_net"] == pytest.approx(0.03336 - 0.12)


@pytest.mark.anyio
async def test_sync_trade_quarantines_invalid_symbol_without_binance_call() -> None:
    client = FakeClient(
        positions=[{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
    )
    provider = FakeAccountProvider(client)
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )
    trade = replace(
        _trade(position_mode="one_way", position_side="BOTH"),
        symbol="\u5e01\u5b89\u4eba\u751fUSDT",
        trade_id="tg:1:bad:15m:1:rule",
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.ERROR_INVALID_SYMBOL
    assert current.exchange_sync_status == "invalid_symbol"
    assert current.exit_reason == "ERROR_INVALID_SYMBOL"
    assert provider.build_calls == 0
    invalid_events = [event for event in events.events if event["event_type"] == "INVALID_SYMBOL_SKIPPED"]
    assert invalid_events
    assert invalid_events[0]["payload"]["action"] == "quarantined_without_binance_call"


@pytest.mark.anyio
async def test_sync_trade_does_not_emit_mismatch_for_commission_only_difference() -> None:
    trade = replace(
        _trade(position_mode="one_way", position_side="BOTH"),
        local_realized_pnl_usd=0.0,
        realized_pnl_usd=0.0,
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
        all_orders=[
            {
                "symbol": "INJUSDT",
                "side": "SELL",
                "status": "FILLED",
                "type": "MARKET",
                "avgPrice": "10.0385",
                "executedQty": "1",
                "positionSide": "BOTH",
                "updateTime": 999,
            }
        ],
        income_history=[
            {"symbol": "INJUSDT", "incomeType": "REALIZED_PNL", "income": "0.0385", "time": 1000},
            {"symbol": "INJUSDT", "incomeType": "COMMISSION", "income": "-0.00256", "time": 1000},
            {"symbol": "INJUSDT", "incomeType": "COMMISSION", "income": "-0.00258", "time": 1000},
        ],
    )
    events = FakeTradeEventsRepo()
    provider = FakeAccountProvider(client)
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.CLOSED
    assert current.local_realized_pnl_usd == pytest.approx(0.0385)
    assert current.exchange_gross_realized_pnl_usd == pytest.approx(0.0385)
    assert current.exchange_commission_usd == pytest.approx(-0.00514)
    assert current.exchange_net_realized_pnl_usd == pytest.approx(0.03336)
    assert current.realized_pnl_usd == pytest.approx(0.03336)
    assert current.pnl_source == "exchange"

    mismatch_events = [event for event in events.events if event["event_type"] == "LIVE_PNL_MISMATCH"]
    assert mismatch_events == []

    commission_events = [
        event for event in events.events if event["event_type"] == "LIVE_PNL_COMMISSION_DIFFERENCE"
    ]
    assert commission_events
    payload = commission_events[0]["payload"]
    assert payload["severity"] == "INFO"
    assert payload["gross_pnl"] == pytest.approx(0.0385)
    assert payload["commission"] == pytest.approx(-0.00514)
    assert payload["net_pnl"] == pytest.approx(0.03336)
    assert payload["gross_difference"] == pytest.approx(0.0)
    assert payload["net_difference"] == pytest.approx(abs(0.03336 - 0.0385))
    assert payload["decision"] == "commission_only_difference"


@pytest.mark.anyio
async def test_sync_trade_sums_lab_exchange_realized_pnl_and_commissions() -> None:
    service_seed = _service()
    base_trade = replace(
        _trade(position_mode="one_way", position_side="BOTH"),
        trade_id="tg:853048829:LABUSDT:15m:177:LONG_BREAKOUT_V18",
        user_id="tg:853048829",
        symbol="LABUSDT",
        opened_at=100_000,
        closed_at=160_000,
        entry_bar_close_time=100_000,
        entry_price=1.0,
        close_price=0.921,
        qty=1.0,
        qty_remaining=0.0,
        local_realized_pnl_usd=-0.1155,
        realized_pnl_usd=-0.1155,
    )
    digest = service_seed._trade_client_id_digest(base_trade)
    trade = replace(
        base_trade,
        exchange_entry_client_order_id=f"mb-e-{digest}",
        exchange_stop_order={
            "algoId": "current-stop",
            "clientAlgoId": f"mb-s0-{digest}",
            "clientOrderId": f"mb-s0-{digest}",
            "status": "NEW",
            "positionSide": "BOTH",
        },
        exchange_tp_orders=[],
        exchange_order_ids=["polluted-old-order"],
    )
    current_close_order = {
        "symbol": "LABUSDT",
        "side": "SELL",
        "algoStatus": "FINISHED",
        "orderType": "STOP_MARKET",
        "actualPrice": "0.921",
        "quantity": "1",
        "positionSide": "BOTH",
        "algoId": "current-stop",
        "clientAlgoId": f"mb-s0-{digest}",
        "updateTime": 160_000,
    }
    unrelated_same_symbol_order = {
        "symbol": "LABUSDT",
        "side": "SELL",
        "algoStatus": "FINISHED",
        "orderType": "STOP_MARKET",
        "actualPrice": "0.91",
        "quantity": "1",
        "positionSide": "BOTH",
        "algoId": "old-stop",
        "clientAlgoId": "mb-s0-unrelatedtrade",
        "updateTime": 159_000,
    }
    client = FakeClient(
        positions=[{"symbol": "LABUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
        all_algo_orders=[current_close_order, unrelated_same_symbol_order],
        income_history=[
            {"symbol": "LABUSDT", "incomeType": "REALIZED_PNL", "income": "-0.079", "time": 160_000},
            {"symbol": "LABUSDT", "incomeType": "COMMISSION", "income": "-0.00467430", "time": 160_000},
            {"symbol": "LABUSDT", "incomeType": "COMMISSION", "income": "-0.00463479", "time": 160_001},
            {
                "symbol": "LABUSDT",
                "incomeType": "REALIZED_PNL",
                "income": "-1.0",
                "time": 160_000,
                "clientAlgoId": "mb-s0-unrelatedtrade",
            },
            {
                "symbol": "LABUSDT",
                "incomeType": "COMMISSION",
                "income": "-1.0",
                "time": 160_000,
                "clientAlgoId": "mb-s0-unrelatedtrade",
            },
        ],
    )
    events = FakeTradeEventsRepo()
    provider = FakeAccountProvider(client)
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )

    current = await service.sync_trade(trade, now_ms=160_000, reason="poll", force=True)

    assert current.status == TradeStatus.CLOSED
    assert current.exchange_gross_realized_pnl_usd == pytest.approx(-0.079)
    assert current.exchange_commission_usd == pytest.approx(-0.00930909)
    assert current.exchange_net_realized_pnl_usd == pytest.approx(-0.08830909)
    assert current.exchange_realized_pnl_usd == pytest.approx(-0.08830909)
    assert current.realized_pnl_usd == pytest.approx(-0.08830909)
    mismatch_events = [event for event in events.events if event["event_type"] == "LIVE_PNL_MISMATCH"]
    assert mismatch_events
    payload = mismatch_events[0]["payload"]
    assert payload["exchange_gross_realized_pnl"] == pytest.approx(-0.079)
    assert payload["exchange_commission"] == pytest.approx(-0.00930909)
    assert payload["exchange_net_realized_pnl"] == pytest.approx(-0.08830909)
    assert payload["difference_local_vs_net"] == pytest.approx(-0.08830909 - (-0.1155))
    assert payload["matched_order_count"] >= 1
    assert payload["suspicious_extra_order_count"] == 1


@pytest.mark.anyio
async def test_sync_trade_defers_final_close_message_when_exchange_pnl_missing() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0"}],
        all_orders=[
            {
                "symbol": "INJUSDT",
                "side": "SELL",
                "status": "FILLED",
                "type": "MARKET",
                "avgPrice": "10.12",
                "executedQty": "1",
                "positionSide": "BOTH",
                "updateTime": 999,
            }
        ],
        income_history=[],
    )
    events = FakeTradeEventsRepo()
    provider = FakeAccountProvider(client)
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )

    current = await service.sync_trade(
        _trade(position_mode="one_way", position_side="BOTH"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.pnl_status == "pending"
    assert current.exchange_sync_status == "pnl_pending"
    assert current.realized_pnl_usd == 0.0
    assert current.local_realized_pnl_usd == pytest.approx(0.12)
    assert current.income_sync_completed is False
    assert current.last_income_sync_at == 1000
    assert current.income_sync_next_retry_at == 6000
    assert current.cleanup_completed is True
    assert current.exchange_position_confirmed_flat is True
    assert len(client.income_history_calls) == 1
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]
    assert [event for event in events.events if event["event_type"] == "CLOSED_PNL_PENDING"]

    build_calls_after_first_sync = provider.build_calls
    income_calls_after_first_sync = len(client.income_history_calls)
    repeated = await service.sync_trade(current, now_ms=2000, reason="poll", force=True)

    assert repeated is current
    assert provider.build_calls == build_calls_after_first_sync
    assert len(client.income_history_calls) == income_calls_after_first_sync


@pytest.mark.anyio
async def test_sync_trade_cleans_leftover_algo_orders_after_stop_close() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0"}],
        all_algo_orders=[
            {
                "algoId": "21",
                "clientAlgoId": "mb-s0",
                "algoStatus": "FINISHED",
                "orderType": "STOP_MARKET",
                "quantity": "1.0",
                "actualPrice": "9.8",
                "triggerPrice": "9.8",
            }
        ],
        open_algo_orders=[
            {"algoId": "11", "clientAlgoId": "mb-t1", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": "12", "clientAlgoId": "mb-t2", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    provider = FakeAccountProvider(client)
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=provider,
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        _trade(position_mode="hedge", position_side="LONG"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "STOP_LOSS_HIT"
    assert current.cleanup_completed is True
    assert current.income_sync_completed is True
    assert len(client.income_history_calls) == 1
    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]
    cleanup_events = [event for event in events.events if event["event_type"] == "LIVE_EXIT_ORDER_CLEANUP"]
    assert cleanup_events
    assert cleanup_events[0]["payload"]["cleanup_status"] == "completed"
    assert cleanup_events[0]["payload"]["open_order_count_before_cleanup"] == 2
    assert cleanup_events[0]["payload"]["exchange_position_confirmed_flat"] is True

    build_calls_after_close = provider.build_calls
    repeated = await service.sync_trade(current, now_ms=2000, reason="poll", force=True)

    assert repeated is current
    assert provider.build_calls == build_calls_after_close
    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]
    assert len(client.income_history_calls) == 1


@pytest.mark.anyio
async def test_sync_trade_treats_tp3_as_partial_when_tp4_exists() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        exchange_tp_orders=[
            {"algoId": "11", "clientAlgoId": "mb-t1", "stage": 1, "status": "NEW", "origQty": 0.25},
            {"algoId": "12", "clientAlgoId": "mb-t2", "stage": 2, "status": "NEW", "origQty": 0.25},
            {"algoId": "13", "clientAlgoId": "mb-t3", "stage": 3, "status": "NEW", "origQty": 0.25},
            {"algoId": "14", "clientAlgoId": "mb-t4", "stage": 4, "status": "NEW", "origQty": 0.25},
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.25"}],
        all_algo_orders=[
            {
                "algoId": "13",
                "clientAlgoId": "mb-t3",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "0.25",
                "executedQty": "0.25",
                "actualPrice": "10.15",
                "triggerPrice": "10.15",
            },
            {"algoId": "14", "clientAlgoId": "mb-t4", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
        ],
        open_algo_orders=[
            {"algoId": "14", "clientAlgoId": "mb-t4", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.tp_hit_count == 3
    assert current.exit_reason is None
    assert live_broker.replace_stop_order_calls
    event_types = [event["event_type"] for event in events.events]
    assert "TP_HIT" in event_types
    assert "CLOSED" not in event_types


@pytest.mark.anyio
async def test_sync_trade_repairs_tp3_local_zero_when_exchange_position_remains() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_hit_count=2,
        tp_count=4,
        tp_close_fractions=[0.25, 0.25, 0.25, 0.25],
        qty=0.24,
        qty_remaining=0.06,
        remaining_pct=0.25,
        exchange_position_amt=0.06,
        exchange_tp_orders=[
            {"algoId": "11", "clientAlgoId": "mb-t1", "stage": 1, "status": "FILLED", "origQty": 0.06},
            {"algoId": "12", "clientAlgoId": "mb-t2", "stage": 2, "status": "FILLED", "origQty": 0.06},
            {
                "algoId": "13",
                "clientAlgoId": "mb-t3",
                "stage": 3,
                "tp_count": 4,
                "status": "NEW",
                "origQty": 0.06,
                "close_fraction": 0.25,
            },
            {
                "algoId": "14",
                "clientAlgoId": "mb-t4",
                "stage": 4,
                "tp_count": 4,
                "status": "NEW",
                "origQty": 0.024,
                "close_fraction": 0.25,
            },
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.024"}],
        all_algo_orders=[
            {
                "algoId": "13",
                "clientAlgoId": "mb-t3",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "0.06",
                "executedQty": "0.06",
                "actualPrice": "10.15",
                "triggerPrice": "10.15",
            },
            {"algoId": "14", "clientAlgoId": "mb-t4", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
        ],
        open_algo_orders=[
            {"algoId": "14", "clientAlgoId": "mb-t4", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.tp_hit_count == 3
    assert current.qty_remaining == pytest.approx(0.024)
    assert current.remaining_pct == pytest.approx(0.1)
    assert live_broker.replace_stop_order_calls
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]
    tp_events = [event for event in events.events if event["event_type"] == "TP_HIT"]
    assert tp_events
    assert tp_events[0]["payload"]["trade_closed"] is False
    assert tp_events[0]["payload"]["exchange_position_amt_after_close"] == pytest.approx(0.024)
    assert tp_events[0]["payload"]["exchange_remaining_pct"] == pytest.approx(0.1)
    mismatch_events = [
        event
        for event in events.events
        if event["event_type"] == "LIVE_CLOSE_MISMATCH_POSITION_STILL_OPEN"
    ]
    assert mismatch_events
    assert mismatch_events[0]["payload"]["tp_index"] == 3
    assert mismatch_events[0]["payload"]["tp_count"] == 4
    assert mismatch_events[0]["payload"]["local_qty_remaining_after_without_exchange"] == pytest.approx(0.0)
    assert mismatch_events[0]["payload"]["exchange_position_amt_after_close"] == pytest.approx(0.024)
    assert mismatch_events[0]["payload"]["decision"] == "open"


@pytest.mark.anyio
async def test_sync_trade_handles_first_tp_as_partial_for_two_level_plan() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_count=2,
        tp_close_fractions=[0.5, 0.5],
        exchange_tp_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "stage": 1,
                "tp_count": 2,
                "status": "NEW",
                "origQty": 0.5,
                "close_fraction": 0.5,
            },
            {
                "algoId": "12",
                "clientAlgoId": "mb-t2",
                "stage": 2,
                "tp_count": 2,
                "status": "NEW",
                "origQty": 0.5,
                "close_fraction": 0.5,
            },
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.5"}],
        all_algo_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "0.5",
                "executedQty": "0.5",
                "actualPrice": "10.05",
                "triggerPrice": "10.05",
            },
            {"algoId": "12", "clientAlgoId": "mb-t2", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
        ],
        open_algo_orders=[
            {"algoId": "12", "clientAlgoId": "mb-t2", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.tp_hit_count == 1
    assert current.qty_remaining == pytest.approx(0.5)
    assert live_broker.replace_stop_order_calls
    tp_events = [event for event in events.events if event["event_type"] == "TP_HIT"]
    assert tp_events
    assert tp_events[0]["payload"]["tp_count"] == 2
    assert tp_events[0]["payload"]["tp_close_pct"] == pytest.approx(50.0)
    assert tp_events[0]["payload"]["trade_closed"] is False
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]


@pytest.mark.anyio
async def test_sync_trade_closes_third_tp_as_remaining_qty_for_three_level_plan() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_hit_count=2,
        tp_count=3,
        tp_close_fractions=[1 / 3, 1 / 3, 1 / 3],
        qty_remaining=0.34,
        remaining_pct=0.34,
        exchange_position_amt=0.34,
        exchange_tp_orders=[
            {"algoId": "11", "clientAlgoId": "mb-t1", "stage": 1, "status": "FILLED", "origQty": 0.33},
            {"algoId": "12", "clientAlgoId": "mb-t2", "stage": 2, "status": "FILLED", "origQty": 0.33},
            {
                "algoId": "13",
                "clientAlgoId": "mb-t3",
                "stage": 3,
                "tp_count": 3,
                "status": "NEW",
                "origQty": 0.34,
                "close_fraction": 1 / 3,
            },
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0"}],
        all_algo_orders=[
            {
                "algoId": "13",
                "clientAlgoId": "mb-t3",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "0.34",
                "executedQty": "0.34",
                "actualPrice": "10.15",
                "triggerPrice": "10.15",
            }
        ],
        open_algo_orders=[
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "TP3_HIT"
    closed_events = [event for event in events.events if event["event_type"] == "CLOSED"]
    assert closed_events
    assert closed_events[0]["payload"]["tp_index"] == 3
    assert closed_events[0]["payload"]["tp_count"] == 3
    assert closed_events[0]["payload"]["tp_close_pct"] == pytest.approx((1 / 3) * 100.0)
    assert closed_events[0]["payload"]["trade_closed"] is True
    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]


@pytest.mark.anyio
async def test_sync_trade_closes_full_trade_when_single_tp_fills() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_count=1,
        tp_close_fractions=[1.0],
        exchange_tp_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "stage": 1,
                "tp_count": 1,
                "status": "NEW",
                "origQty": 1.0,
                "close_fraction": 1.0,
            },
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0"}],
        all_algo_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "1.0",
                "executedQty": "1.0",
                "actualPrice": "10.05",
                "triggerPrice": "10.05",
            }
        ],
        open_algo_orders=[
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "TP1_HIT"
    closed_events = [event for event in events.events if event["event_type"] == "CLOSED"]
    assert closed_events
    assert closed_events[0]["payload"]["tp_index"] == 1
    assert closed_events[0]["payload"]["tp_count"] == 1
    assert closed_events[0]["payload"]["tp_close_pct"] == pytest.approx(100.0)
    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]


@pytest.mark.anyio
async def test_sync_trade_keeps_open_when_final_tp_fills_but_exchange_position_remains() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_count=1,
        tp_close_fractions=[1.0],
        exchange_tp_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "stage": 1,
                "tp_count": 1,
                "status": "NEW",
                "origQty": 1.0,
                "close_fraction": 1.0,
            },
        ],
    )
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.1"}],
        all_algo_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "1.0",
                "executedQty": "0.9",
                "actualPrice": "10.05",
                "triggerPrice": "10.05",
            }
        ],
        open_algo_orders=[
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.tp_hit_count == 1
    assert current.qty_remaining == pytest.approx(0.1)
    assert current.exchange_position_amt == pytest.approx(0.1)
    assert live_broker.replace_stop_order_calls
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]
    tp_events = [event for event in events.events if event["event_type"] == "TP_HIT"]
    assert tp_events
    assert tp_events[0]["payload"]["trade_closed"] is False
    partial_events = [event for event in events.events if event["event_type"] == "LIVE_PARTIAL_CLOSE_DETECTED"]
    assert partial_events
    assert partial_events[0]["payload"]["source"] == "TP1_HIT"
    assert partial_events[0]["payload"]["exchange_position_amt_after_close"] == pytest.approx(0.1)


@pytest.mark.anyio
async def test_sync_trade_keeps_open_when_stop_fills_but_exchange_position_remains() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.2"}],
        all_algo_orders=[
            {
                "algoId": "21",
                "clientAlgoId": "mb-s0",
                "algoStatus": "FINISHED",
                "orderType": "STOP_MARKET",
                "quantity": "1.0",
                "executedQty": "0.8",
                "actualPrice": "9.8",
                "triggerPrice": "9.8",
            }
        ],
        open_algo_orders=[],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        _trade(position_mode="hedge", position_side="LONG"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.exit_reason is None
    assert current.qty_remaining == pytest.approx(0.2)
    assert current.exchange_position_amt == pytest.approx(0.2)
    assert live_broker.replace_stop_order_calls
    assert not [event for event in events.events if event["event_type"] == "STOP"]
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]
    partial_events = [event for event in events.events if event["event_type"] == "LIVE_PARTIAL_CLOSE_DETECTED"]
    assert partial_events
    assert partial_events[0]["payload"]["source"] == "STOP_LOSS_HIT"
    assert partial_events[0]["payload"]["exchange_position_amt_after_close"] == pytest.approx(0.2)


@pytest.mark.anyio
async def test_sync_trade_skips_state_update_when_position_refresh_fails_after_tp_fill() -> None:
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        tp_count=1,
        tp_close_fractions=[1.0],
        exchange_tp_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "stage": 1,
                "tp_count": 1,
                "status": "NEW",
                "origQty": 1.0,
                "close_fraction": 1.0,
            },
        ],
    )
    client = FailingPositionRefreshClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.1"}],
        refresh_error=RuntimeError("timestamp_outside_recvwindow"),
        all_algo_orders=[
            {
                "algoId": "11",
                "clientAlgoId": "mb-t1",
                "algoStatus": "FINISHED",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "1.0",
                "executedQty": "0.9",
                "actualPrice": "10.05",
                "triggerPrice": "10.05",
            }
        ],
        open_algo_orders=[
            {"algoId": "21", "clientAlgoId": "mb-s0", "algoStatus": "NEW", "orderType": "STOP_MARKET"},
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(trade, now_ms=1000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.tp_hit_count == 0
    assert current.qty_remaining == pytest.approx(1.0)
    assert current.exchange_sync_status == "error"
    assert "critical_exchange_state_unavailable:position_refresh_failed:" in str(current.exchange_sync_error)
    assert not [event for event in events.events if event["event_type"] == "TP_HIT"]
    assert not [event for event in events.events if event["event_type"] == "CLOSED"]
    sync_errors = [event for event in events.events if event["event_type"] == "LIVE_SYNC_ERROR"]
    assert sync_errors
    assert sync_errors[0]["payload"]["decision"] == "skip_update"
    assert not live_broker.replace_stop_order_calls


@pytest.mark.anyio
async def test_sync_trade_reconciles_qty_from_exchange_without_mismatch_and_refreshes_stop() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.1"}],
        open_algo_orders=[
            {
                "algoId": "21",
                "clientAlgoId": "mb-s0",
                "algoStatus": "NEW",
                "orderType": "STOP_MARKET",
                "quantity": "0.6",
                "triggerPrice": "9.8",
            }
        ],
        all_algo_orders=[
            {
                "algoId": "21",
                "clientAlgoId": "mb-s0",
                "algoStatus": "NEW",
                "orderType": "STOP_MARKET",
                "quantity": "0.6",
                "triggerPrice": "9.8",
            }
        ],
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(
            _trade(position_mode="hedge", position_side="LONG"),
            qty=1.0,
            qty_remaining=0.6,
            exchange_position_amt=0.6,
        ),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.qty_remaining == 0.1
    assert current.exchange_position_amt == 0.1
    assert current.exchange_stop_order["origQty"] == 0.1
    assert live_broker.replace_stop_order_calls
    assert live_broker.replace_stop_order_calls[0]["new_stop_price"] == current.sl_price
    assert not [event for event in events.events if event["event_type"] == "LIVE_SYNC_MISMATCH"]
    reconciled_events = [event for event in events.events if event["event_type"] == "LIVE_SYNC_RECONCILED"]
    assert reconciled_events
    assert reconciled_events[0]["payload"]["reason"] == "qty_reconciled_from_exchange"


@pytest.mark.anyio
async def test_v1_trade_does_not_activate_soft_stop() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.20"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(_trade(position_mode="hedge", position_side="LONG"), strategy_version="v1"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.soft_stop_activated_at is None
    assert not [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]


@pytest.mark.anyio
async def test_v2_medium_soft_stop_waits_for_tp1_hit_even_after_activation_profit() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.081"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(_trade(position_mode="hedge", position_side="LONG"), strategy_version="v2", signal_level="medium"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.soft_stop_enabled is True
    assert current.soft_stop_activated_at is None
    assert not [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]


@pytest.mark.anyio
async def test_v2_medium_soft_stop_activates_after_tp1_hit() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.081"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(
            _trade(position_mode="hedge", position_side="LONG"),
            strategy_version="v2",
            signal_level="medium",
            tp_hit_count=1,
        ),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.status == TradeStatus.OPEN
    assert current.soft_stop_enabled is True
    assert current.soft_stop_activated_at == 1000
    assert current.soft_stop_current_pct == pytest.approx(0.1)
    assert current.soft_stop_trigger_price == pytest.approx(10.01)
    activation_events = [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]
    assert activation_events
    assert activation_events[0]["payload"]["current_profit_pct"] >= 0.8
    assert activation_events[0]["payload"]["signal_level"] == "medium"
    assert activation_events[0]["payload"]["soft_stop_activation_pct"] == pytest.approx(0.8)
    assert activation_events[0]["payload"]["soft_stop_start_pct"] == pytest.approx(0.1)
    assert activation_events[0]["payload"]["soft_stop_activation_trigger"] == "tp1_hit"
    assert activation_events[0]["payload"]["activation_reason"] == "tp1_hit"
    assert activation_events[0]["payload"]["tp_index"] == 1


@pytest.mark.anyio
async def test_v2_weak_soft_stop_activates_after_tp1_hit() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.061"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(
            _trade(position_mode="hedge", position_side="LONG"),
            strategy_version="v2",
            signal_level="weak",
            tp_hit_count=1,
        ),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.soft_stop_activated_at == 1000
    activation_events = [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]
    assert activation_events[0]["payload"]["soft_stop_activation_pct"] == pytest.approx(0.6)
    assert activation_events[0]["payload"]["soft_stop_activation_trigger"] == "tp1_hit"


@pytest.mark.anyio
async def test_v2_strong_soft_stop_waits_for_tp1_hit_even_after_one_percent_profit() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.101"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(_trade(position_mode="hedge", position_side="LONG"), strategy_version="v2", signal_level="strong"),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.soft_stop_activated_at is None
    assert not [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]


@pytest.mark.anyio
async def test_v2_strong_soft_stop_activates_after_tp1_hit() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.101"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )

    current = await service.sync_trade(
        replace(
            _trade(position_mode="hedge", position_side="LONG"),
            strategy_version="v2",
            signal_level="strong",
            tp_hit_count=1,
        ),
        now_ms=1000,
        reason="poll",
        force=True,
    )

    assert current.soft_stop_activated_at == 1000
    activation_events = [event for event in events.events if event["event_type"] == "SOFT_STOP_ACTIVATED"]
    assert activation_events[0]["payload"]["soft_stop_activation_pct"] == pytest.approx(1.0)
    assert activation_events[0]["payload"]["soft_stop_activation_trigger"] == "tp1_hit"


@pytest.mark.anyio
async def test_v2_soft_stop_raises_by_point_zero_five_percent_every_fifteen_minutes() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.20"}],
    )
    events = FakeTradeEventsRepo()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        strategy_version="v2",
        signal_level="medium",
        soft_stop_enabled=True,
        soft_stop_activated_at=1000,
        soft_stop_current_pct=0.1,
        soft_stop_last_raise_at=1000,
        soft_stop_next_raise_at=901_000,
    )

    current = await service.sync_trade(trade, now_ms=2_701_000, reason="poll", force=True)

    assert current.status == TradeStatus.OPEN
    assert current.soft_stop_current_pct == pytest.approx(0.25)
    assert current.soft_stop_trigger_price == pytest.approx(10.025)
    raised_events = [event for event in events.events if event["event_type"] == "SOFT_STOP_RAISED"]
    assert raised_events
    assert raised_events[0]["payload"]["soft_stop_current_pct"] == pytest.approx(0.25)
    assert raised_events[0]["payload"]["soft_stop_increment_pct"] == pytest.approx(0.05)
    assert raised_events[0]["payload"]["soft_stop_increment_interval_seconds"] == 900


@pytest.mark.anyio
async def test_v2_soft_stop_raise_schedule_after_fifteen_and_thirty_minutes() -> None:
    client = FakeClient(
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.20"}],
    )
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=FakeTradeEventsRepo(),
        account_provider=FakeAccountProvider(client),
        live_broker=FakeLiveBroker(),
        live_poll_interval_ms=0,
    )
    base = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        strategy_version="v2",
        signal_level="medium",
        soft_stop_enabled=True,
        soft_stop_activated_at=1000,
        soft_stop_current_pct=0.1,
        soft_stop_last_raise_at=1000,
        soft_stop_next_raise_at=901_000,
    )

    after_15m = await service.sync_trade(base, now_ms=901_000, reason="poll", force=True)
    after_30m = await service.sync_trade(base, now_ms=1_801_000, reason="poll", force=True)

    assert after_15m.soft_stop_current_pct == pytest.approx(0.15)
    assert after_15m.soft_stop_trigger_price == pytest.approx(10.015)
    assert after_30m.soft_stop_current_pct == pytest.approx(0.20)
    assert after_30m.soft_stop_trigger_price == pytest.approx(10.02)


@pytest.mark.anyio
async def test_v2_soft_stop_closes_trade_only_after_exchange_confirms_flat() -> None:
    client = SoftStopCloseClient(
        open_position={"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "1.0", "markPrice": "10.00"},
        flat_position={"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0", "markPrice": "10.00"},
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        strategy_version="v2",
        soft_stop_enabled=True,
        soft_stop_activated_at=1000,
        soft_stop_current_pct=0.1,
        soft_stop_last_raise_at=1000,
        soft_stop_next_raise_at=3_601_000,
        soft_stop_trigger_price=10.01,
    )

    current = await service.sync_trade(trade, now_ms=2000, reason="poll", force=True)

    assert live_broker.close_position_market_calls
    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "SOFT_TRAILING_STOP"
    assert current.pnl_source == "exchange"
    closed_events = [event for event in events.events if event["event_type"] == "CLOSED"]
    assert closed_events
    assert closed_events[0]["payload"]["reason"] == "SOFT_TRAILING_STOP"
    assert closed_events[0]["payload"]["realized_pnl_usd"] == pytest.approx(0.12)


@pytest.mark.anyio
async def test_stop_replacement_immediate_trigger_closes_instead_of_replacing_stop() -> None:
    client = SoftStopCloseClient(
        open_position={"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0.5", "markPrice": "10.00"},
        flat_position={"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0", "markPrice": "10.00"},
    )
    events = FakeTradeEventsRepo()
    live_broker = FakeLiveBroker()
    service = LiveTradeSyncService(
        trades_repo=FakeTradesRepo(),
        trade_events_repo=events,
        account_provider=FakeAccountProvider(client),
        live_broker=live_broker,
        live_poll_interval_ms=0,
        pnl_retry_delays_seconds=(),
    )
    trade = replace(
        _trade(position_mode="hedge", position_side="LONG"),
        strategy_version="v2",
        signal_level="medium",
        qty_remaining=1.0,
        sl_price=10.01,
    )

    current = await service.sync_trade(trade, now_ms=2000, reason="poll", force=True)

    assert live_broker.replace_stop_order_calls == []
    assert live_broker.close_position_market_calls
    assert live_broker.close_position_market_calls[0]["reason"] == "SOFT_STOP_IMMEDIATE"
    assert current.status == TradeStatus.CLOSED
    assert current.exit_reason == "SOFT_STOP_IMMEDIATE"
    skipped_events = [event for event in events.events if event["event_type"] == "STOP_SKIPPED_IMMEDIATE_TRIGGER"]
    assert skipped_events
    assert skipped_events[0]["payload"]["stop_price"] == pytest.approx(10.01)
    assert skipped_events[0]["payload"]["current_price"] == pytest.approx(10.0)
    closed_events = [event for event in events.events if event["event_type"] == "CLOSED"]
    assert closed_events
    assert closed_events[0]["payload"]["reason"] == "SOFT_STOP_IMMEDIATE"
