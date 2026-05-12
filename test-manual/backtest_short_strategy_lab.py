from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm


DATA_DIR = Path("test-manual/data")
INDICATORS_DIR = Path("test-manual/indicators")
RESULTS_DIR = Path("test-manual/results")

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
]

ENTRY_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h"]
ENTRY_TO_HTF = {"15m": "1h", "30m": "2h", "1h": "4h", "2h": "4h", "4h": "1d"}

STRATEGIES = {
    "short_s1_breakdown_base": {"family": "breakdown", "score4": False, "htf": False, "tp": "base"},
    "short_s2_breakdown_score4": {"family": "breakdown", "score4": True, "htf": False, "tp": "base"},
    "short_s3_rejection_base": {"family": "rejection", "score4": False, "htf": False, "tp": "base"},
    "short_s4_rejection_score4": {"family": "rejection", "score4": True, "htf": False, "tp": "base"},
    "short_s5_pullback_base": {"family": "pullback", "score4": False, "htf": False, "tp": "base"},
    "short_s6_pullback_score4": {"family": "pullback", "score4": True, "htf": False, "tp": "base"},
    "short_s7_breakdown_htf_score4": {"family": "breakdown", "score4": True, "htf": True, "tp": "base"},
    "short_s8_pullback_htf_score4": {"family": "pullback", "score4": True, "htf": True, "tp": "base"},
    "short_s9_breakdown_score4_safer_exit": {"family": "breakdown", "score4": True, "htf": False, "tp": "safer"},
    "short_s10_pullback_score4_safer_exit": {"family": "pullback", "score4": True, "htf": False, "tp": "safer"},
}

INITIAL_DEPOSIT = 1000.0
ENTRY_DEPOSIT_PCT = 0.01
LEVERAGE = 1.0
FEE_RATE = 0.0004
SLIPPAGE_PCT = 0.0

ONE_OPEN_TRADE_PER_SYMBOL = True
MAX_OPEN_TRADES: int | None = None
MAX_SIGNALS_PER_FILE: int | None = None

RSI14 = "rsi_14"
ATR14P = "atr_14_pct"
DIST_EMA50 = "dist_to_ema_50_pct"
VOL_RATIO = "volume_ratio_sma_20"
ADX14 = "adx_14"
CLOSE_POS = "close_position_in_candle"
VOL_PEAK = "vol_peak_offset_10"

BASE_TP = [0.010, 0.020, 0.030, 0.040]
SAFER_TP = [0.008, 0.016, 0.024, 0.032]
TP_PARTS = [0.25, 0.25, 0.25, 0.25]
STOP_LOSS_PCT = 0.020
BE_AFTER_TP_HITS = 1


def ms_to_dt(ms):
    if ms is None or pd.isna(ms):
        return None
    return pd.to_datetime(int(ms), unit="ms", utc=True).isoformat()


def ensure_results_dir():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def close_position_in_candle(df: pd.DataFrame) -> pd.Series:
    rng = df["high"] - df["low"]
    return pd.Series(np.where(rng.abs() > 1e-12, (df["close"] - df["low"]) / rng, np.nan), index=df.index)


def vol_peak_offset_10(s: pd.Series) -> pd.Series:
    def f(arr):
        if np.isnan(arr).any():
            return np.nan
        return float(int(np.argmax(arr)) - 10)
    return s.rolling(11, min_periods=11).apply(f, raw=True)


def load_1m(symbol: str) -> dict[str, np.ndarray] | None:
    path = DATA_DIR / symbol / f"{symbol}_1m.csv.gz"
    if not path.exists():
        print(f"SKIP {symbol}: missing 1m data: {path}")
        return None

    df = pd.read_csv(path, usecols=["open_time", "open", "high", "low", "close"])
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)

    for c in ["open_time", "open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["open_time", "open", "high", "low", "close"])

    return {
        "open_time": df["open_time"].astype("int64").to_numpy(),
        "open": df["open"].astype("float64").to_numpy(),
        "high": df["high"].astype("float64").to_numpy(),
        "low": df["low"].astype("float64").to_numpy(),
        "close": df["close"].astype("float64").to_numpy(),
    }


def load_raw_indicators(symbol: str, tf: str) -> pd.DataFrame | None:
    path = INDICATORS_DIR / symbol / f"{symbol}_{tf}_indicators.csv.gz"
    if not path.exists():
        print(f"SKIP missing indicators: {path}")
        return None

    df = pd.read_csv(path)

    required = [
        "datetime", "open_time", "close_time", "open", "high", "low", "close", "volume",
        RSI14, ATR14P, DIST_EMA50, VOL_RATIO, ADX14,
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"SKIP {symbol} {tf}: missing columns: {missing}")
        return None

    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True).copy()

    numeric_cols = [
        "open_time", "close_time", "open", "high", "low", "close", "volume",
        RSI14, ATR14P, DIST_EMA50, VOL_RATIO, ADX14,
    ]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if CLOSE_POS in df.columns:
        df[CLOSE_POS] = pd.to_numeric(df[CLOSE_POS], errors="coerce")
    else:
        df[CLOSE_POS] = close_position_in_candle(df)

    df[VOL_PEAK] = vol_peak_offset_10(df[VOL_RATIO])

    return df


