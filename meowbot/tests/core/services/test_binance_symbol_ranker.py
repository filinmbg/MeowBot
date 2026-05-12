from __future__ import annotations

import json

import pytest

from meowbot.core.services.runtime.binance_symbol_ranker import BinanceFuturesSymbolRanker


pytestmark = pytest.mark.anyio("asyncio")


class FakeExchange:
    def __init__(self, *, exchange_info: dict | None, tickers: list[dict] | None) -> None:
        self._exchange_info = exchange_info
        self._tickers = tickers

    async def get_exchange_info(self) -> dict | None:
        return self._exchange_info

    async def get_24hr_tickers(self) -> list[dict] | None:
        return self._tickers


async def test_ranker_prioritizes_core_symbols_then_quality_sorted_non_core(tmp_path) -> None:
    exchange = FakeExchange(
        exchange_info={
            "symbols": [
                {"symbol": "ETHUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "BTCUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "XRPUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "LTCUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "ALGOUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "ATOMUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "1000PEPEUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "\u5e01\u5b89\u4eba\u751fUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "BAD-USDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            ]
        },
        tickers=[
            {"symbol": "ETHUSDT", "quoteVolume": "32000000", "lastPrice": "2500", "priceChangePercent": "3.1"},
            {"symbol": "BTCUSDT", "quoteVolume": "99000000", "lastPrice": "65000", "priceChangePercent": "2.2"},
            {"symbol": "XRPUSDT", "quoteVolume": "44000000", "lastPrice": "0.61", "priceChangePercent": "1.1"},
            {"symbol": "LTCUSDT", "quoteVolume": "18000000", "lastPrice": "82", "priceChangePercent": "4.1"},
            {"symbol": "ALGOUSDT", "quoteVolume": "21000000", "lastPrice": "0.21", "priceChangePercent": "2.9"},
            {"symbol": "ATOMUSDT", "quoteVolume": "15000000", "lastPrice": "8.1", "priceChangePercent": "1.7"},
            {"symbol": "1000PEPEUSDT", "quoteVolume": "22000000", "lastPrice": "0.0012", "priceChangePercent": "6"},
            {"symbol": "\u5e01\u5b89\u4eba\u751fUSDT", "quoteVolume": "99000000", "lastPrice": "1", "priceChangePercent": "1"},
            {"symbol": "BAD-USDT", "quoteVolume": "99000000", "lastPrice": "1", "priceChangePercent": "1"},
        ],
    )
    ranker = BinanceFuturesSymbolRanker(
        exchange=exchange,
        core_symbols=["BTCUSDT", "ETHUSDT", "XRPUSDT", "ATOMUSDT"],
        cache_path=tmp_path / "symbols.json",
        max_symbols=10,
    )

    result = await ranker.load_ranked_symbols()

    assert result.symbols == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ATOMUSDT", "ALGOUSDT", "LTCUSDT"]
    assert result.diagnostics.source == "binance_api"
    assert result.diagnostics.after_base_filter_count == 6
    assert result.diagnostics.invalid_symbols_skipped == 2
    assert "1000PEPEUSDT:prefix" in result.diagnostics.excluded_examples
    assert "\u5e01\u5b89\u4eba\u751fUSDT:non_ascii" in result.diagnostics.excluded_examples
    assert "BAD-USDT:regex" in result.diagnostics.excluded_examples


async def test_ranker_applies_quality_filters_and_blacklist(tmp_path) -> None:
    exchange = FakeExchange(
        exchange_info={
            "symbols": [
                {"symbol": "GOODUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "LOWVOLUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "TINYUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "WILDUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
                {"symbol": "BLACKUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            ]
        },
        tickers=[
            {"symbol": "GOODUSDT", "quoteVolume": "15000000", "lastPrice": "2.1", "priceChangePercent": "5"},
            {"symbol": "LOWVOLUSDT", "quoteVolume": "9000000", "lastPrice": "1.0", "priceChangePercent": "2"},
            {"symbol": "TINYUSDT", "quoteVolume": "16000000", "lastPrice": "0.0004", "priceChangePercent": "2"},
            {"symbol": "WILDUSDT", "quoteVolume": "17000000", "lastPrice": "1.5", "priceChangePercent": "31"},
            {"symbol": "BLACKUSDT", "quoteVolume": "17000000", "lastPrice": "1.5", "priceChangePercent": "2"},
        ],
    )
    ranker = BinanceFuturesSymbolRanker(
        exchange=exchange,
        core_symbols=[],
        blacklist=["BLACKUSDT"],
        cache_path=tmp_path / "symbols.json",
        max_symbols=10,
    )

    result = await ranker.load_ranked_symbols()

    assert result.symbols == ["GOODUSDT"]
    assert "LOWVOLUSDT:volume" in result.diagnostics.excluded_examples
    assert "TINYUSDT:price" in result.diagnostics.excluded_examples
    assert "WILDUSDT:change" in result.diagnostics.excluded_examples
    assert "BLACKUSDT:blacklist" in result.diagnostics.excluded_examples


async def test_ranker_uses_cache_then_static_fallback(tmp_path) -> None:
    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps(
            {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "diagnostics": {"all_symbols_count": 300, "after_base_filter_count": 250, "after_quality_filter_count": 200},
            }
        ),
        encoding="utf-8",
    )
    cached_ranker = BinanceFuturesSymbolRanker(
        exchange=FakeExchange(exchange_info=None, tickers=None),
        core_symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        cache_path=cache_path,
        max_symbols=10,
    )
    cached_result = await cached_ranker.load_ranked_symbols()

    assert cached_result.symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert cached_result.diagnostics.source == "cache"

    static_ranker = BinanceFuturesSymbolRanker(
        exchange=FakeExchange(exchange_info=None, tickers=None),
        core_symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        cache_path=tmp_path / "missing.json",
        blacklist=["ETHUSDT"],
        max_symbols=10,
    )
    static_result = await static_ranker.load_ranked_symbols()

    assert static_result.symbols == ["BTCUSDT", "BNBUSDT"]
    assert static_result.diagnostics.source == "static_fallback"
