from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


VALID_BINANCE_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")
DEFAULT_SYMBOL_BLACKLIST = frozenset({"\u5e01\u5b89\u4eba\u751fUSDT"})


@dataclass(frozen=True)
class SymbolValidationResult:
    symbol: str
    normalized_symbol: str
    valid: bool
    reason: str


def normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def validate_binance_usdt_perp_symbol(
    symbol: object,
    *,
    blacklist: Iterable[str] | None = None,
) -> SymbolValidationResult:
    raw = str(symbol or "")
    normalized = normalize_symbol(raw)
    blacklist_set = {normalize_symbol(item) for item in (blacklist or DEFAULT_SYMBOL_BLACKLIST)}

    if not normalized:
        return SymbolValidationResult(raw, normalized, False, "empty")
    if not normalized.isascii():
        return SymbolValidationResult(raw, normalized, False, "non_ascii")
    if not VALID_BINANCE_USDT_SYMBOL_RE.fullmatch(normalized):
        return SymbolValidationResult(raw, normalized, False, "regex")
    if normalized in blacklist_set:
        return SymbolValidationResult(raw, normalized, False, "blacklist")
    return SymbolValidationResult(raw, normalized, True, "ok")


def is_valid_binance_usdt_perp_symbol(
    symbol: object,
    *,
    blacklist: Iterable[str] | None = None,
) -> bool:
    return validate_binance_usdt_perp_symbol(symbol, blacklist=blacklist).valid


def filter_valid_binance_usdt_symbols(
    symbols: Iterable[object],
    *,
    blacklist: Iterable[str] | None = None,
) -> tuple[list[str], list[SymbolValidationResult]]:
    valid: list[str] = []
    invalid: list[SymbolValidationResult] = []
    seen: set[str] = set()

    for symbol in symbols:
        result = validate_binance_usdt_perp_symbol(symbol, blacklist=blacklist)
        if not result.valid:
            invalid.append(result)
            continue
        if result.normalized_symbol in seen:
            continue
        seen.add(result.normalized_symbol)
        valid.append(result.normalized_symbol)

    return valid, invalid
