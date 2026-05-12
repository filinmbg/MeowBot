from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


DEFAULT_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]

ENTRY_FEATURE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "hour_utc",
    "day_of_week_utc",

    "ema9",
    "ema21",
    "ema50",
    "ema100",
    "ema200",
    "dist_close_ema21_pct",
    "dist_close_ema50_pct",
    "dist_close_ema100_pct",
    "dist_close_ema200_pct",
    "abs_dist_close_ema21_pct",
    "abs_dist_close_ema50_pct",
    "ema21_slope_3_pct",
    "ema21_slope_5_pct",
    "ema21_slope_10_pct",
    "ema50_slope_3_pct",
    "ema50_slope_5_pct",
    "ema50_slope_10_pct",
    "ema100_slope_5_pct",
    "ema200_slope_5_pct",
    "trend_ema50_above_ema200",
    "trend_close_above_ema50",
    "trend_close_above_ema200",

    "rsi14",
    "rsi14_slope_3",
    "rsi14_slope_5",
    "rsi14_min_5",
    "rsi14_max_5",
    "rsi14_min_10",
    "rsi14_max_10",
    "rsi_cross_up_45",
    "rsi_cross_up_50",
    "rsi_cross_up_55",
    "rsi_cross_down_45",
    "rsi_cross_down_50",
    "rsi_cross_down_55",

    "macd",
    "macd_signal",
    "macd_hist",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "macd_bullish",
    "macd_hist_positive",

    "atr14",
    "atr_pct",
    "plus_di14",
    "minus_di14",
    "adx14",
    "di_bullish",
    "adx_above_15",
    "adx_above_20",
    "adx_above_25",

    "supertrend",
    "supertrend_direction",
    "supertrend_up",
    "supertrend_down",
    "supertrend_fast",
    "supertrend_fast_direction",
    "supertrend_fast_up",
    "supertrend_fast_down",
    "dist_close_supertrend_pct",

    "bb_middle",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "bb_percent",
    "close_above_bb_middle",
    "close_near_bb_upper",
    "close_near_bb_lower",

    "volume_ma20",
    "volume_ma50",
    "volume_ratio",
    "volume_ratio_50",
    "volume_above_ma20",
    "volume_ratio_above_1_2",
    "volume_ratio_above_1_5",
    "volume_ratio_above_2_0",
    "taker_buy_ratio",
    "taker_buy_ratio_above_0_55",
    "taker_buy_ratio_below_0_45",

    "candle_range_pct",
    "body_pct",
    "body_to_range",
    "upper_wick_pct",
    "lower_wick_pct",
    "upper_wick_to_range",
    "lower_wick_to_range",
    "is_bullish_candle",
    "is_bearish_candle",
    "is_doji",
    "is_bullish_pinbar",
    "is_bearish_pinbar",
    "is_bullish_engulfing",
    "is_bearish_engulfing",

    "return_1",
    "return_2",
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "green_candles_3",
    "green_candles_5",
    "red_candles_3",
    "red_candles_5",

    "high_20",
    "low_20",
    "high_50",
    "low_50",
    "high_100",
    "low_100",
    "dist_to_high_20_pct",
    "dist_to_low_20_pct",
    "dist_to_high_50_pct",
    "dist_to_low_50_pct",
    "dist_to_high_100_pct",
    "dist_to_low_100_pct",
    "break_high_20",
    "break_low_20",
    "break_high_50",
    "break_low_50",

    "pivot",
    "r1",
    "s1",
    "r2",
    "s2",
    "dist_to_pivot_pct",
    "dist_to_r1_pct",
    "dist_to_s1_pct",
    "dist_to_r2_pct",
    "dist_to_s2_pct",
    "close_above_pivot",

    "long_trend_basic",
    "short_trend_basic",
    "long_impulse_basic",
    "short_impulse_basic",
    "long_volume_confirm",
    "short_volume_confirm",
    "market_has_min_volatility",
    "market_too_volatile",
]


EDGE_BINS = {
    "rsi14": [0, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 100],
    "adx14": [0, 8, 12, 15, 18, 20, 25, 30, 35, 40, 60, 100],
    "atr_pct": [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.80, 1.20, 1.80, 2.50, 4.00, 100],
    "volume_ratio": [0, 0.50, 0.80, 1.00, 1.20, 1.50, 2.00, 3.00, 5.00, 100],
    "taker_buy_ratio": [0, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.00],
    "dist_close_ema21_pct": [-100, -5, -3, -2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2, 3, 5, 100],
    "dist_close_ema50_pct": [-100, -5, -3, -2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2, 3, 5, 100],
    "bb_width": [0, 0.20, 0.40, 0.70, 1.00, 1.50, 2.00, 3.00, 5.00, 8.00, 100],
    "bb_percent": [-100, 0, 10, 20, 35, 50, 65, 80, 90, 100, 200],
    "body_pct": [0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.80, 1.20, 2.00, 100],
    "upper_wick_pct": [0, 0.03, 0.05, 0.10, 0.20, 0.35, 0.50, 0.80, 1.20, 100],
    "lower_wick_pct": [0, 0.03, 0.05, 0.10, 0.20, 0.35, 0.50, 0.80, 1.20, 100],
    "return_3": [-100, -5, -3, -2, -1, -0.5, -0.2, 0, 0.2, 0.5, 1, 2, 3, 5, 100],
    "return_5": [-100, -5, -3, -2, -1, -0.5, -0.2, 0, 0.2, 0.5, 1, 2, 3, 5, 100],
}


@dataclass(frozen=True)
class Condition:
    col: str
    op: str
    value: Any
    value2: Any | None = None


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    side: str
    family: str
    description: str
    conditions: tuple[Condition, ...]


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def ms_to_utc_str(ms: int | float | None) -> str:
    if ms is None or pd.isna(ms):
        return ""

    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def read_csv_any(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="infer")