def attach_htf(df: pd.DataFrame, symbol: str, tf: str) -> pd.DataFrame:
    htf = ENTRY_TO_HTF.get(tf, "")
    df = df.copy()
    df["htf_timeframe"] = htf
    df["has_htf"] = False

    if not htf:
        return df

    htf_df = load_raw_indicators(symbol, htf)
    if htf_df is None or htf_df.empty:
        return df

    htf_df = htf_df[["close_time", "close", DIST_EMA50, RSI14, ADX14, ATR14P, CLOSE_POS]].copy()
    htf_df = htf_df.rename(columns={
        "close_time": "htf_close_time",
        "close": "htf_close",
        DIST_EMA50: "htf_dist_to_ema_50_pct",
        RSI14: "htf_rsi_14",
        ADX14: "htf_adx_14",
        ATR14P: "htf_atr_14_pct",
        CLOSE_POS: "htf_close_position_in_candle",
    }).sort_values("htf_close_time").reset_index(drop=True)

    left = df.sort_values("close_time").reset_index(drop=False).rename(columns={"index": "_orig_index"})
    merged = pd.merge_asof(
        left,
        htf_df,
        left_on="close_time",
        right_on="htf_close_time",
        direction="backward",
        allow_exact_matches=True,
    )
    merged = merged.sort_values("_orig_index").drop(columns=["_orig_index"]).reset_index(drop=True)
    merged["has_htf"] = merged["htf_close_time"].notna()

    return merged


def mask_breakdown(df: pd.DataFrame) -> pd.Series:
    valid = (
        df[RSI14].notna() & df[ATR14P].notna() & df[DIST_EMA50].notna()
        & df[VOL_RATIO].notna() & df[ADX14].notna() & df[CLOSE_POS].notna()
        & df[VOL_PEAK].notna()
    )
    return (
        valid
        & (df[RSI14] <= 45)
        & (df[DIST_EMA50] <= -1.0)
        & (df[DIST_EMA50] >= -4.0)
        & (df[VOL_RATIO] >= 1.5)
        & (df[ATR14P] >= 0.4)
        & (df[ATR14P] <= 1.2)
        & (df[VOL_PEAK] >= -4)
        & (df[CLOSE_POS] <= 0.55)
        & (df[ADX14] >= 20)
    ).fillna(False)


def mask_rejection(df: pd.DataFrame) -> pd.Series:
    valid = (
        df[RSI14].notna() & df[ATR14P].notna() & df[DIST_EMA50].notna()
        & df[VOL_RATIO].notna() & df[ADX14].notna() & df[CLOSE_POS].notna()
    )
    return (
        valid
        & (df[RSI14] >= 58)
        & (df[RSI14] <= 75)
        & (df[DIST_EMA50] >= 1.2)
        & (df[DIST_EMA50] <= 4.5)
        & (df[VOL_RATIO] >= 1.2)
        & (df[ATR14P] >= 0.3)
        & (df[ATR14P] <= 1.2)
        & (df[CLOSE_POS] <= 0.60)
        & (df[ADX14] >= 18)
    ).fillna(False)


