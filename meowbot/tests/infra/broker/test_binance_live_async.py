from __future__ import annotations

import logging
from dataclasses import replace

import httpx
import pytest

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.infra.broker.binance_live_async import BinanceLiveBrokerAsync, MIN_TP_NOTIONAL
from meowbot.infra.exchange.binance_futures_account_async import BinanceFuturesAccountAsync
from meowbot.infra.exchange.binance_request_gate import BinanceGateConfig, BinanceRequestGate


class FakeExchangeInfoClient:
    def __init__(
        self,
        *,
        exchange_symbol: str = "INJUSDT",
        tick_size: str = "0.001",
        step_size: str = "0.1",
        min_qty: str = "0.1",
        min_notional: str = "5",
    ) -> None:
        self.exchange_symbol = exchange_symbol
        self.tick_size = tick_size
        self.step_size = step_size
        self.min_qty = min_qty
        self.min_notional = min_notional

    async def get_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": self.exchange_symbol,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": self.tick_size},
                        {"filterType": "MARKET_LOT_SIZE", "stepSize": self.step_size, "minQty": self.min_qty},
                        {"filterType": "MIN_NOTIONAL", "notional": self.min_notional},
                    ],
                }
            ]
        }


class FakeLiveClient(FakeExchangeInfoClient):
    def __init__(
        self,
        *,
        hedge_mode: bool,
        positions: list[dict] | None = None,
        open_orders: list[dict] | None = None,
        open_algo_orders: list[dict] | None = None,
        exchange_symbol: str = "INJUSDT",
        tick_size: str = "0.001",
        step_size: str = "0.1",
        min_qty: str = "0.1",
        min_notional: str = "5",
        market_avg_price: str = "10",
        market_executed_qty: str | None = None,
        algo_order_failures: list[Exception | None] | None = None,
        cancel_algo_order_failures: list[Exception | None] | None = None,
        account_info: dict | None = None,
    ) -> None:
        super().__init__(
            exchange_symbol=exchange_symbol,
            tick_size=tick_size,
            step_size=step_size,
            min_qty=min_qty,
            min_notional=min_notional,
        )
        self.hedge_mode = hedge_mode
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.open_algo_orders = open_algo_orders or []
        self.market_avg_price = market_avg_price
        self.market_executed_qty = market_executed_qty
        self.algo_order_failures = list(algo_order_failures or [])
        self.cancel_algo_order_failures = list(cancel_algo_order_failures or [])
        self.account_info = account_info or {
            "availableBalance": "100",
            "totalMarginBalance": "100",
            "totalInitialMargin": "5",
        }
        self.create_order_calls: list[dict] = []
        self.create_algo_order_calls: list[dict] = []
        self.cancel_algo_order_calls: list[dict] = []
        self.cancel_all_orders_calls: list[dict] = []
        self.cancel_all_algo_orders_calls: list[dict] = []
        self.closed = False

    async def get_position_mode(self):
        return {"dualSidePosition": self.hedge_mode}

    async def ensure_cross_margin(self, *, symbol: str):
        return {"status": "ok", "symbol": symbol}

    async def change_leverage(self, *, symbol: str, leverage: int):
        return {"status": "ok", "symbol": symbol, "leverage": leverage}

    async def get_open_orders(self, *, symbol: str, raise_on_error: bool = False):
        return self.open_orders

    async def get_open_algo_orders(self, *, symbol: str, raise_on_error: bool = False):
        return self.open_algo_orders

    async def get_position_risk(self, *, symbol: str, raise_on_error: bool = False):
        return self.positions

    async def get_account_info(self):
        return self.account_info

    async def create_order(self, **params):
        self.create_order_calls.append(dict(params))
        order_id = len(self.create_order_calls)
        payload = {
            "orderId": str(order_id),
            "clientOrderId": params.get("newClientOrderId"),
            "status": "NEW",
            "type": params.get("type"),
            "positionSide": params.get("positionSide"),
            "origQty": params.get("quantity"),
            "executedQty": "0",
            "avgPrice": "0",
        }
        if params.get("type") == "MARKET":
            payload["status"] = "FILLED"
            payload["executedQty"] = self.market_executed_qty or params.get("quantity")
            payload["avgPrice"] = self.market_avg_price
        return payload

    async def create_algo_order(self, **params):
        self.create_algo_order_calls.append(dict(params))
        if self.algo_order_failures:
            failure = self.algo_order_failures.pop(0)
            if failure is not None:
                raise failure
        algo_id = len(self.create_algo_order_calls)
        return {
            "algoId": str(algo_id),
            "clientAlgoId": params.get("clientAlgoId"),
            "algoStatus": "NEW",
            "algoType": params.get("algoType"),
            "orderType": params.get("type"),
            "positionSide": params.get("positionSide"),
            "quantity": params.get("quantity"),
            "triggerPrice": params.get("triggerPrice"),
            "reduceOnly": params.get("reduceOnly") == "true",
        }

    async def cancel_algo_order(self, **params):
        self.cancel_algo_order_calls.append(dict(params))
        if self.cancel_algo_order_failures:
            failure = self.cancel_algo_order_failures.pop(0)
            if failure is not None:
                raise failure
        return {"code": "200", "msg": "success", **params}

    async def cancel_all_orders(self, *, symbol: str):
        self.cancel_all_orders_calls.append({"symbol": symbol})
        return {"status": "ok"}

    async def cancel_all_algo_orders(self, *, symbol: str):
        self.cancel_all_algo_orders_calls.append({"symbol": symbol})
        return {"status": "ok"}

    async def close(self):
        self.closed = True


