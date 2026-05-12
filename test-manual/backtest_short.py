from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm


# =========================
# CONFIG
# =========================

DATA_DIR = Path("test-manual/data")
INDICATORS_DIR = Path("test-manual/indicators")
RESULTS_DIR = Path("test-manual/results")

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "DOTUSDT",
]

ENTRY_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h"]

ENTRY_TO_HTF = {
    "15m": "1h",
    "30m": "2h",
    "1h": "4h",
    "2h": "4h",
    "4h": "1d",
}

BASE_STRATEGIES = [
    "short_s1_breakdown_base",
    "short_s2_breakdown_score4",
    "short_s3_rejection_base",
    "short_s4_rejection_score4",
    "short_s5_pullback_base",
    "short_s6_pullback_score4",
    "short_s7_breakdown_htf_score4",
    "short_s8_pullback_htf_score4",
    "short_s9_breakdown_score4_htf_soft",
    "short_s10_pullback_score4_htf_soft",
]

TP_PROFILES = {
    "tp06": [0.006, 0.012, 0.018, 0.024],
    "tp08": [0.008, 0.016, 0.024, 0.032],
    "tp10": [0.010, 0.020, 0.030, 0.040],
}

STRATEGY_MODES = [
    f"{base}_{tp_name}"
    for base in BASE_STRATEGIES
    for tp_name in TP_PROFILES.keys()
]

INITIAL_DEPOSIT = 1000.0
ENTRY_DEPOSIT_PCT = 0.01
LEVERAGE = 1.0
FEE_RATE = 0.0004
SLIPPAGE_PCT = 0.0

ONE_OPEN_TRADE_PER_SYMBOL = True
MAX_OPEN_TRADES: int | None = None
MAX_SIGNALS_PER_FILE: int | None = None

# У цьому тесті position не змінюємо.
FIXED_POSITION_MULTIPLIER = 1.0

RSI14_COL = "rsi_14"
ATR14_PCT_COL = "atr_14_pct"
DIST_EMA50_COL = "dist_to_ema_50_pct"
VOLUME_RATIO20_COL = "volume_ratio_sma_20"
ADX14_COL = "adx_14"
CLOSE_POS_COL = "close_position_in_candle"
VOL_PEAK_OFFSET_COL = "vol_peak_offset_10"

BREAKDOWN_SCORE_COL = "short_breakdown_score"
REJECTION_SCORE_COL = "short_rejection_score"
PULLBACK_SCORE_COL = "short_pullback_score"

HTF_PREFIX = "htf_"

TP_PARTS = [0.25, 0.25, 0.25, 0.25]
STOP_LOSS_PCT = 0.020
BE_AFTER_TP_HITS = 1


# =========================
# HELPERS
# =========================

def ensure_results_dir() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def ms_to_dt(ms: int | float | None) -> str | None:
    if ms is None or pd.isna(ms):
        return None
    return pd.to_datetime(int(ms), unit="ms", utc=True).isoformat()


def calculate_close_position_in_candle(df: pd.DataFrame) -> pd.Series:
    candle_range = df["high"] - df["low"]
    values = np.where(
        candle_range.abs() > 1e-12,
        (df["close"] - df["low"]) / candle_range,
        np.nan,
    )
    return pd.Series(values, index=df.index)


def calculate_vol_peak_offset_10(volume_ratio: pd.Series) -> pd.Series:
    def peak_offset(arr: np.ndarray) -> float:
        if np.isnan(arr).any():
            return np.nan
        return float(int(np.argmax(arr)) - 10)

    return volume_ratio.rolling(window=11, min_periods=11).apply(
        peak_offset,
        raw=True,
    )


def parse_strategy_mode(strategy_mode: str) -> tuple[str, str]:
    for tp_name in TP_PROFILES.keys():
        suffix = f"_{tp_name}"
        if strategy_mode.endswith(suffix):
            return strategy_mode[: -len(suffix)], tp_name

    raise ValueError(f"Cannot parse TP profile from strategy_mode: {strategy_mode}")


# =========================
# SHORT BASE MASKS
# =========================

def build_short_breakdown_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    valid = (
        rsi14.notna()
        & atr14_pct.notna()
        & dist_ema50.notna()
        & volume_ratio.notna()
        & adx14.notna()
        & close_pos.notna()
        & vol_peak_offset.notna()
    )

    # SHORT BREAKDOWN V2:
    # жорсткіший breakdown: менше шуму, сильніше закриття вниз,
    # але всі стратегії/TP/таймфрейми залишаються в тесті.
    return (
        valid
        & (rsi14 <= 42)
        & (dist_ema50 <= -1.2)
        & (dist_ema50 >= -3.5)
        & (volume_ratio >= 1.7)
        & (atr14_pct >= 0.4)
        & (atr14_pct <= 1.0)
        & (vol_peak_offset >= -3)
        & (close_pos <= 0.50)
        & (adx14 >= 22)
    ).fillna(False)