def mask_pullback(df: pd.DataFrame) -> pd.Series:
    valid = (
        df[RSI14].notna() & df[ATR14P].notna() & df[DIST_EMA50].notna()
        & df[VOL_RATIO].notna() & df[ADX14].notna() & df[CLOSE_POS].notna()
    )
    return (
        valid
        & (df[RSI14] >= 45)
        & (df[RSI14] <= 60)
        & (df[DIST_EMA50] >= -0.5)
        & (df[DIST_EMA50] <= 1.5)
        & (df[VOL_RATIO] >= 1.2)
        & (df[ATR14P] >= 0.3)
        & (df[ATR14P] <= 1.0)
        & (df[CLOSE_POS] <= 0.55)
        & (df[ADX14] >= 18)
    ).fillna(False)


def score_breakdown(df: pd.DataFrame) -> pd.Series:
    score = pd.Series(0, index=df.index, dtype="int64")
    score += (df[RSI14] <= 40).astype("int64")
    score += ((df[RSI14] >= 30) & (df[RSI14] <= 42)).astype("int64")
    score += (df[DIST_EMA50] <= -1.5).astype("int64")
    score += (df[DIST_EMA50] >= -3.5).astype("int64")
    score += (df[VOL_RATIO] >= 2.0).astype("int64")
    score += (df[VOL_RATIO] >= 2.5).astype("int64")
    score += (df[VOL_PEAK] >= -2).astype("int64")
    score += (df[CLOSE_POS] <= 0.45).astype("int64")
    score += (df[ADX14] >= 25).astype("int64")
    return score


def score_rejection(df: pd.DataFrame) -> pd.Series:
    score = pd.Series(0, index=df.index, dtype="int64")
    score += ((df[RSI14] >= 60) & (df[RSI14] <= 72)).astype("int64")
    score += ((df[RSI14] >= 62) & (df[RSI14] <= 70)).astype("int64")
    score += ((df[DIST_EMA50] >= 1.5) & (df[DIST_EMA50] <= 4.0)).astype("int64")
    score += ((df[DIST_EMA50] >= 2.0) & (df[DIST_EMA50] <= 3.5)).astype("int64")
    score += (df[VOL_RATIO] >= 1.5).astype("int64")
    score += (df[VOL_RATIO] >= 2.0).astype("int64")
    score += (df[CLOSE_POS] <= 0.50).astype("int64")
    score += (df[ADX14] >= 22).astype("int64")
    score += ((df[ATR14P] >= 0.4) & (df[ATR14P] <= 1.0)).astype("int64")
    return score


def score_pullback(df: pd.DataFrame) -> pd.Series:
    score = pd.Series(0, index=df.index, dtype="int64")
    score += ((df[RSI14] >= 48) & (df[RSI14] <= 58)).astype("int64")
    score += ((df[RSI14] >= 50) & (df[RSI14] <= 56)).astype("int64")
    score += ((df[DIST_EMA50] >= -0.2) & (df[DIST_EMA50] <= 1.2)).astype("int64")
    score += ((df[DIST_EMA50] >= 0.0) & (df[DIST_EMA50] <= 1.0)).astype("int64")
    score += (df[VOL_RATIO] >= 1.5).astype("int64")
    score += (df[VOL_RATIO] >= 2.0).astype("int64")
    score += (df[CLOSE_POS] <= 0.45).astype("int64")
    score += (df[ADX14] >= 22).astype("int64")
    score += ((df[ATR14P] >= 0.35) & (df[ATR14P] <= 0.9)).astype("int64")
    return score


def load_indicators(symbol: str, tf: str) -> pd.DataFrame | None:
    df = load_raw_indicators(symbol, tf)
    if df is None or df.empty:
        return None

    df = attach_htf(df, symbol, tf)

    df["mask_breakdown"] = mask_breakdown(df)
    df["mask_rejection"] = mask_rejection(df)
    df["mask_pullback"] = mask_pullback(df)

    df["score_breakdown"] = score_breakdown(df)
    df["score_rejection"] = score_rejection(df)
    df["score_pullback"] = score_pullback(df)

    return df


def htf_bearish(df: pd.DataFrame) -> pd.Series:
    needed = ["has_htf", "htf_dist_to_ema_50_pct", "htf_rsi_14", "htf_adx_14"]
    if any(c not in df.columns for c in needed):
        return pd.Series(False, index=df.index)

    return (
        df["has_htf"]
        & df["htf_dist_to_ema_50_pct"].notna()
        & df["htf_rsi_14"].notna()
        & df["htf_adx_14"].notna()
        & (df["htf_dist_to_ema_50_pct"] < 0)
        & (df["htf_rsi_14"] <= 55)
        & (df["htf_adx_14"] >= 18)
    ).fillna(False)


