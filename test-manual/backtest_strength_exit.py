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

# next higher timeframe scheme
ENTRY_TO_HTF = {
    "15m": "1h",
    "30m": "2h",
    "1h": "4h",
    "2h": "4h",
    "4h": "1d",
}

STRATEGY_MODES = [
    # контроль: наш поточний найкращий кандидат
    "long_breakout_v26_base",

    # V26 + HTF close > EMA50
    "long_breakout_v31_htf_ema",

    # V26 + HTF close > EMA50 + HTF RSI14 52–75
    "long_breakout_v34_htf_ema_rsi",

    # V26 + HTF close > EMA50 + HTF ADX14 >= 18
    "long_breakout_v35_htf_ema_adx",

    # V26 + HTF close > EMA50 + HTF RSI14 52–75 + HTF ADX14 >= 18
    "long_breakout_v37_htf_full",
]

INITIAL_DEPOSIT = 1000.0
ENTRY_DEPOSIT_PCT = 0.01
LEVERAGE = 50.0
FEE_RATE = 0.0004
SLIPPAGE_PCT = 0.0

ONE_OPEN_TRADE_PER_SYMBOL = True
MAX_OPEN_TRADES: int | None = None
MAX_SIGNALS_PER_FILE: int | None = None

RSI14_COL = "rsi_14"
ATR14_PCT_COL = "atr_14_pct"
DIST_EMA50_COL = "dist_to_ema_50_pct"
VOLUME_RATIO20_COL = "volume_ratio_sma_20"
ADX14_COL = "adx_14"
CLOSE_POS_COL = "close_position_in_candle"
VOL_PEAK_OFFSET_COL = "vol_peak_offset_10"

OLD_SCORE_COL = "old_strength_score"
NEW_MEDIUM_COL = "is_new_medium"
NEW_STRONG_COL = "is_new_strong"

HTF_PREFIX = "htf_"

TP_LEVELS = [0.010, 0.020, 0.030, 0.040]
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