def build_short_rejection_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]

    valid = (
        rsi14.notna()
        & atr14_pct.notna()
        & dist_ema50.notna()
        & volume_ratio.notna()
        & adx14.notna()
        & close_pos.notna()
    )

    # SHORT REJECTION V2:
    # тепер це справжній rejection: ціна була високо,
    # але свічка закрилась слабко.
    return (
        valid
        & (rsi14 >= 62)
        & (rsi14 <= 72)
        & (dist_ema50 >= 1.5)
        & (dist_ema50 <= 4.0)
        & (volume_ratio >= 1.5)
        & (atr14_pct >= 0.4)
        & (atr14_pct <= 1.0)
        & (close_pos <= 0.45)
        & (adx14 >= 20)
    ).fillna(False)


def build_short_pullback_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]

    valid = (
        rsi14.notna()
        & atr14_pct.notna()
        & dist_ema50.notna()
        & volume_ratio.notna()
        & adx14.notna()
        & close_pos.notna()
    )

    # SHORT PULLBACK V2:
    # звужений pullback: відскок у зону EMA, але без сильного bullish-закриття.
    return (
        valid
        & (rsi14 >= 48)
        & (rsi14 <= 58)
        & (dist_ema50 >= -0.2)
        & (dist_ema50 <= 1.2)
        & (volume_ratio >= 1.5)
        & (atr14_pct >= 0.35)
        & (atr14_pct <= 0.9)
        & (close_pos <= 0.45)
        & (adx14 >= 20)
    ).fillna(False)


# =========================
# SHORT SCORES
# =========================