def build_strategy_mask(df: pd.DataFrame, strategy_name: str) -> pd.Series:
    cfg = STRATEGIES[strategy_name]
    family = cfg["family"]

    base = df[f"mask_{family}"]
    score = df[f"score_{family}"]

    mask = base.copy()

    if cfg["score4"]:
        mask = mask & (score >= 4)

    if cfg["htf"]:
        mask = mask & htf_bearish(df)

    return mask.fillna(False)


def signal_strength(score: int, row: pd.Series) -> tuple[str, float]:
    vol = float(row[VOL_RATIO])
    adx = float(row[ADX14])
    pos = float(row[CLOSE_POS])

    if score >= 6 and vol >= 2.3 and adx >= 25 and pos <= 0.45:
        return "strong", 1.5

    if score >= 5 and vol >= 2.0 and adx >= 22:
        return "medium", 1.25

    return "weak", 1.0


def find_entry_idx_1m(one_min: dict[str, np.ndarray], signal_close_time_ms: int) -> int | None:
    idx = int(np.searchsorted(one_min["open_time"], signal_close_time_ms + 1, side="left"))
    if idx >= len(one_min["open_time"]):
        return None
    return idx


def simulate_short_exit(
    one_min: dict[str, np.ndarray],
    entry_idx: int,
    entry_price: float,
    tp_levels: list[float],
) -> dict[str, Any]:
    times = one_min["open_time"]
    highs = one_min["high"]
    lows = one_min["low"]
    closes = one_min["close"]

    tp_prices = [entry_price * (1.0 - pct) for pct in tp_levels]
    current_sl = entry_price * (1.0 + STOP_LOSS_PCT)

    remaining = 1.0
    tp_hits = 0
    pnl_dec = 0.0
    parts: list[str] = []

    for i in range(entry_idx, len(times)):
        t = int(times[i])
        high = float(highs[i])
        low = float(lows[i])

        # Conservative order: stop first.
        if high >= current_sl:
            pnl_dec += remaining * ((entry_price - current_sl) / entry_price)
            reason = "SL" if tp_hits == 0 else f"TP{tp_hits}_THEN_BE"
            parts.append(f"{reason}:{remaining:.2f}@{current_sl:.8f}")
            return {
                "exit_time_ms": t,
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": pnl_dec,
                "closed_parts": "|".join(parts),
            }

        while tp_hits < len(tp_prices) and low <= tp_prices[tp_hits]:
            part = float(TP_PARTS[tp_hits])
            tp_price = float(tp_prices[tp_hits])

            pnl_dec += part * ((entry_price - tp_price) / entry_price)
            remaining -= part
            tp_hits += 1
            parts.append(f"TP{tp_hits}:{part:.2f}@{tp_price:.8f}")

            if tp_hits >= BE_AFTER_TP_HITS:
                current_sl = min(current_sl, entry_price)

            if remaining <= 1e-12:
                return {
                    "exit_time_ms": t,
                    "exit_price": float(tp_price),
                    "exit_reason": "TP_ALL",
                    "tp_hits": tp_hits,
                    "weighted_price_pnl_decimal": pnl_dec,
                    "closed_parts": "|".join(parts),
                }

        if tp_hits >= BE_AFTER_TP_HITS and high >= current_sl:
            pnl_dec += remaining * ((entry_price - current_sl) / entry_price)
            reason = f"TP{tp_hits}_THEN_BE"
            parts.append(f"{reason}:{remaining:.2f}@{current_sl:.8f}")
            return {
                "exit_time_ms": t,
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": pnl_dec,
                "closed_parts": "|".join(parts),
            }

    last_close = float(closes[-1])
    pnl_dec += remaining * ((entry_price - last_close) / entry_price)
    parts.append(f"END:{remaining:.2f}@{last_close:.8f}")

    return {
        "exit_time_ms": int(times[-1]),
        "exit_price": float(last_close),
        "exit_reason": "END_OF_DATA",
        "tp_hits": tp_hits,
        "weighted_price_pnl_decimal": pnl_dec,
        "closed_parts": "|".join(parts),
    }