class FakeAccountProvider:
    def __init__(self, client) -> None:
        self.client = client

    async def build_client(self, *, runtime_user_id: str, require_active: bool):
        return self.client, {"permissions_json": {"enableFutures": True}}, None


def _algo_order_http_error(
    *,
    code: int = -1111,
    message: str = "Precision is over the maximum defined for this asset.",
    path: str = "/fapi/v1/algoOrder",
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", f"https://fapi.binance.com{path}?symbol=INJUSDT")
    response = httpx.Response(
        400,
        json={"code": code, "msg": message},
        request=request,
    )
    return httpx.HTTPStatusError(message, request=request, response=response)


class FakeBinanceFuturesAccount(BinanceFuturesAccountAsync):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self._futures_client = object()
        self._futures_gate = object()

    async def _signed_request_json(self, **kwargs):
        raise self.exc


def _live_trade(*, side: Side = Side.LONG) -> Trade:
    return Trade(
        trade_id="tg:1:INJUSDT:2h:1:rule",
        user_id="tg:1",
        symbol="INJUSDT",
        side=side,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=10.0,
        qty=1.0,
        leverage=5,
        stake_usd=2.0,
        tf_entry="2h",
        model_id="rule",
        entry_bar_close_time=1,
        sl_price=9.8,
        mode="live",
        execution_engine="binance_futures_live",
        exchange_name="binance",
    )


@pytest.mark.anyio
async def test_symbol_rules_include_min_notional() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    rules = await broker._get_symbol_rules(FakeExchangeInfoClient(), "INJUSDT")

    assert rules["tick_size"] == 0.001
    assert rules["qty_step"] == 0.1
    assert rules["min_qty"] == 0.1
    assert rules["min_notional"] == 5.0


@pytest.mark.anyio
async def test_open_position_uses_long_position_side_in_hedge_mode() -> None:
    client = FakeLiveClient(hedge_mode=True)
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    trade = await broker.open_position(_live_trade(side=Side.LONG))

    assert trade.exchange_position_mode == "hedge"
    assert trade.exchange_position_side == "LONG"
    assert client.closed is True
    assert client.create_order_calls
    all_calls = [*client.create_order_calls, *client.create_algo_order_calls]
    assert {call["positionSide"] for call in all_calls} == {"LONG"}
    assert all("reduceOnly" not in call for call in all_calls)
    assert client.create_order_calls[0]["type"] == "MARKET"
    assert {call["type"] for call in client.create_algo_order_calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert all(call["algoType"] == "CONDITIONAL" for call in client.create_algo_order_calls)


@pytest.mark.anyio
async def test_open_position_uses_short_position_side_in_hedge_mode() -> None:
    client = FakeLiveClient(hedge_mode=True)
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    trade = await broker.open_position(_live_trade(side=Side.SHORT))

    assert trade.exchange_position_mode == "hedge"
    assert trade.exchange_position_side == "SHORT"
    all_calls = [*client.create_order_calls, *client.create_algo_order_calls]
    assert {call["positionSide"] for call in all_calls} == {"SHORT"}
    assert all("reduceOnly" not in call for call in all_calls)


@pytest.mark.anyio
async def test_open_position_uses_both_position_side_in_one_way_mode() -> None:
    client = FakeLiveClient(hedge_mode=False)
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    trade = await broker.open_position(_live_trade(side=Side.LONG))

    assert trade.exchange_position_mode == "one_way"
    assert trade.exchange_position_side == "BOTH"
    all_calls = [*client.create_order_calls, *client.create_algo_order_calls]
    assert {call["positionSide"] for call in all_calls} == {"BOTH"}
    assert all(
        call.get("reduceOnly") == "true"
        for call in all_calls
        if call["type"] != "MARKET" or call.get("newClientOrderId", "").startswith("mb-mx-")
    )


@pytest.mark.anyio
async def test_open_position_cleans_stale_algo_orders_before_entry_when_flat() -> None:
    client = FakeLiveClient(
        hedge_mode=True,
        positions=[{"symbol": "INJUSDT", "positionSide": "LONG", "positionAmt": "0"}],
        open_algo_orders=[
            {"algoId": "old-tp", "clientAlgoId": "old-tp", "algoStatus": "NEW", "orderType": "TAKE_PROFIT_MARKET"}
        ],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    await broker.open_position(_live_trade(side=Side.LONG))

    assert client.cancel_all_algo_orders_calls == [{"symbol": "INJUSDT"}]
    assert client.create_order_calls[0]["type"] == "MARKET"


def test_format_exchange_number_preserves_filter_multiple_from_float_artifact() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    assert broker._format_exchange_number(1.0499999999999998, 0.05) == "1.05"
    assert broker._format_exchange_number(0.0010000000000002, 0.001) == "0.001"


@pytest.mark.anyio
async def test_open_position_formats_algo_orders_with_exchange_precision() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        exchange_symbol="BTCUSDT",
        tick_size="0.10",
        step_size="0.001",
        min_qty="0.001",
        min_notional="5",
        market_avg_price="93567.87",
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        trade_id="tg:1:BTCUSDT:2h:1:rule",
        symbol="BTCUSDT",
        entry_price=93567.87,
        qty=0.0019,
        qty_requested=0.0019,
        stake_usd=180.0,
        sl_price=None,
    )

    await broker.open_position(trade)

    assert client.create_order_calls[0]["quantity"] == "0.001"
    take_profit_calls = [call for call in client.create_algo_order_calls if call["type"] == "TAKE_PROFIT_MARKET"]
    stop_calls = [call for call in client.create_algo_order_calls if call["type"] == "STOP_MARKET"]

    assert len(take_profit_calls) == 1
    assert len(stop_calls) == 1
    assert take_profit_calls[0]["quantity"] == "0.001"
    assert take_profit_calls[0]["triggerPrice"] == "94035.8"
    assert stop_calls[0]["quantity"] == "0.001"
    assert stop_calls[0]["triggerPrice"] == "91696.5"


def test_build_algo_order_request_rounds_qty_and_trigger_price_with_exchange_filters() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    request = broker._build_algo_order_request(
        base_params={"symbol": "BTCUSDT", "type": "STOP_MARKET"},
        raw_qty=0.001234567,
        qty_step=0.001,
        raw_price=93567.87654,
        tick_size=0.10,
        price_rounding_mode="ROUND_FLOOR",
    )

    assert request["rounded_qty"] == 0.001
    assert request["params"]["quantity"] == "0.001"
    assert request["rounded_price"] == 93567.8
    assert request["params"]["triggerPrice"] == "93567.8"


def test_build_tp_specs_caps_at_four_and_keeps_each_tp_above_min_notional() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    specs = broker._build_tp_specs(
        trade=_live_trade(side=Side.LONG),
        entry_price=10.0,
        qty=1.0,
        tick_size=0.001,
        qty_step=0.01,
    )

    assert len(specs) == 4
    assert [spec["qty"] for spec in specs] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert [spec["close_fraction"] for spec in specs] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert sum(spec["qty"] for spec in specs) == pytest.approx(1.0)
    assert all(spec["qty"] * 10.0 >= MIN_TP_NOTIONAL for spec in specs)


def test_build_tp_specs_uses_v2_one_percent_tp_step() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())
    trade = replace(_live_trade(side=Side.LONG), strategy_version="v2")

    specs = broker._build_tp_specs(
        trade=trade,
        entry_price=10.0,
        qty=1.0,
        tick_size=0.001,
        qty_step=0.01,
    )

    assert [spec["level"] for spec in specs] == pytest.approx([0.01, 0.02, 0.03, 0.04])
    assert [spec["price"] for spec in specs] == pytest.approx([10.1, 10.2, 10.3, 10.4])
    assert [spec["close_fraction"] for spec in specs] == pytest.approx([0.25, 0.25, 0.25, 0.25])


def test_build_tp_specs_keeps_four_equal_levels_when_each_tp_stays_above_min_notional() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    specs = broker._build_tp_specs(
        trade=replace(_live_trade(side=Side.LONG), leverage=1),
        entry_price=10.0,
        qty=0.4,
        tick_size=0.001,
        qty_step=0.001,
    )

    assert len(specs) == 4
    assert [spec["qty"] for spec in specs] == pytest.approx([0.1, 0.1, 0.1, 0.1])
    assert [spec["close_fraction"] for spec in specs] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert all(spec["qty"] * 10.0 >= MIN_TP_NOTIONAL for spec in specs)


def test_build_tp_specs_uses_four_equal_levels_when_rounding_allows_it() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    specs = broker._build_tp_specs(
        trade=replace(_live_trade(side=Side.LONG), leverage=1),
        entry_price=10.0,
        qty=0.12,
        tick_size=0.001,
        qty_step=0.001,
    )

    assert len(specs) == 4
    assert [spec["qty"] for spec in specs] == pytest.approx([0.03, 0.03, 0.03, 0.03])
    assert [spec["close_fraction"] for spec in specs] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert sum(spec["qty"] for spec in specs) == pytest.approx(0.12)
    assert all(spec["qty"] * 10.0 >= MIN_TP_NOTIONAL for spec in specs)


def test_build_tp_specs_reduces_count_after_step_rounding() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    specs = broker._build_tp_specs(
        trade=_live_trade(side=Side.LONG),
        entry_price=0.61,
        qty=10.0,
        tick_size=0.0001,
        qty_step=5.0,
    )

    assert len(specs) == 2
    assert all(spec["qty"] * 0.61 >= MIN_TP_NOTIONAL for spec in specs)


def test_build_tp_specs_keeps_one_tp_for_tiny_position() -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())

    specs = broker._build_tp_specs(
        trade=_live_trade(side=Side.LONG),
        entry_price=1.0,
        qty=0.1,
        tick_size=0.0001,
        qty_step=0.1,
    )

    assert len(specs) == 1
    assert specs[0]["qty"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("entry_margin_usdt", "expected_tp_count", "forced"),
    [
        (0.25, 1, True),
        (0.99, 1, True),
        (1.00, 4, False),
        (2.00, 4, False),
    ],
)
def test_build_tp_specs_forces_single_tp_when_entry_margin_is_below_one_usdt(
    entry_margin_usdt: float,
    expected_tp_count: int,
    forced: bool,
) -> None:
    broker = BinanceLiveBrokerAsync(account_provider=object())
    leverage = 5
    entry_price = 10.0
    qty = (entry_margin_usdt * leverage) / entry_price

    specs = broker._build_tp_specs(
        trade=replace(_live_trade(side=Side.LONG), leverage=leverage),
        entry_price=entry_price,
        qty=qty,
        tick_size=0.001,
        qty_step=0.001,
    )

    assert len(specs) == expected_tp_count
    assert specs[0]["tp_count"] == expected_tp_count
    assert specs[0]["entry_margin_usdt"] == pytest.approx(entry_margin_usdt)
    assert specs[0]["tp_count_forced_by_small_margin"] is forced
    if forced:
        assert specs[0]["original_tp_count"] == 4
        assert specs[0]["tp_count_force_reason"] == "small_entry_margin_lt_1_usdt"