def calculate_short_breakdown_score(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    score = pd.Series(0, index=df.index, dtype="int64")

    score += (rsi14 <= 40).astype("int64")
    score += ((rsi14 >= 30) & (rsi14 <= 42)).astype("int64")

    score += (dist_ema50 <= -1.5).astype("int64")
    score += (dist_ema50 >= -3.5).astype("int64")

    score += (volume_ratio >= 2.0).astype("int64")
    score += (volume_ratio >= 2.5).astype("int64")

    score += (vol_peak_offset >= -2).astype("int64")
    score += (close_pos <= 0.45).astype("int64")
    score += (adx14 >= 25).astype("int64")

    return score


def calculate_short_rejection_score(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    atr14_pct = df[ATR14_PCT_COL]

    score = pd.Series(0, index=df.index, dtype="int64")

    score += ((rsi14 >= 60) & (rsi14 <= 72)).astype("int64")
    score += ((rsi14 >= 62) & (rsi14 <= 70)).astype("int64")

    score += ((dist_ema50 >= 1.5) & (dist_ema50 <= 4.0)).astype("int64")
    score += ((dist_ema50 >= 2.0) & (dist_ema50 <= 3.5)).astype("int64")

    score += (volume_ratio >= 1.5).astype("int64")
    score += (volume_ratio >= 2.0).astype("int64")

    score += (close_pos <= 0.50).astype("int64")
    score += (adx14 >= 22).astype("int64")
    score += ((atr14_pct >= 0.4) & (atr14_pct <= 1.0)).astype("int64")

    return score


def calculate_short_pullback_score(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    dist_ema50 = df[DIST_EMA50_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    atr14_pct = df[ATR14_PCT_COL]

    score = pd.Series(0, index=df.index, dtype="int64")

    score += ((rsi14 >= 48) & (rsi14 <= 58)).astype("int64")
    score += ((rsi14 >= 50) & (rsi14 <= 56)).astype("int64")

    score += ((dist_ema50 >= -0.2) & (dist_ema50 <= 1.2)).astype("int64")
    score += ((dist_ema50 >= 0.0) & (dist_ema50 <= 1.0)).astype("int64")

    score += (volume_ratio >= 1.5).astype("int64")
    score += (volume_ratio >= 2.0).astype("int64")

    score += (close_pos <= 0.45).astype("int64")
    score += (adx14 >= 22).astype("int64")
    score += ((atr14_pct >= 0.35) & (atr14_pct <= 0.9)).astype("int64")

    return score


# =========================
# DATA LOADERS
# =========================

def load_1m_data(symbol: str) -> dict[str, np.ndarray] | None:
    path = DATA_DIR / symbol / f"{symbol}_1m.csv.gz"

    if not path.exists():
        print(f"SKIP {symbol}: missing 1m data: {path}")
        return None

    df = pd.read_csv(path, usecols=["open_time", "open", "high", "low", "close"])
    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"]).reset_index(drop=True)

    for col in ["open_time", "open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open_time", "open", "high", "low", "close"])

    return {
        "open_time": df["open_time"].astype("int64").to_numpy(),
        "open": df["open"].astype("float64").to_numpy(),
        "high": df["high"].astype("float64").to_numpy(),
        "low": df["low"].astype("float64").to_numpy(),
        "close": df["close"].astype("float64").to_numpy(),
    }


def load_raw_indicator_file(symbol: str, timeframe: str) -> pd.DataFrame | None:
    path = INDICATORS_DIR / symbol / f"{symbol}_{timeframe}_indicators.csv.gz"

    if not path.exists():
        print(f"SKIP missing indicators: {path}")
        return None

    df = pd.read_csv(path)

    required = [
        "datetime",
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        RSI14_COL,
        ATR14_PCT_COL,
        DIST_EMA50_COL,
        VOLUME_RATIO20_COL,
        ADX14_COL,
    ]

    missing = [col for col in required if col not in df.columns]
    if missing:
        print(f"SKIP {symbol} {timeframe}: missing columns: {missing}")
        return None

    df = df.copy()
    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"]).reset_index(drop=True)

    numeric_cols = [
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        RSI14_COL,
        ATR14_PCT_COL,
        DIST_EMA50_COL,
        VOLUME_RATIO20_COL,
        ADX14_COL,
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if CLOSE_POS_COL in df.columns:
        df[CLOSE_POS_COL] = pd.to_numeric(df[CLOSE_POS_COL], errors="coerce")
    else:
        df[CLOSE_POS_COL] = calculate_close_position_in_candle(df)

    df[VOL_PEAK_OFFSET_COL] = calculate_vol_peak_offset_10(df[VOLUME_RATIO20_COL])

    return df


def load_htf_indicator_file(symbol: str, htf: str) -> pd.DataFrame | None:
    df = load_raw_indicator_file(symbol, htf)

    if df is None or df.empty:
        return None

    htf_df = df[
        [
            "close_time",
            "close",
            DIST_EMA50_COL,
            RSI14_COL,
            ADX14_COL,
            ATR14_PCT_COL,
            CLOSE_POS_COL,
        ]
    ].copy()

    htf_df = htf_df.rename(
        columns={
            "close_time": f"{HTF_PREFIX}close_time",
            "close": f"{HTF_PREFIX}close",
            DIST_EMA50_COL: f"{HTF_PREFIX}{DIST_EMA50_COL}",
            RSI14_COL: f"{HTF_PREFIX}{RSI14_COL}",
            ADX14_COL: f"{HTF_PREFIX}{ADX14_COL}",
            ATR14_PCT_COL: f"{HTF_PREFIX}{ATR14_PCT_COL}",
            CLOSE_POS_COL: f"{HTF_PREFIX}{CLOSE_POS_COL}",
        }
    )

    htf_df = htf_df.sort_values(f"{HTF_PREFIX}close_time").reset_index(drop=True)

    return htf_df


def attach_htf_columns(
    entry_df: pd.DataFrame,
    symbol: str,
    entry_timeframe: str,
) -> pd.DataFrame:
    htf = ENTRY_TO_HTF.get(entry_timeframe)

    entry_df = entry_df.copy()
    entry_df["htf_timeframe"] = htf or ""
    entry_df["has_htf"] = False

    if not htf:
        return entry_df

    htf_df = load_htf_indicator_file(symbol, htf)

    if htf_df is None or htf_df.empty:
        return entry_df

    left = entry_df.sort_values("close_time").reset_index(drop=False).rename(columns={"index": "_orig_index"})

    merged = pd.merge_asof(
        left,
        htf_df,
        left_on="close_time",
        right_on=f"{HTF_PREFIX}close_time",
        direction="backward",
        allow_exact_matches=True,
    )

    merged = merged.sort_values("_orig_index").drop(columns=["_orig_index"]).reset_index(drop=True)
    merged["has_htf"] = merged[f"{HTF_PREFIX}close_time"].notna()

    return merged


def load_indicator_file(symbol: str, timeframe: str) -> pd.DataFrame | None:
    df = load_raw_indicator_file(symbol, timeframe)

    if df is None or df.empty:
        return None

    df = attach_htf_columns(df, symbol=symbol, entry_timeframe=timeframe)

    df["short_breakdown_mask"] = build_short_breakdown_mask(df)
    df["short_rejection_mask"] = build_short_rejection_mask(df)
    df["short_pullback_mask"] = build_short_pullback_mask(df)

    df[BREAKDOWN_SCORE_COL] = calculate_short_breakdown_score(df)
    df[REJECTION_SCORE_COL] = calculate_short_rejection_score(df)
    df[PULLBACK_SCORE_COL] = calculate_short_pullback_score(df)

    return df


# =========================
# STRATEGY LOGIC
# =========================

def htf_bearish_ok(df: pd.DataFrame) -> pd.Series:
    ema_col = f"{HTF_PREFIX}{DIST_EMA50_COL}"
    rsi_col = f"{HTF_PREFIX}{RSI14_COL}"
    adx_col = f"{HTF_PREFIX}{ADX14_COL}"

    if ema_col not in df.columns or rsi_col not in df.columns or adx_col not in df.columns:
        return pd.Series(False, index=df.index)

    return (
        df["has_htf"]
        & df[ema_col].notna()
        & df[rsi_col].notna()
        & df[adx_col].notna()
        & (df[ema_col] < 0)
        & (df[rsi_col] <= 55)
        & (df[adx_col] >= 18)
    ).fillna(False)


def htf_bearish_soft_ok(df: pd.DataFrame) -> pd.Series:
    ema_col = f"{HTF_PREFIX}{DIST_EMA50_COL}"
    rsi_col = f"{HTF_PREFIX}{RSI14_COL}"

    if ema_col not in df.columns or rsi_col not in df.columns:
        return pd.Series(False, index=df.index)

    return (
        df["has_htf"]
        & df[ema_col].notna()
        & df[rsi_col].notna()
        & (df[ema_col] < 0)
        & (df[rsi_col] <= 58)
    ).fillna(False)


def get_strategy_family(base_strategy: str) -> str:
    if "breakdown" in base_strategy:
        return "breakdown"

    if "rejection" in base_strategy:
        return "rejection"

    if "pullback" in base_strategy:
        return "pullback"

    raise ValueError(f"Cannot detect family from base_strategy: {base_strategy}")


def get_family_mask(df: pd.DataFrame, family: str) -> pd.Series:
    if family == "breakdown":
        return df["short_breakdown_mask"]

    if family == "rejection":
        return df["short_rejection_mask"]

    if family == "pullback":
        return df["short_pullback_mask"]

    raise ValueError(f"Unknown family: {family}")


def get_family_score(df: pd.DataFrame, family: str) -> pd.Series:
    if family == "breakdown":
        return df[BREAKDOWN_SCORE_COL]

    if family == "rejection":
        return df[REJECTION_SCORE_COL]

    if family == "pullback":
        return df[PULLBACK_SCORE_COL]

    raise ValueError(f"Unknown family: {family}")


def build_strategy_mask(df: pd.DataFrame, strategy_mode: str) -> pd.Series:
    base_strategy, _tp_profile = parse_strategy_mode(strategy_mode)
    family = get_strategy_family(base_strategy)

    base_mask = get_family_mask(df, family)
    score = get_family_score(df, family)

    if base_strategy in {
        "short_s1_breakdown_base",
        "short_s3_rejection_base",
        "short_s5_pullback_base",
    }:
        return base_mask

    if base_strategy in {
        "short_s2_breakdown_score4",
        "short_s4_rejection_score4",
        "short_s6_pullback_score4",
    }:
        return base_mask & (score >= 4)

    if base_strategy in {
        "short_s7_breakdown_htf_score4",
        "short_s8_pullback_htf_score4",
    }:
        return base_mask & (score >= 4) & htf_bearish_ok(df)

    if base_strategy in {
        "short_s9_breakdown_score4_htf_soft",
        "short_s10_pullback_score4_htf_soft",
    }:
        return base_mask & (score >= 4) & htf_bearish_soft_ok(df)

    raise ValueError(f"Unknown base_strategy: {base_strategy}")


def get_signal_strength(score: int) -> str:
    if score >= 6:
        return "strong"

    if score >= 5:
        return "medium"

    return "weak"


# =========================
# EXIT SIMULATION — SHORT
# =========================

def find_entry_index_1m(
    one_min: dict[str, np.ndarray],
    signal_close_time_ms: int,
) -> int | None:
    open_times = one_min["open_time"]
    idx = int(np.searchsorted(open_times, signal_close_time_ms + 1, side="left"))

    if idx >= len(open_times):
        return None

    return idx


def stop_reason_short(current_sl: float, entry_price: float, tp_hits: int) -> str:
    if tp_hits == 0 and current_sl > entry_price:
        return "SL"

    if abs(current_sl - entry_price) / entry_price < 1e-10:
        if tp_hits == 0:
            return "BE"
        return f"TP{tp_hits}_THEN_BE"

    return f"TP{tp_hits}_THEN_STOP"


def simulate_exit_short_base(
    one_min: dict[str, np.ndarray],
    entry_idx: int,
    entry_price: float,
    tp_levels: list[float],
) -> dict[str, Any]:
    open_times = one_min["open_time"]
    highs = one_min["high"]
    lows = one_min["low"]
    closes = one_min["close"]

    tp_prices = [entry_price * (1.0 - pct) for pct in tp_levels]
    current_sl = entry_price * (1.0 + STOP_LOSS_PCT)

    remaining = 1.0
    tp_hits = 0
    realized_price_pnl_decimal = 0.0
    closed_parts: list[str] = []

    for i in range(entry_idx, len(open_times)):
        current_time_ms = int(open_times[i])
        high = float(highs[i])
        low = float(lows[i])

        # Conservative: if SL and TP are both inside one 1m candle, SL is counted first.
        if high >= current_sl:
            realized_price_pnl_decimal += remaining * ((entry_price - current_sl) / entry_price)
            reason = stop_reason_short(current_sl, entry_price, tp_hits)
            closed_parts.append(f"{reason}:{remaining:.2f}@{current_sl:.8f}")

            return {
                "exit_time_ms": current_time_ms,
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                "closed_parts": "|".join(closed_parts),
            }

        while tp_hits < len(tp_prices) and low <= tp_prices[tp_hits]:
            part = float(TP_PARTS[tp_hits])
            tp_price = float(tp_prices[tp_hits])

            realized_price_pnl_decimal += part * ((entry_price - tp_price) / entry_price)
            remaining -= part
            tp_hits += 1

            closed_parts.append(f"TP{tp_hits}:{part:.2f}@{tp_price:.8f}")

            if tp_hits >= BE_AFTER_TP_HITS:
                current_sl = min(current_sl, entry_price)

            if remaining <= 1e-12:
                return {
                    "exit_time_ms": current_time_ms,
                    "exit_price": float(tp_price),
                    "exit_reason": "TP_ALL",
                    "tp_hits": tp_hits,
                    "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                    "closed_parts": "|".join(closed_parts),
                }

        if tp_hits >= BE_AFTER_TP_HITS and high >= current_sl:
            realized_price_pnl_decimal += remaining * ((entry_price - current_sl) / entry_price)
            reason = f"TP{tp_hits}_THEN_BE"
            closed_parts.append(f"{reason}:{remaining:.2f}@{current_sl:.8f}")

            return {
                "exit_time_ms": current_time_ms,
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                "closed_parts": "|".join(closed_parts),
            }

    last_close = float(closes[-1])
    realized_price_pnl_decimal += remaining * ((entry_price - last_close) / entry_price)
    closed_parts.append(f"END:{remaining:.2f}@{last_close:.8f}")

    return {
        "exit_time_ms": int(open_times[-1]),
        "exit_price": float(last_close),
        "exit_reason": "END_OF_DATA",
        "tp_hits": tp_hits,
        "weighted_price_pnl_decimal": realized_price_pnl_decimal,
        "closed_parts": "|".join(closed_parts),
    }


def simulate_trade_exit(
    one_min: dict[str, np.ndarray],
    signal_close_time_ms: int,
    tp_levels: list[float],
) -> dict[str, Any] | None:
    entry_idx = find_entry_index_1m(one_min, signal_close_time_ms)

    if entry_idx is None:
        return None

    entry_time_ms = int(one_min["open_time"][entry_idx])
    entry_price = float(one_min["open"][entry_idx])

    if SLIPPAGE_PCT > 0:
        entry_price *= 1.0 - SLIPPAGE_PCT

    result = simulate_exit_short_base(
        one_min=one_min,
        entry_idx=entry_idx,
        entry_price=entry_price,
        tp_levels=tp_levels,
    )

    result["entry_time_ms"] = entry_time_ms
    result["entry_price"] = entry_price
    result["hold_minutes"] = (result["exit_time_ms"] - entry_time_ms) / 60_000

    return result


# =========================
# CANDIDATES
# =========================

def collect_candidates_for_file(
    symbol: str,
    timeframe: str,
    one_min: dict[str, np.ndarray],
    strategy_mode: str,
) -> list[dict[str, Any]]:
    df = load_indicator_file(symbol, timeframe)

    if df is None or df.empty:
        return []

    base_strategy, tp_profile = parse_strategy_mode(strategy_mode)
    family = get_strategy_family(base_strategy)
    score_series = get_family_score(df, family)
    tp_levels = TP_PROFILES[tp_profile]

    mask = build_strategy_mask(df, strategy_mode)
    indices = np.where(mask.to_numpy())[0]

    if MAX_SIGNALS_PER_FILE is not None:
        indices = indices[:MAX_SIGNALS_PER_FILE]

    candidates: list[dict[str, Any]] = []

    for idx in tqdm(indices, desc=f"{strategy_mode} {symbol} {timeframe}", leave=False):
        row = df.iloc[int(idx)]

        signal_score = int(score_series.iloc[int(idx)])
        signal_strength = get_signal_strength(signal_score)
        position_multiplier = FIXED_POSITION_MULTIPLIER

        signal_open_time_ms = int(row["open_time"])
        signal_close_time_ms = int(row["close_time"])

        exit_result = simulate_trade_exit(
            one_min=one_min,
            signal_close_time_ms=signal_close_time_ms,
            tp_levels=tp_levels,
        )

        if exit_result is None:
            continue

        base = {
            "strategy_mode": strategy_mode,
            "base_strategy": base_strategy,
            "tp_profile": tp_profile,
            "symbol": symbol,
            "timeframe": timeframe,
            "htf_timeframe": row.get("htf_timeframe", ""),
            "side": "short",
            "family": family,
            "signal_row_idx": int(idx),
            "signal_strength": signal_strength,
            "signal_score": signal_score,
            "position_multiplier": position_multiplier,
            "tp_levels": "|".join(str(x) for x in tp_levels),
            "stop_loss_pct": STOP_LOSS_PCT,
            "signal_open_time_ms": signal_open_time_ms,
            "signal_close_time_ms": signal_close_time_ms,
            "signal_open_time": ms_to_dt(signal_open_time_ms),
            "signal_close_time": ms_to_dt(signal_close_time_ms),
            "entry_time_ms": exit_result["entry_time_ms"],
            "entry_time": ms_to_dt(exit_result["entry_time_ms"]),
            "entry_price": exit_result["entry_price"],
            "exit_time_ms": exit_result["exit_time_ms"],
            "exit_time": ms_to_dt(exit_result["exit_time_ms"]),
            "exit_price": exit_result["exit_price"],
            "exit_reason": exit_result["exit_reason"],
            "tp_hits": exit_result["tp_hits"],
            "is_win": int(exit_result["tp_hits"] >= 1),
            "weighted_price_pnl_decimal": exit_result["weighted_price_pnl_decimal"],
            "weighted_price_pnl_pct": exit_result["weighted_price_pnl_decimal"] * 100.0,
            "hold_minutes": exit_result["hold_minutes"],
            "closed_parts": exit_result["closed_parts"],
            "entry_rule": "SHORT_TP_GRID",
            "ind_rsi_14": row[RSI14_COL],
            "ind_atr_14_pct": row[ATR14_PCT_COL],
            "ind_dist_to_ema_50_pct": row[DIST_EMA50_COL],
            "ind_volume_ratio_sma_20": row[VOLUME_RATIO20_COL],
            "ind_adx_14": row[ADX14_COL],
            "ind_close_position_in_candle": row[CLOSE_POS_COL],
            "ind_vol_peak_offset_10": row[VOL_PEAK_OFFSET_COL],
            "ind_short_breakdown_score": row[BREAKDOWN_SCORE_COL],
            "ind_short_rejection_score": row[REJECTION_SCORE_COL],
            "ind_short_pullback_score": row[PULLBACK_SCORE_COL],
            "ind_htf_close_time": row.get(f"{HTF_PREFIX}close_time", np.nan),
            "ind_htf_close": row.get(f"{HTF_PREFIX}close", np.nan),
            "ind_htf_dist_to_ema_50_pct": row.get(f"{HTF_PREFIX}{DIST_EMA50_COL}", np.nan),
            "ind_htf_rsi_14": row.get(f"{HTF_PREFIX}{RSI14_COL}", np.nan),
            "ind_htf_adx_14": row.get(f"{HTF_PREFIX}{ADX14_COL}", np.nan),
            "ind_htf_atr_14_pct": row.get(f"{HTF_PREFIX}{ATR14_PCT_COL}", np.nan),
        }

        candidates.append(base)

    del df
    gc.collect()

    return candidates


def collect_all_candidates(strategy_mode: str) -> list[dict[str, Any]]:
    all_candidates: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        one_min = load_1m_data(symbol)

        if one_min is None:
            continue

        for timeframe in ENTRY_TIMEFRAMES:
            print("=" * 100)
            print(f"COLLECT {strategy_mode.upper()} {symbol} {timeframe}")

            candidates = collect_candidates_for_file(
                symbol=symbol,
                timeframe=timeframe,
                one_min=one_min,
                strategy_mode=strategy_mode,
            )

            print(f"Candidates: {len(candidates):,}")
            all_candidates.extend(candidates)

        del one_min
        gc.collect()

    all_candidates.sort(key=lambda x: x["entry_time_ms"])

    return all_candidates


# =========================
# CAPITAL
# =========================

def close_due_trades(
    active_trades: list[dict[str, Any]],
    current_time_ms: int,
    deposit_state: dict[str, float],
    closed_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    due = [trade for trade in active_trades if trade["exit_time_ms"] <= current_time_ms]
    not_due = [trade for trade in active_trades if trade["exit_time_ms"] > current_time_ms]

    due.sort(key=lambda x: x["exit_time_ms"])

    for trade in due:
        deposit_state["deposit"] += trade["pnl_usd"]
        trade["deposit_after_exit"] = deposit_state["deposit"]
        closed_trades.append(trade)

    return not_due


def has_active_symbol(active_trades: list[dict[str, Any]], symbol: str) -> bool:
    return any(trade["symbol"] == symbol for trade in active_trades)


def apply_capital_management(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deposit_state = {"deposit": INITIAL_DEPOSIT}

    active_trades: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []

    skipped_same_symbol = 0
    skipped_max_open = 0
    trade_counter = 0

    for candidate in tqdm(candidates, desc="Apply capital"):
        entry_time_ms = int(candidate["entry_time_ms"])

        active_trades = close_due_trades(
            active_trades=active_trades,
            current_time_ms=entry_time_ms,
            deposit_state=deposit_state,
            closed_trades=closed_trades,
        )

        if ONE_OPEN_TRADE_PER_SYMBOL and has_active_symbol(active_trades, candidate["symbol"]):
            skipped_same_symbol += 1
            continue

        if MAX_OPEN_TRADES is not None and len(active_trades) >= MAX_OPEN_TRADES:
            skipped_max_open += 1
            continue

        deposit_before = deposit_state["deposit"]

        position_multiplier = float(candidate.get("position_multiplier", 1.0))
        margin_usd = deposit_before * ENTRY_DEPOSIT_PCT * position_multiplier
        notional_usd = margin_usd * LEVERAGE

        gross_pnl_usd = notional_usd * float(candidate["weighted_price_pnl_decimal"])
        fees_usd = notional_usd * FEE_RATE * 2.0
        pnl_usd = gross_pnl_usd - fees_usd

        trade_counter += 1

        trade = dict(candidate)

        trade["trade_id"] = f"{trade['strategy_mode']}__{trade_counter:08d}"
        trade["deposit_before_entry"] = deposit_before
        trade["entry_margin_usd"] = margin_usd
        trade["base_entry_margin_usd"] = deposit_before * ENTRY_DEPOSIT_PCT
        trade["notional_usd"] = notional_usd
        trade["leverage"] = LEVERAGE
        trade["gross_pnl_usd"] = gross_pnl_usd
        trade["fees_usd"] = fees_usd
        trade["pnl_usd"] = pnl_usd
        trade["pnl_on_margin_pct"] = (pnl_usd / margin_usd * 100.0) if margin_usd else np.nan
        trade["deposit_after_exit"] = np.nan

        active_trades.append(trade)

    active_trades.sort(key=lambda x: x["exit_time_ms"])

    for trade in active_trades:
        deposit_state["deposit"] += trade["pnl_usd"]
        trade["deposit_after_exit"] = deposit_state["deposit"]
        closed_trades.append(trade)

    closed_trades.sort(key=lambda x: x["exit_time_ms"])

    stats = {
        "initial_deposit": INITIAL_DEPOSIT,
        "final_deposit": deposit_state["deposit"],
        "total_pnl_usd": deposit_state["deposit"] - INITIAL_DEPOSIT,
        "roi_pct": (deposit_state["deposit"] - INITIAL_DEPOSIT) / INITIAL_DEPOSIT * 100.0,
        "candidates": len(candidates),
        "executed_trades": len(closed_trades),
        "skipped_same_symbol": skipped_same_symbol,
        "skipped_max_open": skipped_max_open,
    }

    return closed_trades, stats


# =========================
# SUMMARY
# =========================

def make_summary_row(
    strategy_mode: str,
    scope: str,
    symbol: str,
    timeframe: str,
    htf_timeframe: str,
    family: str,
    tp_profile: str,
    signal_strength: str,
    trades_df: pd.DataFrame,
    global_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trades = len(trades_df)
    wins = int(trades_df["is_win"].sum()) if trades else 0
    losses = trades - wins

    row = {
        "strategy_mode": strategy_mode,
        "scope": scope,
        "symbol": symbol,
        "timeframe": timeframe,
        "htf_timeframe": htf_timeframe,
        "family": family,
        "tp_profile": tp_profile,
        "signal_strength": signal_strength,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate": wins / trades * 100.0 if trades else 0.0,
        "total_pnl_usd": trades_df["pnl_usd"].sum() if trades else 0.0,
        "avg_pnl_usd": trades_df["pnl_usd"].mean() if trades else 0.0,
        "avg_pnl_on_margin_pct": trades_df["pnl_on_margin_pct"].mean() if trades else 0.0,
        "avg_tp_hits": trades_df["tp_hits"].mean() if trades else 0.0,
        "avg_hold_minutes": trades_df["hold_minutes"].mean() if trades else 0.0,
        "avg_position_multiplier": (
            trades_df["position_multiplier"].mean()
            if trades and "position_multiplier" in trades_df.columns
            else 1.0
        ),
        "initial_deposit": np.nan,
        "final_deposit": np.nan,
        "roi_pct": np.nan,
        "max_drawdown_pct": np.nan,
        "candidates": np.nan,
        "executed_trades": np.nan,
        "skipped_same_symbol": np.nan,
        "skipped_max_open": np.nan,
    }

    if global_stats is not None:
        equity = trades_df["deposit_after_exit"].astype(float)
        peak = equity.cummax()
        drawdown = (equity - peak) / peak.replace(0, np.nan) * 100.0
        max_drawdown_pct = float(drawdown.min()) if len(drawdown) else 0.0

        row.update(
            {
                "initial_deposit": global_stats["initial_deposit"],
                "final_deposit": global_stats["final_deposit"],
                "roi_pct": global_stats["roi_pct"],
                "max_drawdown_pct": max_drawdown_pct,
                "candidates": global_stats["candidates"],
                "executed_trades": global_stats["executed_trades"],
                "skipped_same_symbol": global_stats["skipped_same_symbol"],
                "skipped_max_open": global_stats["skipped_max_open"],
            }
        )

    return row


def build_summary(trades_df: pd.DataFrame, global_stats: dict[str, Any], strategy_mode: str) -> pd.DataFrame:
    base_strategy, tp_profile = parse_strategy_mode(strategy_mode)
    family = get_strategy_family(base_strategy)

    if trades_df.empty:
        return pd.DataFrame(
            [
                {
                    "strategy_mode": strategy_mode,
                    "scope": "GLOBAL",
                    "symbol": "ALL",
                    "timeframe": "ALL",
                    "htf_timeframe": "ALL",
                    "family": family,
                    "tp_profile": tp_profile,
                    "signal_strength": "ALL",
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "winrate": 0.0,
                    "total_pnl_usd": 0.0,
                    "avg_pnl_usd": 0.0,
                    "avg_pnl_on_margin_pct": 0.0,
                    "avg_tp_hits": 0.0,
                    "avg_hold_minutes": 0.0,
                    "avg_position_multiplier": FIXED_POSITION_MULTIPLIER,
                    "initial_deposit": global_stats["initial_deposit"],
                    "final_deposit": global_stats["final_deposit"],
                    "roi_pct": global_stats["roi_pct"],
                    "max_drawdown_pct": 0.0,
                    "candidates": global_stats["candidates"],
                    "executed_trades": global_stats["executed_trades"],
                    "skipped_same_symbol": global_stats["skipped_same_symbol"],
                    "skipped_max_open": global_stats["skipped_max_open"],
                }
            ]
        )

    df = trades_df.copy()

    df["is_win"] = pd.to_numeric(df["is_win"], errors="coerce").fillna(0).astype(int)
    df["pnl_usd"] = pd.to_numeric(df["pnl_usd"], errors="coerce").fillna(0.0)
    df["hold_minutes"] = pd.to_numeric(df["hold_minutes"], errors="coerce")
    df["pnl_on_margin_pct"] = pd.to_numeric(df["pnl_on_margin_pct"], errors="coerce")
    df["position_multiplier"] = pd.to_numeric(df["position_multiplier"], errors="coerce").fillna(1.0)

    rows: list[dict[str, Any]] = []

    rows.append(
        make_summary_row(
            strategy_mode=strategy_mode,
            scope="GLOBAL",
            symbol="ALL",
            timeframe="ALL",
            htf_timeframe="ALL",
            family=family,
            tp_profile=tp_profile,
            signal_strength="ALL",
            trades_df=df,
            global_stats=global_stats,
        )
    )

    for (symbol, timeframe, group_family, group_tp), g in df.groupby(
        ["symbol", "timeframe", "family", "tp_profile"],
        dropna=False,
    ):
        rows.append(
            make_summary_row(
                strategy_mode=strategy_mode,
                scope="GROUP_SYMBOL_TF",
                symbol=str(symbol),
                timeframe=str(timeframe),
                htf_timeframe="ALL",
                family=str(group_family),
                tp_profile=str(group_tp),
                signal_strength="ALL",
                trades_df=g,
            )
        )

    for strength, g in df.groupby("signal_strength", dropna=False):
        rows.append(
            make_summary_row(
                strategy_mode=strategy_mode,
                scope="GROUP_STRENGTH",
                symbol="ALL",
                timeframe="ALL",
                htf_timeframe="ALL",
                family=family,
                tp_profile=tp_profile,
                signal_strength=str(strength),
                trades_df=g,
            )
        )

    return pd.DataFrame(rows)


# =========================
# RUNNERS
# =========================

def run_single_backtest(strategy_mode: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_results_dir()

    base_strategy, tp_profile = parse_strategy_mode(strategy_mode)

    print("=" * 100)
    print(f"BACKTEST SHORT: {strategy_mode.upper()}")
    print(f"Base strategy: {base_strategy}")
    print(f"TP profile: {tp_profile} -> {TP_PROFILES[tp_profile]}")
    print(f"Initial deposit: {INITIAL_DEPOSIT}")
    print(f"Entry deposit pct: {ENTRY_DEPOSIT_PCT * 100:.2f}%")
    print(f"Position multiplier: {FIXED_POSITION_MULTIPLIER:.2f}x")
    print(f"Leverage: {LEVERAGE}x")
    print(f"Fee rate: {FEE_RATE}")
    print(f"Entry TFs: {ENTRY_TIMEFRAMES}")
    print(f"HTF map: {ENTRY_TO_HTF}")
    print(f"Exit TF: 1m")
    print("=" * 100)

    candidates = collect_all_candidates(strategy_mode)

    print("=" * 100)
    print(f"{strategy_mode.upper()} total candidates: {len(candidates):,}")

    executed_trades, global_stats = apply_capital_management(candidates)

    trades_df = pd.DataFrame(executed_trades)
    summary_df = build_summary(trades_df, global_stats, strategy_mode)

    trades_path = RESULTS_DIR / f"manual_backtest_short_{strategy_mode}_trades.csv"
    summary_path = RESULTS_DIR / f"manual_backtest_short_{strategy_mode}_summary.csv"

    trades_df.to_csv(trades_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    print("=" * 100)
    print(f"DONE {strategy_mode.upper()}")
    print(f"Trades : {trades_path}")
    print(f"Summary: {summary_path}")

    global_row = summary_df[summary_df["scope"] == "GLOBAL"].iloc[0]

    print("-" * 100)
    print(f"Candidates: {int(global_row['candidates']):,}")
    print(f"Executed: {int(global_row['executed_trades']):,}")
    print(f"Winrate: {float(global_row['winrate']):.2f}%")
    print(f"Final deposit: {float(global_row['final_deposit']):.2f}")
    print(f"Total PnL: {float(global_row['total_pnl_usd']):.2f}")
    print(f"ROI: {float(global_row['roi_pct']):.2f}%")
    print("=" * 100)

    del candidates
    gc.collect()

    return trades_df, summary_df


def run_all_backtests() -> None:
    ensure_results_dir()

    all_summaries = []

    for strategy_mode in STRATEGY_MODES:
        _, summary_df = run_single_backtest(strategy_mode)
        all_summaries.append(summary_df)

    compare_df = pd.concat(all_summaries, ignore_index=True)
    compare_path = RESULTS_DIR / "manual_backtest_short_compare_summary.csv"
    compare_df.to_csv(compare_path, index=False)

    global_compare = compare_df[compare_df["scope"] == "GLOBAL"].copy()

    print("\n" + "=" * 100)
    print("SHORT COMPARE SUMMARY")
    print("=" * 100)

    if not global_compare.empty:
        cols = [
            "strategy_mode",
            "family",
            "tp_profile",
            "trades",
            "winrate",
            "total_pnl_usd",
            "roi_pct",
            "max_drawdown_pct",
            "avg_pnl_on_margin_pct",
            "avg_tp_hits",
            "avg_hold_minutes",
            "avg_position_multiplier",
            "final_deposit",
            "candidates",
            "executed_trades",
            "skipped_same_symbol",
        ]

        existing_cols = [c for c in cols if c in global_compare.columns]
        global_compare = global_compare.sort_values("roi_pct", ascending=False)

        print(global_compare[existing_cols].to_string(index=False))

    print("-" * 100)
    print(f"Compare file: {compare_path}")
    print("=" * 100)


if __name__ == "__main__":
    run_all_backtests()