def simulate_trade(one_min: dict[str, np.ndarray], signal_close_time_ms: int, tp_levels: list[float]) -> dict[str, Any] | None:
    idx = find_entry_idx_1m(one_min, signal_close_time_ms)
    if idx is None:
        return None

    entry_time_ms = int(one_min["open_time"][idx])
    entry_price = float(one_min["open"][idx])

    if SLIPPAGE_PCT > 0:
        entry_price *= 1.0 - SLIPPAGE_PCT

    result = simulate_short_exit(one_min, idx, entry_price, tp_levels)
    result["entry_time_ms"] = entry_time_ms
    result["entry_price"] = entry_price
    result["hold_minutes"] = (result["exit_time_ms"] - entry_time_ms) / 60_000

    return result


def collect_candidates_for_file(symbol: str, tf: str, one_min: dict[str, np.ndarray], strategy_name: str) -> list[dict[str, Any]]:
    df = load_indicators(symbol, tf)
    if df is None or df.empty:
        return []

    cfg = STRATEGIES[strategy_name]
    family = cfg["family"]
    tp_levels = SAFER_TP if cfg["tp"] == "safer" else BASE_TP

    mask = build_strategy_mask(df, strategy_name)
    indices = np.where(mask.to_numpy())[0]

    if MAX_SIGNALS_PER_FILE is not None:
        indices = indices[:MAX_SIGNALS_PER_FILE]

    rows: list[dict[str, Any]] = []

    for idx in tqdm(indices, desc=f"{strategy_name} {symbol} {tf}", leave=False):
        row = df.iloc[int(idx)]
        score = int(row[f"score_{family}"])
        strength, pos_mult = signal_strength(score, row)

        exit_result = simulate_trade(one_min, int(row["close_time"]), tp_levels)
        if exit_result is None:
            continue

        item = {
            "strategy_mode": strategy_name,
            "symbol": symbol,
            "timeframe": tf,
            "htf_timeframe": row.get("htf_timeframe", ""),
            "side": "short",
            "family": family,
            "signal_row_idx": int(idx),
            "signal_strength": strength,
            "signal_score": score,
            "position_multiplier": pos_mult,
            "tp_levels": "|".join(str(x) for x in tp_levels),
            "stop_loss_pct": STOP_LOSS_PCT,
            "signal_open_time_ms": int(row["open_time"]),
            "signal_close_time_ms": int(row["close_time"]),
            "signal_open_time": ms_to_dt(row["open_time"]),
            "signal_close_time": ms_to_dt(row["close_time"]),
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
            "entry_rule": "SHORT_STRATEGY_LAB",
            "ind_rsi_14": row[RSI14],
            "ind_atr_14_pct": row[ATR14P],
            "ind_dist_to_ema_50_pct": row[DIST_EMA50],
            "ind_volume_ratio_sma_20": row[VOL_RATIO],
            "ind_adx_14": row[ADX14],
            "ind_close_position_in_candle": row[CLOSE_POS],
            "ind_vol_peak_offset_10": row[VOL_PEAK],
            "ind_score_breakdown": row["score_breakdown"],
            "ind_score_rejection": row["score_rejection"],
            "ind_score_pullback": row["score_pullback"],
            "ind_htf_close_time": row.get("htf_close_time", np.nan),
            "ind_htf_close": row.get("htf_close", np.nan),
            "ind_htf_dist_to_ema_50_pct": row.get("htf_dist_to_ema_50_pct", np.nan),
            "ind_htf_rsi_14": row.get("htf_rsi_14", np.nan),
            "ind_htf_adx_14": row.get("htf_adx_14", np.nan),
            "ind_htf_atr_14_pct": row.get("htf_atr_14_pct", np.nan),
        }

        rows.append(item)

    del df
    gc.collect()

    return rows


def collect_all_candidates(strategy_name: str) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        one_min = load_1m(symbol)
        if one_min is None:
            continue

        for tf in ENTRY_TIMEFRAMES:
            print("=" * 100)
            print(f"COLLECT {strategy_name.upper()} {symbol} {tf}")

            rows = collect_candidates_for_file(symbol, tf, one_min, strategy_name)
            print(f"Candidates: {len(rows):,}")
            all_rows.extend(rows)

        del one_min
        gc.collect()

    all_rows.sort(key=lambda x: x["entry_time_ms"])

    return all_rows