@pytest.mark.anyio
async def test_open_position_uses_actual_exchange_position_qty_for_protection_orders() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        market_executed_qty="1.2",
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0.8"}],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        qty=1.3,
        qty_requested=1.3,
        stake_usd=2.6,
    )

    opened = await broker.open_position(trade)

    assert opened.qty == pytest.approx(0.8)
    assert opened.qty_filled == pytest.approx(0.8)
    assert opened.qty_remaining == pytest.approx(0.8)
    assert opened.exchange_position_amt == pytest.approx(0.8)
    assert opened.exchange_stop_order["origQty"] == pytest.approx(0.8)
    assert sum(order["target_qty"] for order in opened.exchange_tp_orders) == pytest.approx(0.8)
    assert opened.tp_count == len(opened.exchange_tp_orders)
    assert opened.tp_plan
    assert len(opened.tp_order_ids) == len(opened.exchange_tp_orders)


@pytest.mark.anyio
async def test_open_position_bumps_entry_qty_to_min_notional_before_market_order() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        exchange_symbol="ALGOUSDT",
        tick_size="0.0001",
        step_size="0.1",
        min_qty="0.1",
        min_notional="5",
        market_avg_price="0.1082",
        account_info={
            "availableBalance": "100",
            "totalMarginBalance": "100",
            "totalInitialMargin": "7",
        },
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        trade_id="tg:1:ALGOUSDT:15m:1:rule",
        symbol="ALGOUSDT",
        entry_price=0.1082,
        qty=46.2,
        qty_requested=46.2,
        leverage=5,
        stake_usd=0.999768,
        sl_price=0.1060,
    )

    opened = await broker.open_position(trade)

    assert client.create_order_calls[0]["quantity"] == "47.2"
    assert opened.qty == pytest.approx(47.2)
    assert opened.qty_remaining == pytest.approx(47.2)


