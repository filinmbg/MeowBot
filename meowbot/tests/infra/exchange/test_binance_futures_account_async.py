from __future__ import annotations

import httpx
import pytest

from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsync,
    BinanceFuturesAccountAsyncConfig,
    ExchangeStateFetchError,
    reset_binance_time_sync_state_for_tests,
)
from meowbot.infra.exchange.binance_request_gate import BinanceRequestGate


@pytest.fixture(autouse=True)
def reset_time_sync_state():
    reset_binance_time_sync_state_for_tests()
    yield
    reset_binance_time_sync_state_for_tests()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _http_1021_error(path: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://fapi.binance.com{path}")
    response = httpx.Response(
        400,
        json={"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."},
        request=request,
    )
    return httpx.HTTPStatusError("timestamp drift", request=request, response=response)


def _http_binance_error(path: str, *, code: int, msg: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://fapi.binance.com{path}")
    response = httpx.Response(
        400,
        json={"code": code, "msg": msg},
        request=request,
    )
    return httpx.HTTPStatusError("binance error", request=request, response=response)


class FakeRetryGate:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.signed_attempt = 0

    async def execute_json(self, *, client, method, path, params=None, params_factory=None, weight=1):
        built_params = params_factory() if callable(params_factory) else dict(params or {})
        self.calls.append({"method": method, "path": path, "params": dict(built_params), "weight": weight})
        if path == "/fapi/v1/time":
            return {"serverTime": 100_500}
        if path == "/fapi/v2/positionRisk":
            self.signed_attempt += 1
            if self.signed_attempt == 1:
                raise _http_1021_error(path)
            return [{"symbol": "BTCUSDT", "positionAmt": "0"}]
        return {"ok": True}


class FakeAlways1021Gate:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_json(self, *, client, method, path, params=None, params_factory=None, weight=1):
        built_params = params_factory() if callable(params_factory) else dict(params or {})
        self.calls.append({"method": method, "path": path, "params": dict(built_params), "weight": weight})
        if path == "/fapi/v1/time":
            return {"serverTime": 100_500}
        raise _http_1021_error(path)


class FakeTimeGate:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_json(self, *, client, method, path, params=None, params_factory=None, weight=1):
        self.calls.append({"method": method, "path": path, "weight": weight})
        if path == "/fapi/v1/time":
            return {"serverTime": 100_500}
        return {"ok": True}


class FakeSuccessGate:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_json(self, *, client, method, path, params=None, params_factory=None, weight=1):
        built_params = params_factory() if callable(params_factory) else dict(params or {})
        self.calls.append({"method": method, "path": path, "params": dict(built_params), "weight": weight})
        return {"ok": True}


@pytest.mark.anyio
async def test_signed_request_retries_after_syncing_server_time_on_1021() -> None:
    account = BinanceFuturesAccountAsync(
        api_key="key",
        api_secret="secret",
        config=BinanceFuturesAccountAsyncConfig(
            recv_window_ms=12000,
            signed_request_safety_margin_ms=500,
        ),
    )
    gate = FakeRetryGate()
    account._futures_gate = gate
    account._futures_client = httpx.AsyncClient(base_url="https://fapi.binance.com")

    local_values = iter([100_000, 100_600, 100_700, 100_800, 100_900, 101_000])

    def fake_time() -> float:
        try:
            value = next(local_values)
        except StopIteration:
            value = 101_000
        return value / 1000.0

    original_time = __import__("meowbot.infra.exchange.binance_futures_account_async", fromlist=["time"]).time.time
    module = __import__("meowbot.infra.exchange.binance_futures_account_async", fromlist=["time"])
    module.time.time = fake_time
    try:
        payload = await account._signed_request_json(
            client=account._futures_client,
            gate=gate,
            method="GET",
            path="/fapi/v2/positionRisk",
            params={"symbol": "BTCUSDT"},
            weight=5,
        )
    finally:
        module.time.time = original_time
        await account._futures_client.aclose()

    assert isinstance(payload, list)
    assert len(gate.calls) == 3
    assert gate.calls[0]["path"] == "/fapi/v2/positionRisk"
    assert gate.calls[0]["params"]["recvWindow"] == 30000
    assert gate.calls[0]["params"]["timestamp"] == 97_000
    assert gate.calls[1]["path"] == "/fapi/v1/time"
    assert gate.calls[2]["path"] == "/fapi/v2/positionRisk"
    assert gate.calls[2]["params"]["recvWindow"] == 30000
    assert gate.calls[2]["params"]["timestamp"] == 97_650
    assert account._server_time_offset_ms < 0


def test_signed_timestamp_uses_server_offset_and_safety_margin() -> None:
    account = BinanceFuturesAccountAsync(
        api_key="key",
        api_secret="secret",
        config=BinanceFuturesAccountAsyncConfig(
            recv_window_ms=10000,
            signed_request_safety_margin_ms=500,
        ),
    )
    account._futures_time_sync_state.stable_offset_ms = -1000

    assert account._current_timestamp_ms(local_time_ms=200_000) == 196_000


def test_default_signed_request_safety_margin_is_3000ms() -> None:
    account = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")

    assert account._signed_request_safety_margin_ms() == 3000


def test_recv_window_is_clamped_to_minimum_30000ms() -> None:
    account = BinanceFuturesAccountAsync(
        api_key="key",
        api_secret="secret",
        config=BinanceFuturesAccountAsyncConfig(recv_window_ms=5000),
    )

    assert account._recv_window_ms() == 30000


def test_time_sync_state_is_shared_between_account_clients() -> None:
    first = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")
    first._futures_time_sync_state.stable_offset_ms = -222

    second = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")

    assert second._futures_time_sync_state is first._futures_time_sync_state
    assert second._current_timestamp_ms(local_time_ms=100_000) == 96_778


@pytest.mark.anyio
async def test_signed_request_uses_clamped_recv_window() -> None:
    account = BinanceFuturesAccountAsync(
        api_key="key",
        api_secret="secret",
        config=BinanceFuturesAccountAsyncConfig(recv_window_ms=5000),
    )
    gate = FakeSuccessGate()
    client = httpx.AsyncClient(base_url="https://fapi.binance.com")

    try:
        await account._signed_request_json(
            client=client,
            gate=gate,
            method="GET",
            path="/fapi/v1/openOrders",
            params={"symbol": "BTCUSDT"},
            weight=1,
        )
    finally:
        await client.aclose()

    assert gate.calls[0]["params"]["recvWindow"] == 30000


@pytest.mark.anyio
async def test_time_sync_reuses_fresh_offset_if_another_task_already_refreshed() -> None:
    account = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")
    gate = FakeTimeGate()
    account._futures_time_sync_state.stable_offset_ms = -740
    account._futures_time_sync_state.last_binance_server_time_ms = 100_500
    account._futures_time_sync_state.last_time_sync_monotonic = __import__(
        "meowbot.infra.exchange.binance_futures_account_async",
        fromlist=["time"],
    ).time.monotonic()
    client = httpx.AsyncClient(base_url="https://fapi.binance.com")

    try:
        payload = await account._sync_server_time(
            client=client,
            gate=gate,
            expected_offset_ms=2920,
        )
    finally:
        await client.aclose()

    assert payload["time_sync_reused"] is True
    assert payload["offset_before"] == 2920
    assert payload["offset_after"] == -740
    assert not [call for call in gate.calls if call["path"] == "/fapi/v1/time"]


@pytest.mark.anyio
async def test_high_rtt_time_sync_sample_does_not_replace_stable_offset() -> None:
    account = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")
    gate = FakeTimeGate()
    state = account._futures_time_sync_state
    state.stable_offset_ms = -500
    state.last_good_offset_ms = -500
    state.sync_confidence = "high"
    state.healthy = True
    client = httpx.AsyncClient(base_url="https://fapi.binance.com")

    local_values = iter([100_000, 106_000])
    monotonic_values = iter([10.0, 10.0, 10.0, 16.5])

    def fake_time() -> float:
        try:
            value = next(local_values)
        except StopIteration:
            value = 106_000
        return value / 1000.0

    def fake_monotonic() -> float:
        try:
            return next(monotonic_values)
        except StopIteration:
            return 16.5

    module = __import__("meowbot.infra.exchange.binance_futures_account_async", fromlist=["time"])
    original_time = module.time.time
    original_monotonic = module.time.monotonic
    module.time.time = fake_time
    module.time.monotonic = fake_monotonic
    try:
        payload = await account._sync_server_time(
            client=client,
            gate=gate,
            expected_offset_ms=state.stable_offset_ms,
            force=True,
        )
    finally:
        module.time.time = original_time
        module.time.monotonic = original_monotonic
        await client.aclose()

    assert payload["round_trip_ms"] == 6500
    assert payload["sample_accepted"] is False
    assert payload["stable_offset_ms"] == -500
    assert payload["sync_healthy"] is False
    assert payload["sync_confidence"] == "degraded"


@pytest.mark.anyio
async def test_retry_1021_failure_includes_time_diagnostics_in_strict_fetch_error() -> None:
    account = BinanceFuturesAccountAsync(
        api_key="key",
        api_secret="secret",
        config=BinanceFuturesAccountAsyncConfig(
            recv_window_ms=10000,
            signed_request_safety_margin_ms=500,
        ),
    )
    gate = FakeAlways1021Gate()
    account._futures_gate = gate
    account._futures_client = httpx.AsyncClient(base_url="https://fapi.binance.com")

    local_values = iter([100_000, 100_600, 100_700, 100_800, 100_900, 101_000])

    def fake_time() -> float:
        try:
            value = next(local_values)
        except StopIteration:
            value = 101_000
        return value / 1000.0

    original_time = __import__("meowbot.infra.exchange.binance_futures_account_async", fromlist=["time"]).time.time
    module = __import__("meowbot.infra.exchange.binance_futures_account_async", fromlist=["time"])
    module.time.time = fake_time
    try:
        with pytest.raises(ExchangeStateFetchError) as caught:
            await account.get_open_orders(symbol="BTCUSDT", raise_on_error=True)
    finally:
        module.time.time = original_time
        await account._futures_client.aclose()

    details = caught.value.details
    assert details["http_status"] == 400
    assert details["http_path"] == "/fapi/v1/openOrders"
    assert details["exchange_error_code"] == -1021
    assert details["local_time_ms"] >= 100_800
    assert details["binance_server_time_ms"] == details["local_time_ms"] + details["server_time_offset_ms"]
    assert details["safety_margin_ms"] == 3000
    assert details["final_timestamp"] == details["binance_server_time_ms"] - 3000
    assert details["recvWindow"] == 30000
    assert details["retry_after_time_sync"] is True
    assert "offset_before" in details
    assert "offset_after" in details
    assert "local_before" in details
    assert "local_after" in details
    assert "round_trip_ms" in details
    assert "estimated_local_at_response" in details
    assert "lock_wait_ms" in details
    assert details["sync_confidence"] in {"high", "medium", "low", "degraded"}


@pytest.mark.anyio
async def test_get_open_orders_can_raise_strict_exchange_fetch_error() -> None:
    account = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")

    async def failing_signed_request_json(**kwargs):
        raise RuntimeError("clock drift")

    account._signed_request_json = failing_signed_request_json  # type: ignore[method-assign]

    with pytest.raises(ExchangeStateFetchError):
        await account.get_open_orders(symbol="BTCUSDT", raise_on_error=True)


@pytest.mark.anyio
async def test_strict_exchange_fetch_error_includes_binance_response_details() -> None:
    account = BinanceFuturesAccountAsync(api_key="key", api_secret="secret")

    async def failing_signed_request_json(**kwargs):
        raise _http_binance_error(
            "/fapi/v1/openOrders",
            code=-1021,
            msg="Timestamp for this request is outside of the recvWindow.",
        )

    account._signed_request_json = failing_signed_request_json  # type: ignore[method-assign]

    with pytest.raises(ExchangeStateFetchError) as caught:
        await account.get_open_orders(symbol="BTCUSDT", raise_on_error=True)

    details = caught.value.details
    assert details["http_status"] == 400
    assert details["http_path"] == "/fapi/v1/openOrders"
    assert details["exchange_error_code"] == -1021
    assert details["exchange_error_message"] == "Timestamp for this request is outside of the recvWindow."
    assert '"code":-1021' in details["response_body"] or '"code": -1021' in details["response_body"]