def close_due(active, now_ms, state, closed):
    due = [t for t in active if t["exit_time_ms"] <= now_ms]
    keep = [t for t in active if t["exit_time_ms"] > now_ms]
    due.sort(key=lambda x: x["exit_time_ms"])

    for t in due:
        state["deposit"] += t["pnl_usd"]
        t["deposit_after_exit"] = state["deposit"]
        closed.append(t)

    return keep


def has_active_symbol(active, symbol):
    return any(t["symbol"] == symbol for t in active)


def apply_capital(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = {"deposit": INITIAL_DEPOSIT}
    active: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []

    skipped_same_symbol = 0
    skipped_max_open = 0
    trade_counter = 0

    for c in tqdm(candidates, desc="Apply capital"):
        entry_time_ms = int(c["entry_time_ms"])
        active = close_due(active, entry_time_ms, state, closed)

        if ONE_OPEN_TRADE_PER_SYMBOL and has_active_symbol(active, c["symbol"]):
            skipped_same_symbol += 1
            continue

        if MAX_OPEN_TRADES is not None and len(active) >= MAX_OPEN_TRADES:
            skipped_max_open += 1
            continue

        deposit_before = state["deposit"]
        pos_mult = float(c.get("position_multiplier", 1.0))

        margin_usd = deposit_before * ENTRY_DEPOSIT_PCT * pos_mult
        notional_usd = margin_usd * LEVERAGE

        gross_pnl_usd = notional_usd * float(c["weighted_price_pnl_decimal"])
        fees_usd = notional_usd * FEE_RATE * 2.0
        pnl_usd = gross_pnl_usd - fees_usd

        trade_counter += 1
        t = dict(c)
        t["trade_id"] = f"{t['strategy_mode']}__{trade_counter:08d}"
        t["deposit_before_entry"] = deposit_before
        t["entry_margin_usd"] = margin_usd
        t["base_entry_margin_usd"] = deposit_before * ENTRY_DEPOSIT_PCT
        t["notional_usd"] = notional_usd
        t["leverage"] = LEVERAGE
        t["gross_pnl_usd"] = gross_pnl_usd
        t["fees_usd"] = fees_usd
        t["pnl_usd"] = pnl_usd
        t["pnl_on_margin_pct"] = (pnl_usd / margin_usd * 100.0) if margin_usd else np.nan
        t["deposit_after_exit"] = np.nan

        active.append(t)

    active.sort(key=lambda x: x["exit_time_ms"])
    for t in active:
        state["deposit"] += t["pnl_usd"]
        t["deposit_after_exit"] = state["deposit"]
        closed.append(t)

    closed.sort(key=lambda x: x["exit_time_ms"])

    stats = {
        "initial_deposit": INITIAL_DEPOSIT,
        "final_deposit": state["deposit"],
        "total_pnl_usd": state["deposit"] - INITIAL_DEPOSIT,
        "roi_pct": (state["deposit"] - INITIAL_DEPOSIT) / INITIAL_DEPOSIT * 100.0,
        "candidates": len(candidates),
        "executed_trades": len(closed),
        "skipped_same_symbol": skipped_same_symbol,
        "skipped_max_open": skipped_max_open,
    }

    return closed, stats


def max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0, np.nan) * 100.0
    return float(dd.min()) if len(dd) else 0.0


def summary_row(strategy, scope, df, stats=None, symbol="ALL", tf="ALL", family="ALL", strength="ALL"):
    trades = len(df)
    wins = int(df["is_win"].sum()) if trades else 0

    row = {
        "strategy_mode": strategy,
        "scope": scope,
        "symbol": symbol,
        "timeframe": tf,
        "family": family,
        "signal_strength": strength,
        "trades": trades,
        "wins": wins,
        "losses": trades - wins,
        "winrate": wins / trades * 100.0 if trades else 0.0,
        "total_pnl_usd": df["pnl_usd"].sum() if trades else 0.0,
        "avg_pnl_usd": df["pnl_usd"].mean() if trades else 0.0,
        "avg_pnl_on_margin_pct": df["pnl_on_margin_pct"].mean() if trades else 0.0,
        "avg_tp_hits": df["tp_hits"].mean() if trades else 0.0,
        "avg_hold_minutes": df["hold_minutes"].mean() if trades else 0.0,
        "avg_position_multiplier": df["position_multiplier"].mean() if trades else 0.0,
        "initial_deposit": np.nan,
        "final_deposit": np.nan,
        "roi_pct": np.nan,
        "max_drawdown_pct": np.nan,
        "candidates": np.nan,
        "executed_trades": np.nan,
        "skipped_same_symbol": np.nan,
        "skipped_max_open": np.nan,
    }

    if stats is not None:
        row.update({
            "initial_deposit": stats["initial_deposit"],
            "final_deposit": stats["final_deposit"],
            "roi_pct": stats["roi_pct"],
            "max_drawdown_pct": max_drawdown_pct(df["deposit_after_exit"].astype(float)) if trades else 0.0,
            "candidates": stats["candidates"],
            "executed_trades": stats["executed_trades"],
            "skipped_same_symbol": stats["skipped_same_symbol"],
            "skipped_max_open": stats["skipped_max_open"],
        })

    return row


