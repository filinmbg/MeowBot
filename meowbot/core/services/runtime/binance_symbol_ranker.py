from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from meowbot.infra.exchange.binance_futures_usdtm_async import BinanceFuturesMarketDataAsync
from meowbot.core.services.runtime.symbol_validator import (
    DEFAULT_SYMBOL_BLACKLIST,
    filter_valid_binance_usdt_symbols,
    validate_binance_usdt_perp_symbol,
)


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class SymbolRankingDiagnostics:
    all_symbols_count: int
    after_base_filter_count: int
    after_quality_filter_count: int
    selected_count: int
    first_20_symbols: list[str]
    excluded_examples: list[str]
    source: str
    invalid_symbols_skipped: int = 0


@dataclass(frozen=True)
class SymbolRankingResult:
    symbols: list[str]
    diagnostics: SymbolRankingDiagnostics


class BinanceFuturesSymbolRanker:
    def __init__(
        self,
        *,
        exchange: BinanceFuturesMarketDataAsync,
        core_symbols: list[str],
        blacklist: list[str] | None = None,
        cache_path: str | Path | None = None,
        min_quote_volume: float = 10_000_000.0,
        min_last_price: float = 0.0005,
        max_abs_price_change_percent: float = 25.0,
        max_symbols: int = 200,
    ) -> None:
        self.exchange = exchange
        blacklist_items = [*DEFAULT_SYMBOL_BLACKLIST, *(blacklist or [])]
        self.blacklist = {str(symbol).strip().upper() for symbol in blacklist_items if symbol and str(symbol).strip()}
        self.core_symbols, invalid_core = filter_valid_binance_usdt_symbols(
            core_symbols,
            blacklist=self.blacklist,
        )
        if invalid_core:
            log.warning(
                "[symbols] invalid core symbols skipped count=%s examples=%s",
                len(invalid_core),
                [f"{item.normalized_symbol or item.symbol}:{item.reason}" for item in invalid_core[:10]],
            )
        self.cache_path = Path(cache_path) if cache_path else None
        self.min_quote_volume = float(min_quote_volume)
        self.min_last_price = float(min_last_price)
        self.max_abs_price_change_percent = float(max_abs_price_change_percent)
        self.max_symbols = max(1, int(max_symbols))

    async def load_ranked_symbols(self) -> SymbolRankingResult:
        exchange_info = await self.exchange.get_exchange_info()
        tickers = await self.exchange.get_24hr_tickers()

        if exchange_info and tickers:
            result = self._build_from_payloads(exchange_info=exchange_info, tickers=tickers)
            self._save_cache(result)
            return result

        cached = self._load_cache()
        if cached is not None:
            return cached

        return self._static_fallback_result()

    def _build_from_payloads(self, *, exchange_info: dict, tickers: list[dict]) -> SymbolRankingResult:
        raw_symbols = exchange_info.get("symbols") or []
        all_symbols_count = len(raw_symbols)
        base_candidates: list[dict] = []
        excluded_examples: list[str] = []
        invalid_symbols_skipped = 0

        for row in raw_symbols:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            if row.get("quoteAsset") != "USDT":
                self._append_example(excluded_examples, f"{symbol}:quoteAsset")
                continue
            if row.get("contractType") != "PERPETUAL":
                self._append_example(excluded_examples, f"{symbol}:contractType")
                continue
            if row.get("status") != "TRADING":
                self._append_example(excluded_examples, f"{symbol}:status")
                continue
            symbol_validation = validate_binance_usdt_perp_symbol(symbol, blacklist=self.blacklist)
            if not symbol_validation.valid:
                invalid_symbols_skipped += 1
                self._append_example(excluded_examples, f"{symbol}:{symbol_validation.reason}")
                continue
            if symbol.startswith("10000") or symbol.startswith("1000"):
                self._append_example(excluded_examples, f"{symbol}:prefix")
                continue
            base_candidates.append(row)

        ticker_by_symbol = {
            str(row.get("symbol") or "").upper(): row
            for row in tickers
            if row.get("symbol")
        }

        qualified: list[dict] = []
        for row in base_candidates:
            symbol = str(row.get("symbol") or "").upper()
            ticker = ticker_by_symbol.get(symbol)
            if not ticker:
                self._append_example(excluded_examples, f"{symbol}:no_ticker")
                continue

            quote_volume = self._safe_float(ticker.get("quoteVolume"))
            last_price = self._safe_float(ticker.get("lastPrice"))
            abs_change = abs(self._safe_float(ticker.get("priceChangePercent")))

            if quote_volume < self.min_quote_volume:
                self._append_example(excluded_examples, f"{symbol}:volume")
                continue
            if last_price <= self.min_last_price:
                self._append_example(excluded_examples, f"{symbol}:price")
                continue
            if abs_change > self.max_abs_price_change_percent:
                self._append_example(excluded_examples, f"{symbol}:change")
                continue

            qualified.append(
                {
                    "symbol": symbol,
                    "quote_volume": quote_volume,
                    "last_price": last_price,
                    "abs_change": abs_change,
                }
            )

        core_available = {
            row["symbol"]: row
            for row in qualified
            if row["symbol"] in set(self.core_symbols)
        }
        ranked_symbols: list[str] = []
        for symbol in self.core_symbols:
            if symbol in core_available:
                ranked_symbols.append(symbol)

        non_core = [row for row in qualified if row["symbol"] not in core_available]
        non_core.sort(
            key=lambda row: (
                -row["quote_volume"],
                row["abs_change"],
                -row["last_price"],
                row["symbol"],
            )
        )
        ranked_symbols.extend(row["symbol"] for row in non_core)
        ranked_symbols = ranked_symbols[: self.max_symbols]

        diagnostics = SymbolRankingDiagnostics(
            all_symbols_count=all_symbols_count,
            after_base_filter_count=len(base_candidates),
            after_quality_filter_count=len(qualified),
            selected_count=len(ranked_symbols),
            first_20_symbols=ranked_symbols[:20],
            excluded_examples=excluded_examples[:20],
            source="binance_api",
            invalid_symbols_skipped=invalid_symbols_skipped,
        )
        return SymbolRankingResult(symbols=ranked_symbols, diagnostics=diagnostics)

    def _static_fallback_result(self) -> SymbolRankingResult:
        fallback_symbols, invalid_symbols = filter_valid_binance_usdt_symbols(
            self.core_symbols,
            blacklist=self.blacklist,
        )
        fallback_symbols = [
            symbol
            for symbol in fallback_symbols
            if not symbol.startswith("1000") and not symbol.startswith("10000")
        ][: self.max_symbols]
        return SymbolRankingResult(
            symbols=fallback_symbols,
            diagnostics=SymbolRankingDiagnostics(
                all_symbols_count=0,
                after_base_filter_count=0,
                after_quality_filter_count=0,
                selected_count=len(fallback_symbols),
                first_20_symbols=fallback_symbols[:20],
                excluded_examples=[
                    f"{item.normalized_symbol or item.symbol}:{item.reason}"
                    for item in invalid_symbols[:20]
                ],
                source="static_fallback",
                invalid_symbols_skipped=len(invalid_symbols),
            ),
        )

    def _load_cache(self) -> SymbolRankingResult | None:
        if self.cache_path is None or not self.cache_path.exists():
            return None

        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            cached_symbols, invalid_symbols = filter_valid_binance_usdt_symbols(
                payload.get("symbols", []),
                blacklist=self.blacklist,
            )
            if not cached_symbols:
                return None
            diagnostics_payload = payload.get("diagnostics") or {}
            return SymbolRankingResult(
                symbols=cached_symbols[: self.max_symbols],
                diagnostics=SymbolRankingDiagnostics(
                    all_symbols_count=int(diagnostics_payload.get("all_symbols_count", 0) or 0),
                    after_base_filter_count=int(diagnostics_payload.get("after_base_filter_count", 0) or 0),
                    after_quality_filter_count=int(diagnostics_payload.get("after_quality_filter_count", 0) or 0),
                    selected_count=min(len(cached_symbols), self.max_symbols),
                    first_20_symbols=cached_symbols[:20],
                    excluded_examples=(
                        list(diagnostics_payload.get("excluded_examples") or [])
                        + [
                            f"{item.normalized_symbol or item.symbol}:{item.reason}"
                            for item in invalid_symbols[:20]
                        ]
                    )[:20],
                    source="cache",
                    invalid_symbols_skipped=int(diagnostics_payload.get("invalid_symbols_skipped", 0) or 0)
                    + len(invalid_symbols),
                ),
            )
        except Exception:
            log.exception("[symbols] failed to load ranked symbols cache path=%s", self.cache_path)
            return None

    def _save_cache(self, result: SymbolRankingResult) -> None:
        if self.cache_path is None:
            return

        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(
                    {
                        "symbols": result.symbols,
                        "diagnostics": {
                            "all_symbols_count": result.diagnostics.all_symbols_count,
                            "after_base_filter_count": result.diagnostics.after_base_filter_count,
                            "after_quality_filter_count": result.diagnostics.after_quality_filter_count,
                            "selected_count": result.diagnostics.selected_count,
                            "excluded_examples": result.diagnostics.excluded_examples,
                            "source": result.diagnostics.source,
                            "invalid_symbols_skipped": result.diagnostics.invalid_symbols_skipped,
                        },
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.exception("[symbols] failed to save ranked symbols cache path=%s", self.cache_path)

    @staticmethod
    def _safe_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _append_example(examples: list[str], item: str, *, limit: int = 20) -> None:
        if len(examples) < limit:
            examples.append(item)