def open_text_for_write(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8", newline="")

    return path.open("w", encoding="utf-8", newline="")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        result = float(value)

        if math.isnan(result) or math.isinf(result):
            return default

        return result
    except Exception:
        return default


def to_csv_value(value: Any) -> Any:
    if value is None:
        return ""

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        value = float(value)

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return round(value, 10)

    return value


def discover_symbols(data_dir: Path) -> list[str]:
    if not data_dir.exists():
        return []

    symbols: list[str] = []

    for path in data_dir.iterdir():
        if path.is_dir():
            symbols.append(path.name.upper())

    return sorted(set(symbols))


def find_indicator_file(data_dir: Path, symbol: str, timeframe: str) -> Path | None:
    symbol_dir = data_dir / symbol

    candidates = [
        symbol_dir / f"{symbol}_{timeframe}_critical_indicators.csv.gz",
        symbol_dir / f"{symbol}_{timeframe}_critical_indicators.csv",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def find_1m_file(data_dir: Path, symbol: str) -> Path | None:
    symbol_dir = data_dir / symbol

    candidates = [
        symbol_dir / f"{symbol}_1m.csv.gz",
        symbol_dir / f"{symbol}_1m.csv",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def normalize_price_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    required = ["open_time", "open", "high", "low", "close"]
    missing = [col for col in required if col not in df.columns]

    if missing:
        raise ValueError(f"Missing price columns: {missing}")

    numeric_cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    df["open_time"] = df["open_time"].astype("int64")
    df = df.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)

    return df


def prepare_entry_df(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_price_df(df)

    if "rsi14" in df.columns:
        df["rsi14_prev"] = df["rsi14"].shift(1)

        df["rsi14_min_5"] = df["rsi14"].rolling(5).min()
        df["rsi14_max_5"] = df["rsi14"].rolling(5).max()
        df["rsi14_min_10"] = df["rsi14"].rolling(10).min()
        df["rsi14_max_10"] = df["rsi14"].rolling(10).max()

        df["rsi_cross_up_45"] = ((df["rsi14"] > 45) & (df["rsi14_prev"] <= 45)).astype("int8")
        df["rsi_cross_up_50"] = ((df["rsi14"] > 50) & (df["rsi14_prev"] <= 50)).astype("int8")
        df["rsi_cross_up_55"] = ((df["rsi14"] > 55) & (df["rsi14_prev"] <= 55)).astype("int8")

        df["rsi_cross_down_45"] = ((df["rsi14"] < 45) & (df["rsi14_prev"] >= 45)).astype("int8")
        df["rsi_cross_down_50"] = ((df["rsi14"] < 50) & (df["rsi14_prev"] >= 50)).astype("int8")
        df["rsi_cross_down_55"] = ((df["rsi14"] < 55) & (df["rsi14_prev"] >= 55)).astype("int8")

    for col in [
        "dist_close_ema21_pct",
        "dist_close_ema50_pct",
        "dist_close_ema100_pct",
        "dist_close_ema200_pct",
    ]:
        if col in df.columns:
            df[f"abs_{col}"] = df[col].abs()

    return df.copy()


def condition_to_text(cond: Condition) -> str:
    if cond.op in {"gt_col", "ge_col", "lt_col", "le_col"}:
        op_map = {
            "gt_col": ">",
            "ge_col": ">=",
            "lt_col": "<",
            "le_col": "<=",
        }
        return f"{cond.col} {op_map[cond.op]} {cond.value}"

    if cond.op == "gt":
        return f"{cond.col} > {cond.value}"

    if cond.op == "ge":
        return f"{cond.col} >= {cond.value}"

    if cond.op == "lt":
        return f"{cond.col} < {cond.value}"

    if cond.op == "le":
        return f"{cond.col} <= {cond.value}"

    if cond.op == "eq":
        return f"{cond.col} == {cond.value}"

    if cond.op == "between":
        return f"{cond.value} <= {cond.col} <= {cond.value2}"

    if cond.op == "abs_le":
        return f"abs({cond.col}) <= {cond.value}"

    return f"{cond.col} {cond.op} {cond.value}"


def evaluate_condition(df: pd.DataFrame, cond: Condition) -> pd.Series:
    if cond.col not in df.columns:
        return pd.Series(False, index=df.index)

    if cond.op == "eq":
        s = df[cond.col]
    else:
        s = pd.to_numeric(df[cond.col], errors="coerce")

    if cond.op == "gt":
        return s > cond.value

    if cond.op == "ge":
        return s >= cond.value

    if cond.op == "lt":
        return s < cond.value

    if cond.op == "le":
        return s <= cond.value

    if cond.op == "eq":
        return s == cond.value

    if cond.op == "between":
        return (s >= cond.value) & (s <= cond.value2)

    if cond.op == "abs_le":
        return pd.to_numeric(s, errors="coerce").abs() <= cond.value

    if cond.op == "gt_col":
        if cond.value not in df.columns:
            return pd.Series(False, index=df.index)
        return pd.to_numeric(df[cond.col], errors="coerce") > pd.to_numeric(df[cond.value], errors="coerce")

    if cond.op == "ge_col":
        if cond.value not in df.columns:
            return pd.Series(False, index=df.index)
        return pd.to_numeric(df[cond.col], errors="coerce") >= pd.to_numeric(df[cond.value], errors="coerce")

    if cond.op == "lt_col":
        if cond.value not in df.columns:
            return pd.Series(False, index=df.index)
        return pd.to_numeric(df[cond.col], errors="coerce") < pd.to_numeric(df[cond.value], errors="coerce")

    if cond.op == "le_col":
        if cond.value not in df.columns:
            return pd.Series(False, index=df.index)
        return pd.to_numeric(df[cond.col], errors="coerce") <= pd.to_numeric(df[cond.value], errors="coerce")

    raise ValueError(f"Unsupported condition op: {cond.op}")


def evaluate_rule(df: pd.DataFrame, rule: RuleSpec) -> pd.Series:
    mask = pd.Series(True, index=df.index)

    for cond in rule.conditions:
        mask = mask & evaluate_condition(df, cond)

    return mask.fillna(False)


def evaluate_edge_filter(df: pd.DataFrame, side: str, profile: str) -> pd.Series:
    if profile in {"", "none", None}:
        return pd.Series(True, index=df.index)

    if profile not in {"v1", "v1_taker"}:
        raise ValueError(f"Unsupported edge filter profile: {profile}")

    mask = pd.Series(True, index=df.index)

    def numeric(col: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return pd.to_numeric(df[col], errors="coerce")

    mask &= numeric("bb_width") <= 8.0

    if side == "long":
        mask &= numeric("dist_close_ema50_pct") <= 5.0
        mask &= numeric("dist_close_ema21_pct") <= 5.0
        mask &= numeric("bb_percent") <= 100.0
        mask &= numeric("upper_wick_pct") <= 1.2
        mask &= numeric("return_5") <= 5.0

        if profile == "v1_taker":
            mask &= numeric("taker_buy_ratio") > 0.55

    elif side == "short":
        mask &= numeric("dist_close_ema50_pct") >= -5.0
        mask &= numeric("dist_close_ema21_pct") >= -5.0
        mask &= numeric("bb_percent") >= 0.0
        mask &= numeric("lower_wick_pct") <= 1.2
        mask &= numeric("return_5") >= -5.0

        if profile == "v1_taker":
            mask &= numeric("taker_buy_ratio") < 0.45

    else:
        mask &= False

    return mask.fillna(False)


def make_rule_id(side: str, family: str, conditions: Iterable[Condition], counter: int) -> str:
    raw = "|".join(condition_to_text(c) for c in conditions)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return f"{side.upper()}_{family.upper()}_{counter:05d}_{digest}"


def dedupe_rule_sets(rule_sets: list[tuple[str, str, list[Condition]]]) -> list[tuple[str, str, list[Condition]]]:
    seen: set[tuple[str, ...]] = set()
    result: list[tuple[str, str, list[Condition]]] = []

    for family, desc, conditions in rule_sets:
        key = tuple(condition_to_text(c) for c in conditions)

        if key in seen:
            continue

        seen.add(key)
        result.append((family, desc, conditions))

    return result


def generate_rules(max_rules_per_side: int = 800, only_side: str = "both") -> list[RuleSpec]:
    rule_sets_long: list[tuple[str, str, list[Condition]]] = []
    rule_sets_short: list[tuple[str, str, list[Condition]]] = []

    adx_filters = [
        [],
        [Condition("adx14", "gt", 12)],
        [Condition("adx14", "gt", 15)],
        [Condition("adx14", "gt", 18)],
        [Condition("adx14", "gt", 20)],
        [Condition("adx14", "gt", 25)],
    ]

    volume_filters = [
        [],
        [Condition("volume_ratio", "gt", 0.8)],
        [Condition("volume_ratio", "gt", 1.0)],
        [Condition("volume_ratio", "gt", 1.2)],
        [Condition("volume_ratio", "gt", 1.5)],
    ]

    atr_filters = [
        [],
        [Condition("atr_pct", "between", 0.08, 5.0)],
        [Condition("atr_pct", "between", 0.15, 4.0)],
        [Condition("atr_pct", "between", 0.20, 3.0)],
        [Condition("atr_pct", "between", 0.30, 2.5)],
    ]

    long_trend_cores = [
        [Condition("close", "gt_col", "ema50"), Condition("ema50_slope_5_pct", "gt", 0)],
        [Condition("close", "gt_col", "ema200"), Condition("ema50", "gt_col", "ema200")],
        [Condition("supertrend_up", "eq", 1)],
        [Condition("supertrend_fast_up", "eq", 1)],
        [Condition("long_trend_basic", "eq", 1)],
    ]

    long_momentum_filters = [
        [],
        [Condition("rsi14", "gt", 50)],
        [Condition("rsi14", "gt", 52)],
        [Condition("rsi14", "gt", 55)],
        [Condition("macd_hist", "gt", 0)],
        [Condition("macd_bullish", "eq", 1)],
        [Condition("rsi14", "gt", 50), Condition("macd_hist", "gt", 0)],
    ]

    for core in long_trend_cores:
        for mom in long_momentum_filters:
            for adx in adx_filters:
                for vol in volume_filters:
                    for atr in atr_filters:
                        conditions = [*core, *mom, *adx, *vol, *atr]
                        rule_sets_long.append(("trend", "LONG trend simple", conditions))

    short_trend_cores = [
        [Condition("close", "lt_col", "ema50"), Condition("ema50_slope_5_pct", "lt", 0)],
        [Condition("close", "lt_col", "ema200"), Condition("ema50", "lt_col", "ema200")],
        [Condition("supertrend_down", "eq", 1)],
        [Condition("supertrend_fast_down", "eq", 1)],
        [Condition("short_trend_basic", "eq", 1)],
    ]

    short_momentum_filters = [
        [],
        [Condition("rsi14", "lt", 50)],
        [Condition("rsi14", "lt", 48)],
        [Condition("rsi14", "lt", 45)],
        [Condition("macd_hist", "lt", 0)],
        [Condition("macd_bullish", "eq", 0)],
        [Condition("rsi14", "lt", 50), Condition("macd_hist", "lt", 0)],
    ]

    for core in short_trend_cores:
        for mom in short_momentum_filters:
            for adx in adx_filters:
                for vol in volume_filters:
                    for atr in atr_filters:
                        conditions = [*core, *mom, *adx, *vol, *atr]
                        rule_sets_short.append(("trend", "SHORT trend simple", conditions))

    breakout_long_cores = [
        [Condition("break_high_20", "eq", 1)],
        [Condition("break_high_50", "eq", 1)],
        [Condition("close", "gt_col", "bb_upper")],
    ]

    breakout_long_mom = [
        [],
        [Condition("macd_hist", "gt", 0)],
        [Condition("rsi14", "gt", 55)],
        [Condition("macd_hist", "gt", 0), Condition("rsi14", "gt", 52)],
    ]

    breakout_volume_filters = [
        [Condition("volume_ratio", "gt", 1.0)],
        [Condition("volume_ratio", "gt", 1.2)],
        [Condition("volume_ratio", "gt", 1.5)],
        [Condition("volume_ratio", "gt", 2.0)],
    ]

    for core in breakout_long_cores:
        for mom in breakout_long_mom:
            for vol in breakout_volume_filters:
                for adx in adx_filters:
                    for atr in atr_filters:
                        conditions = [*core, *mom, *vol, *adx, *atr]
                        rule_sets_long.append(("breakout", "LONG breakout volume", conditions))

    breakout_short_cores = [
        [Condition("break_low_20", "eq", 1)],
        [Condition("break_low_50", "eq", 1)],
        [Condition("close", "lt_col", "bb_lower")],
    ]

    breakout_short_mom = [
        [],
        [Condition("macd_hist", "lt", 0)],
        [Condition("rsi14", "lt", 45)],
        [Condition("macd_hist", "lt", 0), Condition("rsi14", "lt", 48)],
    ]

    for core in breakout_short_cores:
        for mom in breakout_short_mom:
            for vol in breakout_volume_filters:
                for adx in adx_filters:
                    for atr in atr_filters:
                        conditions = [*core, *mom, *vol, *adx, *atr]
                        rule_sets_short.append(("breakout", "SHORT breakout volume", conditions))

    pullback_near_ema = [
        [Condition("dist_close_ema21_pct", "abs_le", 0.35)],
        [Condition("dist_close_ema21_pct", "abs_le", 0.60)],
        [Condition("dist_close_ema50_pct", "abs_le", 0.50)],
        [Condition("dist_close_ema50_pct", "abs_le", 0.90)],
    ]

    long_pullback_cores = [
        [Condition("close", "gt_col", "ema200"), Condition("ema50_slope_5_pct", "gt", 0)],
        [Condition("long_trend_basic", "eq", 1)],
        [Condition("supertrend_up", "eq", 1), Condition("close", "gt_col", "ema50")],
    ]

    long_pullback_candles = [
        [],
        [Condition("lower_wick_to_range", "gt", 0.35)],
        [Condition("is_bullish_pinbar", "eq", 1)],
        [Condition("is_bullish_engulfing", "eq", 1)],
    ]

    long_pullback_rsi = [
        [Condition("rsi14", "between", 35, 58)],
        [Condition("rsi14", "between", 40, 60)],
        [Condition("rsi14_slope_3", "gt", 0)],
        [Condition("rsi14", "between", 35, 58), Condition("rsi14_slope_3", "gt", 0)],
    ]

    for core in long_pullback_cores:
        for near in pullback_near_ema:
            for rsi in long_pullback_rsi:
                for candle in long_pullback_candles:
                    for vol in volume_filters:
                        for atr in atr_filters:
                            conditions = [*core, *near, *rsi, *candle, *vol, *atr]
                            rule_sets_long.append(("pullback", "LONG pullback rebound", conditions))

    short_pullback_cores = [
        [Condition("close", "lt_col", "ema200"), Condition("ema50_slope_5_pct", "lt", 0)],
        [Condition("short_trend_basic", "eq", 1)],
        [Condition("supertrend_down", "eq", 1), Condition("close", "lt_col", "ema50")],
    ]

    short_pullback_candles = [
        [],
        [Condition("upper_wick_to_range", "gt", 0.35)],
        [Condition("is_bearish_pinbar", "eq", 1)],
        [Condition("is_bearish_engulfing", "eq", 1)],
    ]

    short_pullback_rsi = [
        [Condition("rsi14", "between", 42, 65)],
        [Condition("rsi14", "between", 40, 60)],
        [Condition("rsi14_slope_3", "lt", 0)],
        [Condition("rsi14", "between", 42, 65), Condition("rsi14_slope_3", "lt", 0)],
    ]

    for core in short_pullback_cores:
        for near in pullback_near_ema:
            for rsi in short_pullback_rsi:
                for candle in short_pullback_candles:
                    for vol in volume_filters:
                        for atr in atr_filters:
                            conditions = [*core, *near, *rsi, *candle, *vol, *atr]
                            rule_sets_short.append(("pullback", "SHORT pullback rebound", conditions))

    long_rsi_rebound_cores = [
        [Condition("rsi14_min_5", "lt", 35), Condition("rsi_cross_up_45", "eq", 1)],
        [Condition("rsi14_min_5", "lt", 40), Condition("rsi_cross_up_50", "eq", 1)],
        [Condition("rsi14_min_10", "lt", 40), Condition("rsi14", "gt", 50), Condition("rsi14_slope_3", "gt", 0)],
    ]

    long_context_filters = [
        [Condition("close", "gt_col", "ema200")],
        [Condition("ema50_slope_5_pct", "gt", 0)],
        [Condition("supertrend_up", "eq", 1)],
        [Condition("long_trend_basic", "eq", 1)],
    ]

    for core in long_rsi_rebound_cores:
        for context in long_context_filters:
            for vol in volume_filters:
                for atr in atr_filters:
                    conditions = [*core, *context, *vol, *atr]
                    rule_sets_long.append(("rsi_rebound", "LONG RSI rebound", conditions))

    short_rsi_rebound_cores = [
        [Condition("rsi14_max_5", "gt", 65), Condition("rsi_cross_down_55", "eq", 1)],
        [Condition("rsi14_max_5", "gt", 60), Condition("rsi_cross_down_50", "eq", 1)],
        [Condition("rsi14_max_10", "gt", 60), Condition("rsi14", "lt", 50), Condition("rsi14_slope_3", "lt", 0)],
    ]

    short_context_filters = [
        [Condition("close", "lt_col", "ema200")],
        [Condition("ema50_slope_5_pct", "lt", 0)],
        [Condition("supertrend_down", "eq", 1)],
        [Condition("short_trend_basic", "eq", 1)],
    ]

    for core in short_rsi_rebound_cores:
        for context in short_context_filters:
            for vol in volume_filters:
                for atr in atr_filters:
                    conditions = [*core, *context, *vol, *atr]
                    rule_sets_short.append(("rsi_rebound", "SHORT RSI rebound", conditions))

    rule_sets_long = dedupe_rule_sets(rule_sets_long)
    rule_sets_short = dedupe_rule_sets(rule_sets_short)

    if max_rules_per_side and max_rules_per_side > 0:
        rule_sets_long = rule_sets_long[:max_rules_per_side]
        rule_sets_short = rule_sets_short[:max_rules_per_side]

    rules: list[RuleSpec] = []

    if only_side in {"both", "long"}:
        for i, (family, desc, conditions) in enumerate(rule_sets_long, start=1):
            rules.append(
                RuleSpec(
                    rule_id=make_rule_id("long", family, conditions, i),
                    side="long",
                    family=family,
                    description=desc,
                    conditions=tuple(conditions),
                )
            )

    if only_side in {"both", "short"}:
        for i, (family, desc, conditions) in enumerate(rule_sets_short, start=1):
            rules.append(
                RuleSpec(
                    rule_id=make_rule_id("short", family, conditions, i),
                    side="short",
                    family=family,
                    description=desc,
                    conditions=tuple(conditions),
                )
            )

    return rules


def get_tp_parts(tp_pcts: list[float], tp_parts: list[float] | None) -> list[float]:
    if not tp_parts:
        return [1.0 / len(tp_pcts)] * len(tp_pcts)

    if len(tp_parts) != len(tp_pcts):
        raise ValueError("--tp-parts length must match --tp-pcts length")

    total = sum(tp_parts)

    if total <= 0:
        raise ValueError("TP parts sum must be positive")

    return [part / total for part in tp_parts]


def calc_price_from_profit_pct(entry_price: float, side: str, profit_pct: float) -> float:
    if side == "long":
        return entry_price * (1.0 + profit_pct / 100.0)

    return entry_price * (1.0 - profit_pct / 100.0)


def calc_trade_return_pct(entry_price: float, exit_price: float, side: str) -> float:
    if side == "long":
        return (exit_price - entry_price) / entry_price * 100.0

    return (entry_price - exit_price) / entry_price * 100.0


def close_position_part(
    *,
    entry_price: float,
    exit_price: float,
    side: str,
    part: float,
    margin_usd: float,
    leverage: float,
    fee_rate: float,
) -> tuple[float, float]:
    notional = margin_usd * leverage
    part_notional = notional * part

    ret_pct = calc_trade_return_pct(entry_price, exit_price, side)
    gross_pnl = part_notional * (ret_pct / 100.0)

    entry_fee = part_notional * fee_rate
    exit_notional = max(0.0, part_notional * (1.0 + ret_pct / 100.0))
    exit_fee = exit_notional * fee_rate

    net_pnl = gross_pnl - entry_fee - exit_fee

    return net_pnl, ret_pct


def simulate_exit_on_1m(
    *,
    side: str,
    entry_time_ms: int,
    entry_price: float,
    minute_times: np.ndarray,
    minute_high: np.ndarray,
    minute_low: np.ndarray,
    minute_close: np.ndarray,
    tp_pcts: list[float],
    tp_parts: list[float],
    sl_pct: float,
    sl_after_tp1_pct: float,
    sl_after_tp2_pct: float,
    sl_after_tp3_pct: float,
    max_hold_hours: float,
    margin_usd: float,
    leverage: float,
    fee_rate: float,
    intrabar_mode: str,
) -> dict[str, Any] | None:
    start_idx = int(np.searchsorted(minute_times, entry_time_ms, side="left"))

    if start_idx >= len(minute_times):
        return None

    end_time_ms = entry_time_ms + int(max_hold_hours * 60 * 60 * 1000)
    end_idx = int(np.searchsorted(minute_times, end_time_ms, side="right"))
    end_idx = min(end_idx, len(minute_times))

    if end_idx <= start_idx:
        return None

    tp_prices = [calc_price_from_profit_pct(entry_price, side, pct) for pct in tp_pcts]

    tp_hit = [0 for _ in tp_pcts]
    tp_times = ["" for _ in tp_pcts]

    tp_index = 0
    remaining_part = 1.0

    stop_profit_pct = -abs(sl_pct)

    pnl_usd = 0.0
    weighted_exit_return_pct = 0.0
    closed_part_total = 0.0

    max_favorable_pct = 0.0
    max_adverse_pct = 0.0

    exit_time_ms: int | None = None
    exit_price: float | None = None
    exit_reason = "UNKNOWN"

    def current_stop_price() -> float:
        return calc_price_from_profit_pct(entry_price, side, stop_profit_pct)

    def hit_tp(high_value: float, low_value: float, price: float) -> bool:
        if side == "long":
            return high_value >= price
        return low_value <= price

    def hit_stop(high_value: float, low_value: float, price: float) -> bool:
        if side == "long":
            return low_value <= price
        return high_value >= price

    def update_stop_after_tp(tp_number: int) -> None:
        nonlocal stop_profit_pct

        if tp_number == 1:
            stop_profit_pct = max(stop_profit_pct, sl_after_tp1_pct)
        elif tp_number == 2:
            stop_profit_pct = max(stop_profit_pct, sl_after_tp2_pct)
        elif tp_number == 3:
            stop_profit_pct = max(stop_profit_pct, sl_after_tp3_pct)

    def close_part(price: float, part: float) -> None:
        nonlocal pnl_usd, weighted_exit_return_pct, closed_part_total

        if part <= 0:
            return

        part_pnl, ret_pct = close_position_part(
            entry_price=entry_price,
            exit_price=price,
            side=side,
            part=part,
            margin_usd=margin_usd,
            leverage=leverage,
            fee_rate=fee_rate,
        )

        pnl_usd += part_pnl
        weighted_exit_return_pct += ret_pct * part
        closed_part_total += part

    for i in range(start_idx, end_idx):
        high_value = float(minute_high[i])
        low_value = float(minute_low[i])
        current_time = int(minute_times[i])

        if side == "long":
            favorable = (high_value - entry_price) / entry_price * 100.0
            adverse = (low_value - entry_price) / entry_price * 100.0
        else:
            favorable = (entry_price - low_value) / entry_price * 100.0
            adverse = (entry_price - high_value) / entry_price * 100.0

        max_favorable_pct = max(max_favorable_pct, favorable)
        max_adverse_pct = min(max_adverse_pct, adverse)

        stop_price = current_stop_price()
        stop_is_hit = hit_stop(high_value, low_value, stop_price)

        if intrabar_mode == "conservative" and stop_is_hit:
            close_part(stop_price, remaining_part)
            exit_time_ms = current_time
            exit_price = stop_price
            exit_reason = "SL" if tp_index == 0 else "TRAIL_SL"
            remaining_part = 0.0
            break

        while tp_index < len(tp_prices) and hit_tp(high_value, low_value, tp_prices[tp_index]):
            part = min(tp_parts[tp_index], remaining_part)
            close_part(tp_prices[tp_index], part)

            remaining_part -= part
            tp_hit[tp_index] = 1
            tp_times[tp_index] = ms_to_utc_str(current_time)

            tp_index += 1
            update_stop_after_tp(tp_index)

            if remaining_part <= 1e-12:
                exit_time_ms = current_time
                exit_price = tp_prices[tp_index - 1]
                exit_reason = f"TP{tp_index}"
                remaining_part = 0.0
                break

        if remaining_part <= 1e-12:
            break

        stop_price = current_stop_price()

        if hit_stop(high_value, low_value, stop_price):
            close_part(stop_price, remaining_part)
            exit_time_ms = current_time
            exit_price = stop_price
            exit_reason = "SL" if tp_index == 0 else "TRAIL_SL"
            remaining_part = 0.0
            break

    if remaining_part > 1e-12:
        last_i = end_idx - 1
        close_value = float(minute_close[last_i])
        close_part(close_value, remaining_part)

        exit_time_ms = int(minute_times[last_i])
        exit_price = close_value
        exit_reason = "TIME_EXIT"
        remaining_part = 0.0

    pnl_pct_on_margin = (pnl_usd / margin_usd * 100.0) if margin_usd > 0 else 0.0
    avg_exit_return_pct = weighted_exit_return_pct / closed_part_total if closed_part_total > 0 else 0.0

    return {
        "exit_time_ms": exit_time_ms,
        "exit_time": ms_to_utc_str(exit_time_ms),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_usd": pnl_usd,
        "pnl_pct_on_margin": pnl_pct_on_margin,
        "avg_exit_return_pct": avg_exit_return_pct,
        "max_favorable_pct": max_favorable_pct,
        "max_adverse_pct": max_adverse_pct,
        "tp1_hit": tp_hit[0] if len(tp_hit) > 0 else 0,
        "tp2_hit": tp_hit[1] if len(tp_hit) > 1 else 0,
        "tp3_hit": tp_hit[2] if len(tp_hit) > 2 else 0,
        "tp4_hit": tp_hit[3] if len(tp_hit) > 3 else 0,
        "tp1_time": tp_times[0] if len(tp_times) > 0 else "",
        "tp2_time": tp_times[1] if len(tp_times) > 1 else "",
        "tp3_time": tp_times[2] if len(tp_times) > 2 else "",
        "tp4_time": tp_times[3] if len(tp_times) > 3 else "",
        "any_tp_hit": int(any(tp_hit)),
        "sl_hit": int(exit_reason in {"SL", "TRAIL_SL"}),
        "is_win": int(any(tp_hit)),
    }


def init_stats(symbol: str, timeframe: str, rule: RuleSpec, start_deposit: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "rule_id": rule.rule_id,
        "side": rule.side,
        "family": rule.family,
        "description": rule.description,
        "conditions": " AND ".join(condition_to_text(c) for c in rule.conditions),

        "trades": 0,
        "wins": 0,
        "losses": 0,
        "tp1_hits": 0,
        "tp2_hits": 0,
        "tp3_hits": 0,
        "tp4_hits": 0,
        "sl_hits": 0,

        "gross_profit_usd": 0.0,
        "gross_loss_usd": 0.0,
        "total_pnl_usd": 0.0,
        "best_trade_usd": None,
        "worst_trade_usd": None,

        "start_deposit": start_deposit,
        "current_deposit": start_deposit,
        "peak_deposit": start_deposit,
        "max_drawdown_pct": 0.0,
    }


def update_stats(stats: dict[str, Any], trade: dict[str, Any]) -> None:
    pnl = safe_float(trade.get("pnl_usd"), 0.0)

    stats["trades"] += 1
    stats["wins"] += int(trade.get("is_win", 0))
    stats["losses"] += int(not trade.get("is_win", 0))

    stats["tp1_hits"] += int(trade.get("tp1_hit", 0))
    stats["tp2_hits"] += int(trade.get("tp2_hit", 0))
    stats["tp3_hits"] += int(trade.get("tp3_hit", 0))
    stats["tp4_hits"] += int(trade.get("tp4_hit", 0))
    stats["sl_hits"] += int(trade.get("sl_hit", 0))

    if pnl >= 0:
        stats["gross_profit_usd"] += pnl
    else:
        stats["gross_loss_usd"] += abs(pnl)

    stats["total_pnl_usd"] += pnl

    if stats["best_trade_usd"] is None or pnl > stats["best_trade_usd"]:
        stats["best_trade_usd"] = pnl

    if stats["worst_trade_usd"] is None or pnl < stats["worst_trade_usd"]:
        stats["worst_trade_usd"] = pnl

    stats["current_deposit"] += pnl
    stats["peak_deposit"] = max(stats["peak_deposit"], stats["current_deposit"])

    if stats["peak_deposit"] > 0:
        dd = (stats["peak_deposit"] - stats["current_deposit"]) / stats["peak_deposit"] * 100.0
        stats["max_drawdown_pct"] = max(stats["max_drawdown_pct"], dd)


def finalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    trades = int(stats["trades"])
    wins = int(stats["wins"])

    gross_profit = float(stats["gross_profit_usd"])
    gross_loss = float(stats["gross_loss_usd"])

    result = dict(stats)

    result["winrate"] = wins / trades * 100.0 if trades else 0.0
    result["profit_factor"] = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    result["avg_pnl_usd"] = float(stats["total_pnl_usd"]) / trades if trades else 0.0

    result["roi_pct"] = (
        (float(stats["current_deposit"]) - float(stats["start_deposit"]))
        / float(stats["start_deposit"])
        * 100.0
        if float(stats["start_deposit"]) > 0
        else 0.0
    )

    for name in ["tp1_hits", "tp2_hits", "tp3_hits", "tp4_hits", "sl_hits"]:
        result[f"{name}_rate"] = int(stats[name]) / trades * 100.0 if trades else 0.0

    return result


def write_rules_catalog(rules: list[RuleSpec], path: Path) -> None:
    with open_text_for_write(path) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["rule_id", "side", "family", "description", "conditions"],
        )
        writer.writeheader()

        for rule in rules:
            writer.writerow(
                {
                    "rule_id": rule.rule_id,
                    "side": rule.side,
                    "family": rule.family,
                    "description": rule.description,
                    "conditions": " AND ".join(condition_to_text(c) for c in rule.conditions),
                }
            )


def write_summary(summary_rows: list[dict[str, Any]], path: Path) -> None:
    if not summary_rows:
        return

    summary_rows = sorted(
        summary_rows,
        key=lambda r: (
            -safe_float(r.get("profit_factor"), 0.0),
            -safe_float(r.get("total_pnl_usd"), 0.0),
            -safe_float(r.get("winrate"), 0.0),
            -int(r.get("trades", 0)),
        ),
    )

    fieldnames = list(summary_rows[0].keys())

    with open_text_for_write(path) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in summary_rows:
            writer.writerow({k: to_csv_value(v) for k, v in row.items()})


def make_edge_report(trades_path: Path, output_path: Path, min_trades: int = 30) -> None:
    print(f"[EDGE] Reading trades for edge report: {trades_path}", flush=True)

    trades = pd.read_csv(trades_path, compression="infer")

    if trades.empty:
        print("[EDGE] No trades. Skip edge report.", flush=True)
        return

    if "pnl_usd" not in trades.columns:
        print("[EDGE] pnl_usd not found. Skip edge report.", flush=True)
        return

    trades["pnl_usd"] = pd.to_numeric(trades["pnl_usd"], errors="coerce").fillna(0.0)
    trades["is_win"] = pd.to_numeric(trades.get("is_win", 0), errors="coerce").fillna(0).astype(int)

    rows: list[dict[str, Any]] = []
    group_base_cols = ["side", "timeframe", "family"]

    for feature, bins in EDGE_BINS.items():
        if feature not in trades.columns:
            continue

        temp = trades.copy()
        temp[feature] = pd.to_numeric(temp[feature], errors="coerce")
        temp = temp.dropna(subset=[feature])

        if temp.empty:
            continue

        temp["feature_bin"] = pd.cut(
            temp[feature],
            bins=bins,
            include_lowest=True,
            duplicates="drop",
        ).astype(str)

        grouped = temp.groupby([*group_base_cols, "feature_bin"], dropna=False)

        for keys, group in grouped:
            if len(group) < min_trades:
                continue

            side, timeframe, family, feature_bin = keys

            pnl = group["pnl_usd"]
            gross_profit = pnl[pnl > 0].sum()
            gross_loss = abs(pnl[pnl < 0].sum())

            rows.append(
                {
                    "feature": feature,
                    "bin": feature_bin,
                    "side": side,
                    "timeframe": timeframe,
                    "family": family,
                    "trades": len(group),
                    "wins": int(group["is_win"].sum()),
                    "losses": int(len(group) - group["is_win"].sum()),
                    "winrate": group["is_win"].mean() * 100.0,
                    "total_pnl_usd": pnl.sum(),
                    "avg_pnl_usd": pnl.mean(),
                    "median_pnl_usd": pnl.median(),
                    "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
                    "gross_profit_usd": gross_profit,
                    "gross_loss_usd": gross_loss,
                }
            )

    if not rows:
        print("[EDGE] No edge rows after min_trades filter.", flush=True)
        return

    edge_df = pd.DataFrame(rows)
    edge_df = edge_df.sort_values(
        ["total_pnl_usd", "profit_factor", "winrate"],
        ascending=[True, True, True],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    edge_df.to_csv(output_path, index=False)

    print(f"[EDGE] Saved edge report: {output_path}", flush=True)


def run_backtest(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    results_dir = Path(args.results_dir)
    run_id = args.run_id or utc_run_id()

    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.symbols:
        symbols = sorted(set(s.upper() for s in args.symbols))
    else:
        symbols = discover_symbols(data_dir)

    if not symbols:
        raise RuntimeError(f"No symbols found in {data_dir}")

    rules = generate_rules(
        max_rules_per_side=args.max_rules_per_side,
        only_side=args.only_side,
    )

    if not rules:
        raise RuntimeError("No rules generated")

    trades_path = run_dir / "trades.csv.gz"
    summary_path = run_dir / "summary_by_rule.csv"
    rules_path = run_dir / "rules_catalog.csv"
    edge_path = run_dir / "edge_report.csv"

    write_rules_catalog(rules, rules_path)

    print("=" * 120, flush=True)
    print("MeowBot indicator rule grid backtest", flush=True)
    print(f"Run dir:              {run_dir}", flush=True)
    print(f"Symbols:              {symbols}", flush=True)
    print(f"Timeframes:           {args.timeframes}", flush=True)
    print(f"Rules:                {len(rules)}", flush=True)
    print(f"Max rules per side:   {args.max_rules_per_side}", flush=True)
    print(f"Max trades per rule:  {args.max_trades_per_rule}", flush=True)
    print(f"Edge filter profile:  {args.edge_filter_profile}", flush=True)
    print(f"Start deposit/rule:   {args.deposit}", flush=True)
    print(f"Margin per trade:     {args.margin_pct}%", flush=True)
    print(f"Leverage:             {args.leverage}x", flush=True)
    print(f"TP pcts:              {args.tp_pcts}", flush=True)
    print(f"TP parts:             {args.tp_parts}", flush=True)
    print(f"SL pct:               {args.sl_pct}", flush=True)
    print(f"SL after TP1:         {args.sl_after_tp1_pct}%", flush=True)
    print(f"SL after TP2:         {args.sl_after_tp2_pct}%", flush=True)
    print(f"SL after TP3:         {args.sl_after_tp3_pct}%", flush=True)
    print(f"Max hold hours:       {args.max_hold_hours}", flush=True)
    print(f"Intrabar mode:        {args.intrabar_mode}", flush=True)
    print("=" * 120, flush=True)

    tp_pcts = [float(x) for x in args.tp_pcts]
    tp_parts = get_tp_parts(tp_pcts, args.tp_parts)

    trade_fieldnames = [
        "run_id",
        "symbol",
        "timeframe",
        "rule_id",
        "side",
        "family",
        "description",
        "conditions",

        "signal_time_ms",
        "signal_time",
        "entry_time_ms",
        "entry_time",
        "entry_price",

        "exit_time_ms",
        "exit_time",
        "exit_price",
        "exit_reason",

        "deposit_before",
        "deposit_after",
        "margin_usd",
        "leverage",
        "fee_rate",

        "pnl_usd",
        "pnl_pct_on_margin",
        "avg_exit_return_pct",
        "max_favorable_pct",
        "max_adverse_pct",

        "tp1_hit",
        "tp2_hit",
        "tp3_hit",
        "tp4_hit",
        "tp1_time",
        "tp2_time",
        "tp3_time",
        "tp4_time",
        "any_tp_hit",
        "sl_hit",
        "is_win",

        *ENTRY_FEATURE_COLUMNS,
    ]

    stats_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    total_trades_written = 0

    with open_text_for_write(trades_path) as f:
        writer = csv.DictWriter(f, fieldnames=trade_fieldnames)
        writer.writeheader()

        for symbol in symbols:
            one_minute_path = find_1m_file(data_dir, symbol)

            if one_minute_path is None:
                print(f"[SKIP] {symbol}: 1m file not found", flush=True)
                continue

            print(f"[READ 1m] {symbol}: {one_minute_path}", flush=True)
            minute_df = normalize_price_df(read_csv_any(one_minute_path))

            minute_times = minute_df["open_time"].to_numpy(dtype=np.int64)
            minute_high = minute_df["high"].to_numpy(dtype=float)
            minute_low = minute_df["low"].to_numpy(dtype=float)
            minute_close = minute_df["close"].to_numpy(dtype=float)

            for timeframe in args.timeframes:
                indicator_path = find_indicator_file(data_dir, symbol, timeframe)

                if indicator_path is None:
                    print(f"[SKIP] {symbol} {timeframe}: indicator file not found", flush=True)
                    continue

                print(f"[READ IND] {symbol} {timeframe}: {indicator_path}", flush=True)
                entry_df = prepare_entry_df(read_csv_any(indicator_path))

                if entry_df.empty:
                    print(f"[SKIP] {symbol} {timeframe}: empty indicator df", flush=True)
                    continue

                print(f"[BT] {symbol} {timeframe}: rows={len(entry_df):,}, rules={len(rules):,}", flush=True)

                open_times = entry_df["open_time"].to_numpy(dtype=np.int64)
                open_prices = entry_df["open"].to_numpy(dtype=float)

                edge_masks_by_side = {
                    "long": evaluate_edge_filter(
                        entry_df,
                        side="long",
                        profile=args.edge_filter_profile,
                    ),
                    "short": evaluate_edge_filter(
                        entry_df,
                        side="short",
                        profile=args.edge_filter_profile,
                    ),
                }

                if args.edge_filter_profile != "none":
                    long_pass = int(edge_masks_by_side["long"].sum())
                    short_pass = int(edge_masks_by_side["short"].sum())
                    total_rows = len(entry_df)

                    print(
                        f"[EDGE FILTER] {symbol} {timeframe}: "
                        f"profile={args.edge_filter_profile}, "
                        f"long_pass={long_pass:,}/{total_rows:,}, "
                        f"short_pass={short_pass:,}/{total_rows:,}",
                        flush=True,
                    )

                exit_cache: dict[tuple[str, int, float], dict[str, Any]] = {}
                exit_cache_hits = 0
                exit_cache_misses = 0

                for rule_i, rule in enumerate(rules, start=1):
                    if rule_i % 50 == 0:
                        cache_total = exit_cache_hits + exit_cache_misses
                        cache_hit_rate = (exit_cache_hits / cache_total * 100.0) if cache_total > 0 else 0.0

                        print(
                            f"[PROGRESS] {symbol} {timeframe}: "
                            f"rule {rule_i:,}/{len(rules):,}, "
                            f"trades={total_trades_written:,}, "
                            f"exit_cache={len(exit_cache):,}, "
                            f"hit_rate={cache_hit_rate:.1f}%",
                            flush=True,
                        )

                    signal_mask = evaluate_rule(entry_df, rule)

                    edge_mask = edge_masks_by_side.get(
                        rule.side,
                        pd.Series(False, index=entry_df.index),
                    )

                    signal_mask = signal_mask & edge_mask
                    signal_indices = np.flatnonzero(signal_mask.to_numpy(dtype=bool))

                    if len(signal_indices) == 0:
                        continue

                    key = (symbol, timeframe, rule.rule_id)

                    if key not in stats_by_key:
                        stats_by_key[key] = init_stats(
                            symbol=symbol,
                            timeframe=timeframe,
                            rule=rule,
                            start_deposit=args.deposit,
                        )

                    stats = stats_by_key[key]
                    last_exit_time_ms = -1
                    trades_for_rule = 0

                    for signal_idx in signal_indices:
                        entry_idx = signal_idx + args.entry_delay_bars

                        if entry_idx >= len(entry_df):
                            continue

                        signal_time_ms = int(open_times[signal_idx])
                        entry_time_ms = int(open_times[entry_idx])

                        if entry_time_ms <= last_exit_time_ms:
                            continue

                        deposit_before = float(stats["current_deposit"])

                        if deposit_before <= 0:
                            break

                        margin_usd = deposit_before * (args.margin_pct / 100.0)

                        if margin_usd <= 0:
                            continue

                        entry_price = float(open_prices[entry_idx])

                        if entry_price <= 0:
                            continue

                        cache_key = (
                            rule.side,
                            entry_time_ms,
                            round(entry_price, 12),
                        )

                        cached_exit_result = exit_cache.get(cache_key)

                        if cached_exit_result is None:
                            cached_exit_result = simulate_exit_on_1m(
                                side=rule.side,
                                entry_time_ms=entry_time_ms,
                                entry_price=entry_price,
                                minute_times=minute_times,
                                minute_high=minute_high,
                                minute_low=minute_low,
                                minute_close=minute_close,
                                tp_pcts=tp_pcts,
                                tp_parts=tp_parts,
                                sl_pct=args.sl_pct,
                                sl_after_tp1_pct=args.sl_after_tp1_pct,
                                sl_after_tp2_pct=args.sl_after_tp2_pct,
                                sl_after_tp3_pct=args.sl_after_tp3_pct,
                                max_hold_hours=args.max_hold_hours,
                                margin_usd=1.0,
                                leverage=args.leverage,
                                fee_rate=args.fee_rate,
                                intrabar_mode=args.intrabar_mode,
                            )

                            exit_cache_misses += 1

                            if cached_exit_result is not None:
                                exit_cache[cache_key] = cached_exit_result
                        else:
                            exit_cache_hits += 1

                        if cached_exit_result is None:
                            continue

                        exit_result = dict(cached_exit_result)

                        pnl_per_1_margin = safe_float(exit_result["pnl_usd"], 0.0)
                        exit_result["pnl_usd"] = pnl_per_1_margin * margin_usd
                        exit_result["pnl_pct_on_margin"] = pnl_per_1_margin * 100.0

                        last_exit_time_ms = int(exit_result["exit_time_ms"])
                        signal_row = entry_df.iloc[signal_idx]

                        trade_row: dict[str, Any] = {
                            "run_id": run_id,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "rule_id": rule.rule_id,
                            "side": rule.side,
                            "family": rule.family,
                            "description": rule.description,
                            "conditions": " AND ".join(condition_to_text(c) for c in rule.conditions),

                            "signal_time_ms": signal_time_ms,
                            "signal_time": ms_to_utc_str(signal_time_ms),
                            "entry_time_ms": entry_time_ms,
                            "entry_time": ms_to_utc_str(entry_time_ms),
                            "entry_price": entry_price,

                            "deposit_before": deposit_before,
                            "margin_usd": margin_usd,
                            "leverage": args.leverage,
                            "fee_rate": args.fee_rate,

                            **exit_result,
                        }

                        deposit_after = deposit_before + float(exit_result["pnl_usd"])
                        trade_row["deposit_after"] = deposit_after

                        for feature in ENTRY_FEATURE_COLUMNS:
                            trade_row[feature] = signal_row[feature] if feature in signal_row.index else ""

                        writer.writerow({k: to_csv_value(trade_row.get(k, "")) for k in trade_fieldnames})

                        update_stats(stats, trade_row)

                        total_trades_written += 1
                        trades_for_rule += 1

                        if args.max_trades_per_rule > 0 and trades_for_rule >= args.max_trades_per_rule:
                            break

    summary_rows = [
        finalize_stats(stats)
        for stats in stats_by_key.values()
        if int(stats.get("trades", 0)) >= args.min_trades_summary
    ]

    write_summary(summary_rows, summary_path)

    if args.make_edge_report:
        make_edge_report(
            trades_path=trades_path,
            output_path=edge_path,
            min_trades=args.min_trades_edge,
        )

    print("=" * 120, flush=True)
    print("Backtest finished", flush=True)
    print(f"Trades:        {total_trades_written:,}", flush=True)
    print(f"Trades file:   {trades_path}", flush=True)
    print(f"Summary file:  {summary_path}", flush=True)
    print(f"Rules catalog: {rules_path}", flush=True)

    if args.make_edge_report:
        print(f"Edge report:   {edge_path}", flush=True)

    print("=" * 120, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mass backtest simple indicator rules for MeowBot."
    )

    parser.add_argument("--data-dir", type=str, default="test/data")
    parser.add_argument("--results-dir", type=str, default="test/results/indicator_rule_grid")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES)

    parser.add_argument(
        "--only-side",
        choices=["both", "long", "short"],
        default="both",
    )

    parser.add_argument(
        "--edge-filter-profile",
        choices=["none", "v1", "v1_taker"],
        default="none",
        help="Additional edge filters applied after rule signal. Default: none.",
    )

    parser.add_argument(
        "--max-rules-per-side",
        type=int,
        default=800,
        help="0 = generate all rules. Default: 800 per side.",
    )

    parser.add_argument(
        "--max-trades-per-rule",
        type=int,
        default=0,
        help="0 = no limit.",
    )

    parser.add_argument("--min-trades-summary", type=int, default=20)

    parser.add_argument(
        "--entry-delay-bars",
        type=int,
        default=1,
        help="1 = enter on next candle open after signal.",
    )

    parser.add_argument("--deposit", type=float, default=100.0)

    parser.add_argument(
        "--margin-pct",
        type=float,
        default=1.0,
        help="Margin percent of current rule deposit per trade.",
    )

    parser.add_argument("--leverage", type=float, default=20.0)

    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.0005,
        help="Fee rate per side. 0.0005 = 0.05%.",
    )

    parser.add_argument(
        "--tp-pcts",
        nargs="+",
        type=float,
        default=[1.0, 2.0, 3.0, 4.0],
    )

    parser.add_argument(
        "--tp-parts",
        nargs="+",
        type=float,
        default=[0.25, 0.25, 0.25, 0.25],
    )

    parser.add_argument("--sl-pct", type=float, default=2.0)

    parser.add_argument(
        "--sl-after-tp1-pct",
        type=float,
        default=0.0,
        help="After TP1 move SL to this profit pct. Default: 0.",
    )

    parser.add_argument(
        "--sl-after-tp2-pct",
        type=float,
        default=0.5,
        help="After TP2 move SL to this profit pct.",
    )

    parser.add_argument(
        "--sl-after-tp3-pct",
        type=float,
        default=1.0,
        help="After TP3 move SL to this profit pct.",
    )

    parser.add_argument(
        "--max-hold-hours",
        type=float,
        default=168.0,
        help="Time exit if trade is still open. Default: 168h = 7 days.",
    )

    parser.add_argument(
        "--intrabar-mode",
        choices=["conservative", "tp_first"],
        default="conservative",
        help="If TP and SL happen inside same 1m candle.",
    )

    parser.add_argument("--make-edge-report", action="store_true")
    parser.add_argument("--min-trades-edge", type=int, default=30)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_backtest(args)


if __name__ == "__main__":
    main()