def build_summary(trades_df: pd.DataFrame, stats: dict[str, Any], strategy: str) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame([summary_row(strategy, "GLOBAL", trades_df, stats)])

    df = trades_df.copy()
    df["is_win"] = pd.to_numeric(df["is_win"], errors="coerce").fillna(0).astype(int)
    for c in ["pnl_usd", "pnl_on_margin_pct", "hold_minutes", "position_multiplier"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    rows = [summary_row(strategy, "GLOBAL", df, stats)]

    for (symbol, tf, family), g in df.groupby(["symbol", "timeframe", "family"], dropna=False):
        rows.append(summary_row(strategy, "GROUP_SYMBOL_TF", g, symbol=str(symbol), tf=str(tf), family=str(family)))

    for strength, g in df.groupby("signal_strength", dropna=False):
        rows.append(summary_row(strategy, "GROUP_STRENGTH", g, strength=str(strength)))

    return pd.DataFrame(rows)


def run_one(strategy: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_results_dir()

    print("=" * 100)
    print(f"BACKTEST SHORT STRATEGY LAB: {strategy}")
    print(f"Initial deposit: {INITIAL_DEPOSIT}")
    print(f"Entry deposit pct: {ENTRY_DEPOSIT_PCT * 100:.2f}%")
    print(f"Leverage: {LEVERAGE}x")
    print(f"Fee rate: {FEE_RATE}")
    print(f"Entry TFs: {ENTRY_TIMEFRAMES}")
    print(f"Exit TF: 1m")
    print("=" * 100)

    candidates = collect_all_candidates(strategy)
    print(f"{strategy} total candidates: {len(candidates):,}")

    trades, stats = apply_capital(candidates)

    trades_df = pd.DataFrame(trades)
    summary_df = build_summary(trades_df, stats, strategy)

    trades_path = RESULTS_DIR / f"manual_backtest_short_lab_{strategy}_trades.csv"
    summary_path = RESULTS_DIR / f"manual_backtest_short_lab_{strategy}_summary.csv"

    trades_df.to_csv(trades_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    global_row = summary_df[summary_df["scope"] == "GLOBAL"].iloc[0]
    print("-" * 100)
    print(f"Trades : {trades_path}")
    print(f"Summary: {summary_path}")
    print(f"Executed: {int(global_row['executed_trades']):,}")
    print(f"Winrate: {float(global_row['winrate']):.2f}%")
    print(f"ROI: {float(global_row['roi_pct']):.2f}%")
    print(f"Final deposit: {float(global_row['final_deposit']):.2f}")
    print("=" * 100)

    del candidates
    gc.collect()

    return trades_df, summary_df


def run_all():
    ensure_results_dir()

    summaries = []

    for strategy in STRATEGIES:
        _, summary_df = run_one(strategy)
        summaries.append(summary_df)

    compare_df = pd.concat(summaries, ignore_index=True)
    compare_path = RESULTS_DIR / "manual_backtest_short_lab_compare_summary.csv"
    compare_df.to_csv(compare_path, index=False)

    global_compare = compare_df[compare_df["scope"] == "GLOBAL"].copy()

    print("\n" + "=" * 100)
    print("SHORT STRATEGY LAB COMPARE SUMMARY")
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
        cols = [c for c in cols if c in global_compare.columns]
        global_compare = global_compare.sort_values("roi_pct", ascending=False)
        print(global_compare[cols].to_string(index=False))

    print("-" * 100)
    print(f"Compare file: {compare_path}")
    print("=" * 100)


if __name__ == "__main__":
    run_all()