@pytest.mark.anyio
async def test_open_position_bumps_market_entry_to_min_notional_buffer() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        exchange_symbol="ZILUSDT",
        tick_size="0.00001",
        step_size="1",
        min_qty="1",
        min_notional="5",
        market_avg_price="0.00414",
        account_info={
            "availableBalance": "100",
            "totalMarginBalance": "100",
            "totalInitialMargin": "7",
        },
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        trade_id="tg:1:ZILUSDT:15m:1:rule",
        symbol="ZILUSDT",
        entry_price=0.00414,
        qty=270,
        qty_requested=270,
        leverage=5,
        stake_usd=0.22356,
        sl_price=0.00405,
    )

    opened = await broker.open_position(trade)

    assert client.create_order_calls[0]["quantity"] == "1232"
    assert opened.qty == pytest.approx(1232)
    assert opened.qty_remaining == pytest.approx(1232)


@pytest.mark.anyio
async def test_open_position_blocks_bumped_entry_when_final_margin_limit_would_be_exceeded() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        exchange_symbol="ALGOUSDT",
        tick_size="0.0001",
        step_size="0.1",
        min_qty="0.1",
        min_notional="5",
        account_info={
            "availableBalance": "100",
            "totalMarginBalance": "8",
            "totalInitialMargin": "7.1",
        },
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        trade_id="tg:1:ALGOUSDT:15m:2:rule",
        symbol="ALGOUSDT",
        entry_price=0.1082,
        qty=46.2,
        qty_requested=46.2,
        leverage=5,
        stake_usd=0.999768,
        sl_price=0.1060,
    )

    with pytest.raises(RuntimeError, match="entry_bump_blocked_by_global_margin_limit"):
        await broker.open_position(trade)

    assert client.create_order_calls == []