def calculate_old_strength_score(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    score = pd.Series(0, index=df.index, dtype="int64")

    score += (adx14 >= 25).astype("int64")
    score += (adx14 >= 30).astype("int64")

    score += (volume_ratio >= 2.0).astype("int64")
    score += (volume_ratio >= 2.5).astype("int64")

    score += (rsi14 >= 80).astype("int64")
    score += ((rsi14 >= 80) & (rsi14 <= 88)).astype("int64")

    score += (vol_peak_offset >= -2).astype("int64")

    score += (close_pos < 0.80).astype("int64")
    score += ((close_pos >= 0.45) & (close_pos <= 0.80)).astype("int64")

    return score


def build_v18_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50_abs = df[DIST_EMA50_COL].abs()
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    valid = (
        rsi14.notna()
        & atr14_pct.notna()
        & dist_ema50_abs.notna()
        & volume_ratio.notna()
        & adx14.notna()
        & close_pos.notna()
        & vol_peak_offset.notna()
    )

    return (
        valid
        & (rsi14 >= 75)
        & (rsi14 <= 90)
        & (dist_ema50_abs >= 1.5)
        & (dist_ema50_abs <= 4.0)
        & (volume_ratio >= 1.5)
        & (atr14_pct >= 0.4)
        & (atr14_pct <= 0.8)
        & (vol_peak_offset >= -4)
        & (close_pos < 0.85)
        & (adx14 >= 22)
    ).fillna(False)


def build_new_medium_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50_abs = df[DIST_EMA50_COL].abs()
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    return (
        (rsi14 >= 78)
        & (rsi14 <= 88)
        & (dist_ema50_abs >= 1.7)
        & (dist_ema50_abs <= 3.5)
        & (volume_ratio >= 2.0)
        & (atr14_pct >= 0.45)
        & (atr14_pct <= 0.75)
        & (vol_peak_offset >= -3)
        & (close_pos >= 0.45)
        & (close_pos <= 0.80)
        & (adx14 >= 25)
    ).fillna(False)


def build_new_strong_mask(df: pd.DataFrame) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50_abs = df[DIST_EMA50_COL].abs()
    volume_ratio = df[VOLUME_RATIO20_COL]
    adx14 = df[ADX14_COL]
    close_pos = df[CLOSE_POS_COL]
    vol_peak_offset = df[VOL_PEAK_OFFSET_COL]

    return (
        (rsi14 >= 80)
        & (rsi14 <= 88)
        & (dist_ema50_abs >= 1.8)
        & (dist_ema50_abs <= 3.2)
        & (volume_ratio >= 2.3)
        & (atr14_pct >= 0.45)
        & (atr14_pct <= 0.70)
        & (vol_peak_offset >= -2)
        & (close_pos >= 0.50)
        & (close_pos <= 0.78)
        & (adx14 >= 28)
    ).fillna(False)


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

    df["v18_mask"] = build_v18_mask(df)
    df[OLD_SCORE_COL] = calculate_old_strength_score(df)
    df[NEW_MEDIUM_COL] = build_new_medium_mask(df)
    df[NEW_STRONG_COL] = build_new_strong_mask(df)

    return df


# =========================
# STRATEGY LOGIC
# =========================

def htf_ema_ok(df: pd.DataFrame) -> pd.Series:
    col = f"{HTF_PREFIX}{DIST_EMA50_COL}"
    if col not in df.columns:
        return pd.Series(False, index=df.index)

    return (df["has_htf"] & df[col].notna() & (df[col] > 0)).fillna(False)


def htf_rsi_ok(df: pd.DataFrame) -> pd.Series:
    col = f"{HTF_PREFIX}{RSI14_COL}"
    if col not in df.columns:
        return pd.Series(False, index=df.index)

    return (df["has_htf"] & df[col].notna() & (df[col] >= 52) & (df[col] <= 75)).fillna(False)


def htf_adx_ok(df: pd.DataFrame) -> pd.Series:
    col = f"{HTF_PREFIX}{ADX14_COL}"
    if col not in df.columns:
        return pd.Series(False, index=df.index)

    return (df["has_htf"] & df[col].notna() & (df[col] >= 18)).fillna(False)


def build_strategy_mask(df: pd.DataFrame, strategy_mode: str) -> pd.Series:
    v26_base = df["v18_mask"] & (df[OLD_SCORE_COL] >= 4)

    if strategy_mode == "long_breakout_v26_base":
        return v26_base

    if strategy_mode == "long_breakout_v31_htf_ema":
        return v26_base & htf_ema_ok(df)

    if strategy_mode == "long_breakout_v34_htf_ema_rsi":
        return v26_base & htf_ema_ok(df) & htf_rsi_ok(df)

    if strategy_mode == "long_breakout_v35_htf_ema_adx":
        return v26_base & htf_ema_ok(df) & htf_adx_ok(df)

    if strategy_mode == "long_breakout_v37_htf_full":
        return v26_base & htf_ema_ok(df) & htf_rsi_ok(df) & htf_adx_ok(df)

    raise ValueError(f"Unknown strategy_mode: {strategy_mode}")


def get_signal_strength_and_position(row: pd.Series) -> tuple[str, int, float]:
    score = int(row[OLD_SCORE_COL])
    is_new_medium = bool(row[NEW_MEDIUM_COL])
    is_new_strong = bool(row[NEW_STRONG_COL])

    if is_new_strong:
        return "new_strong", score, 1.50

    if is_new_medium:
        return "new_medium", score, 1.25

    return "new_weak_old_medium", score, 1.0


# =========================
# EXIT SIMULATION
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


def simulate_exit_long_base(
    one_min: dict[str, np.ndarray],
    entry_idx: int,
    entry_price: float,
) -> dict[str, Any]:
    open_times = one_min["open_time"]
    highs = one_min["high"]
    lows = one_min["low"]
    closes = one_min["close"]

    tp_prices = [entry_price * (1.0 + pct) for pct in TP_LEVELS]
    current_sl = entry_price * (1.0 - STOP_LOSS_PCT)

    remaining = 1.0
    tp_hits = 0
    realized_price_pnl_decimal = 0.0
    closed_parts: list[str] = []

    for i in range(entry_idx, len(open_times)):
        current_time_ms = int(open_times[i])
        high = float(highs[i])
        low = float(lows[i])

        if low <= current_sl:
            realized_price_pnl_decimal += remaining * ((current_sl - entry_price) / entry_price)
            reason = "SL" if tp_hits == 0 else f"TP{tp_hits}_THEN_BE"
            closed_parts.append(f"{reason}:{remaining:.2f}@{current_sl:.8f}")

            return {
                "exit_time_ms": current_time_ms,
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                "closed_parts": "|".join(closed_parts),
            }

        while tp_hits < len(tp_prices) and high >= tp_prices[tp_hits]:
            part = float(TP_PARTS[tp_hits])
            tp_price = float(tp_prices[tp_hits])

            realized_price_pnl_decimal += part * ((tp_price - entry_price) / entry_price)
            remaining -= part
            tp_hits += 1

            closed_parts.append(f"TP{tp_hits}:{part:.2f}@{tp_price:.8f}")

            if tp_hits >= BE_AFTER_TP_HITS:
                current_sl = max(current_sl, entry_price)

            if remaining <= 1e-12:
                return {
                    "exit_time_ms": current_time_ms,
                    "exit_price": float(tp_price),
                    "exit_reason": "TP_ALL",
                    "tp_hits": tp_hits,
                    "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                    "closed_parts": "|".join(closed_parts),
                }

        if tp_hits >= BE_AFTER_TP_HITS and low <= current_sl:
            realized_price_pnl_decimal += remaining * ((current_sl - entry_price) / entry_price)
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
    realized_price_pnl_decimal += remaining * ((last_close - entry_price) / entry_price)
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
) -> dict[str, Any] | None:
    entry_idx = find_entry_index_1m(one_min, signal_close_time_ms)

    if entry_idx is None:
        return None

    entry_time_ms = int(one_min["open_time"][entry_idx])
    entry_price = float(one_min["open"][entry_idx])

    if SLIPPAGE_PCT > 0:
        entry_price *= 1.0 + SLIPPAGE_PCT

    result = simulate_exit_long_base(
        one_min=one_min,
        entry_idx=entry_idx,
        entry_price=entry_price,
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

    mask = build_strategy_mask(df, strategy_mode)
    indices = np.where(mask.to_numpy())[0]

    if MAX_SIGNALS_PER_FILE is not None:
        indices = indices[:MAX_SIGNALS_PER_FILE]

    candidates: list[dict[str, Any]] = []

    for idx in tqdm(indices, desc=f"{strategy_mode} {symbol} {timeframe}", leave=False):
        row = df.iloc[int(idx)]

        signal_strength, signal_score, position_multiplier = get_signal_strength_and_position(row)

        if position_multiplier <= 0:
            continue

        signal_open_time_ms = int(row["open_time"])
        signal_close_time_ms = int(row["close_time"])

        exit_result = simulate_trade_exit(
            one_min=one_min,
            signal_close_time_ms=signal_close_time_ms,
        )

        if exit_result is None:
            continue

        base = {
            "strategy_mode": strategy_mode,
            "symbol": symbol,
            "timeframe": timeframe,
            "htf_timeframe": row.get("htf_timeframe", ""),
            "side": "long",
            "signal_row_idx": int(idx),
            "signal_strength": signal_strength,
            "signal_score": signal_score,
            "position_multiplier": position_multiplier,
            "tp_levels": "|".join(str(x) for x in TP_LEVELS),
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
            "entry_rule": "LONG_BREAKOUT_V26_HTF",
            "ind_rsi_14": row[RSI14_COL],
            "ind_atr_14_pct": row[ATR14_PCT_COL],
            "ind_dist_to_ema_50_pct": row[DIST_EMA50_COL],
            "ind_volume_ratio_sma_20": row[VOLUME_RATIO20_COL],
            "ind_adx_14": row[ADX14_COL],
            "ind_close_position_in_candle": row[CLOSE_POS_COL],
            "ind_vol_peak_offset_10": row[VOL_PEAK_OFFSET_COL],
            "ind_old_strength_score": row[OLD_SCORE_COL],
            "ind_is_new_medium": row[NEW_MEDIUM_COL],
            "ind_is_new_strong": row[NEW_STRONG_COL],
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
    if trades_df.empty:
        return pd.DataFrame(
            [
                {
                    "strategy_mode": strategy_mode,
                    "scope": "GLOBAL",
                    "symbol": "ALL",
                    "timeframe": "ALL",
                    "htf_timeframe": "ALL",
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
                    "avg_position_multiplier": 0.0,
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
            signal_strength="ALL",
            trades_df=df,
            global_stats=global_stats,
        )
    )

    for (symbol, timeframe, htf_timeframe), g in df.groupby(
        ["symbol", "timeframe", "htf_timeframe"],
        dropna=False,
    ):
        rows.append(
            make_summary_row(
                strategy_mode=strategy_mode,
                scope="GROUP_SYMBOL_TF",
                symbol=str(symbol),
                timeframe=str(timeframe),
                htf_timeframe=str(htf_timeframe),
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

    print("=" * 100)
    print(f"BACKTEST V26 HTF: {strategy_mode.upper()}")
    print(f"Initial deposit: {INITIAL_DEPOSIT}")
    print(f"Entry deposit pct: {ENTRY_DEPOSIT_PCT * 100:.2f}%")
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

    trades_path = RESULTS_DIR / f"manual_backtest_v26_htf_{strategy_mode}_trades.csv"
    summary_path = RESULTS_DIR / f"manual_backtest_v26_htf_{strategy_mode}_summary.csv"

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
    compare_path = RESULTS_DIR / "manual_backtest_v26_htf_compare_summary.csv"
    compare_df.to_csv(compare_path, index=False)

    global_compare = compare_df[compare_df["scope"] == "GLOBAL"].copy()

    print("\n" + "=" * 100)
    print("V26 HTF COMPARE SUMMARY")
    print("=" * 100)

    if not global_compare.empty:
        cols = [
            "strategy_mode",
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
