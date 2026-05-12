from __future__ import annotations

from meowbot.core.services.runtime.symbol_validator import (
    filter_valid_binance_usdt_symbols,
    validate_binance_usdt_perp_symbol,
)


def test_symbol_validator_accepts_ascii_uppercase_usdt_symbols() -> None:
    result = validate_binance_usdt_perp_symbol(" btcusdt ")

    assert result.valid is True
    assert result.normalized_symbol == "BTCUSDT"


def test_symbol_validator_rejects_non_ascii_spaces_punctuation_and_non_usdt() -> None:
    invalid_symbols = [
        "\u5e01\u5b89\u4eba\u751fUSDT",
        "BTC USDT",
        "BTC-USDT",
        "BTCUSDC",
        "BTCUSDT!",
    ]

    results = [validate_binance_usdt_perp_symbol(symbol) for symbol in invalid_symbols]

    assert [result.valid for result in results] == [False, False, False, False, False]
    assert results[0].reason == "non_ascii"


def test_symbol_filter_dedupes_and_returns_invalid_details() -> None:
    valid, invalid = filter_valid_binance_usdt_symbols(
        ["btcusdt", "BTCUSDT", "ETHUSDT", "\u5e01\u5b89\u4eba\u751fUSDT"]
    )

    assert valid == ["BTCUSDT", "ETHUSDT"]
    assert len(invalid) == 1
    assert invalid[0].reason == "non_ascii"