@pytest.mark.anyio
async def test_open_position_retries_algo_order_once_after_precision_error() -> None:
    client = FakeLiveClient(
        hedge_mode=False,
        algo_order_failures=[_algo_order_http_error(), None],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    opened = await broker.open_position(_live_trade(side=Side.LONG))

    assert opened.protection_status == "protected"
    assert opened.exchange_stop_order is not None
    assert len(client.create_algo_order_calls) == 6


@pytest.mark.anyio
async def test_open_position_marks_trade_unprotected_when_stop_setup_fails() -> None:
    failure = _algo_order_http_error()
    client = FakeLiveClient(
        hedge_mode=False,
        algo_order_failures=[failure, failure],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    opened = await broker.open_position(_live_trade(side=Side.LONG))

    assert opened.protection_status == "unprotected"
    assert opened.protection_error is not None
    assert opened.exchange_stop_order is None
    assert opened.exchange_tp_orders == []
    assert opened.protection_details["exchange_error_code"] == -1111
    assert opened.protection_details["exchange_error_message"] == "Precision is over the maximum defined for this asset."
    assert len(client.create_algo_order_calls) == 2


@pytest.mark.anyio
async def test_open_position_marks_trade_partially_protected_when_tp_setup_fails_but_stop_succeeds() -> None:
    failure = _algo_order_http_error()
    client = FakeLiveClient(
        hedge_mode=False,
        algo_order_failures=[None, failure, failure],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))

    opened = await broker.open_position(_live_trade(side=Side.LONG))

    assert opened.protection_status == "partially_protected"
    assert opened.exchange_stop_order is not None
    assert len(opened.exchange_tp_orders) == 3
    assert opened.protection_error is not None
    assert opened.protection_details["tp_order_count_created"] == 3
    assert opened.protection_details["tp_order_count_requested"] == 4


@pytest.mark.anyio
async def test_replace_stop_order_dedupes_duplicate_cancel_and_create_for_stale_algo_id() -> None:
    client = FakeLiveClient(hedge_mode=False, step_size="0.01")
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    stale_trade = replace(
        _live_trade(side=Side.LONG),
        qty=1.0,
        qty_remaining=0.65,
        exchange_position_mode="one_way",
        exchange_position_side="BOTH",
        exchange_stop_order={
            "algoId": "4000001163278224",
            "clientAlgoId": "mb-s0-old",
            "clientOrderId": "mb-s0-old",
            "status": "NEW",
            "type": "STOP_MARKET",
            "origQty": 1.0,
            "quantity": 1.0,
            "triggerPrice": 9.2,
            "positionSide": "BOTH",
        },
        exchange_order_ids=["4000001163278224"],
    )

    first = await broker.replace_stop_order(
        stale_trade,
        new_stop_price=9.382,
        now_ms=1000,
    )
    second = await broker.replace_stop_order(
        stale_trade,
        new_stop_price=9.382,
        now_ms=1001,
    )

    assert len(client.cancel_algo_order_calls) == 1
    assert client.cancel_algo_order_calls[0]["algo_id"] == "4000001163278224"
    assert len(client.create_algo_order_calls) == 1
    assert first.exchange_stop_order["algoId"] == second.exchange_stop_order["algoId"]
    assert second.exchange_last_sync_reason == "stop_replace_duplicate_ignored"
    assert "4000001163278224" not in second.exchange_order_ids


@pytest.mark.anyio
async def test_repair_exit_orders_rebuilds_stop_and_remaining_tp_for_exchange_leftover() -> None:
    old_stop = {
        "algoId": "old-stop",
        "clientAlgoId": "mb-s2-old",
        "algoStatus": "NEW",
        "orderType": "STOP_MARKET",
        "quantity": "0.2",
        "triggerPrice": "10.02",
        "positionSide": "BOTH",
    }
    old_tp = {
        "algoId": "old-tp4",
        "clientAlgoId": "mb-t4-old",
        "algoStatus": "NEW",
        "orderType": "TAKE_PROFIT_MARKET",
        "quantity": "0.2",
        "triggerPrice": "10.2",
        "positionSide": "BOTH",
        "stage": 4,
    }
    client = FakeLiveClient(
        hedge_mode=False,
        step_size="0.001",
        positions=[{"symbol": "INJUSDT", "positionSide": "BOTH", "positionAmt": "0.024"}],
        open_algo_orders=[old_stop, old_tp],
    )
    broker = BinanceLiveBrokerAsync(account_provider=FakeAccountProvider(client))
    trade = replace(
        _live_trade(side=Side.LONG),
        qty=0.24,
        qty_remaining=0.024,
        remaining_pct=0.1,
        exchange_position_amt=0.024,
        tp_hit_count=3,
        tp_count=4,
        exchange_position_mode="one_way",
        exchange_position_side="BOTH",
        exchange_stop_order=old_stop,
        exchange_tp_orders=[old_tp],
        exchange_order_ids=["old-stop", "old-tp4"],
    )

    repaired = await broker.repair_exit_orders_for_remaining_position(
        trade,
        new_stop_price=10.02,
        now_ms=1000,
        reason="TP3_HIT",
    )

    assert repaired.qty_remaining == pytest.approx(0.024)
    assert repaired.remaining_pct == pytest.approx(0.1)
    assert repaired.protection_status == "protected"
    assert repaired.exchange_last_sync_reason == "exit_orders_repaired:TP3_HIT"
    assert [call["algo_id"] for call in client.cancel_algo_order_calls] == ["old-stop", "old-tp4"]
    assert [call["type"] for call in client.create_algo_order_calls] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert client.create_algo_order_calls[-1]["quantity"] == "0.024"
    assert repaired.exchange_tp_orders[0]["stage"] == 4
    assert repaired.exchange_tp_orders[0]["target_qty"] == pytest.approx(0.024)


@pytest.mark.anyio
async def test_cancel_algo_order_treats_unknown_order_as_idempotent_success() -> None:
    exc = _algo_order_http_error(
        code=-2011,
        message="Unknown order sent.",
        path="/fapi/v1/algoOrder",
    )
    client = FakeBinanceFuturesAccount(exc)

    result = await client.cancel_algo_order(algo_id="4000001163278224")

    assert result["status"] == "already_missing"
    assert result["code"] == -2011
    assert result["algoId"] == "4000001163278224"


@pytest.mark.anyio
async def test_request_gate_downgrades_expected_algo_cancel_not_found_log(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": -2011, "msg": "Unknown order sent."},
            request=request,
        )

    gate = BinanceRequestGate(BinanceGateConfig(retry_attempts=1))
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="https://fapi.binance.com", transport=transport) as client:
        with caplog.at_level(logging.INFO, logger="meowbot"):
            with pytest.raises(httpx.HTTPStatusError):
                await gate.execute_json(
                    client=client,
                    method="DELETE",
                    path="/fapi/v1/algoOrder",
                    params={"algoId": "4000001163278224", "signature": "secret"},
                    weight=1,
                )

    assert "idempotent cancel not found" in caplog.text
    assert "non-retriable" not in caplog.text
    assert all(
        record.levelno < logging.WARNING
        for record in caplog.records
        if "[binance-gate]" in record.getMessage()
    )
