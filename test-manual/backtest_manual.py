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

ENTRY_TIMEFRAMES = [
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
]

# Тестуємо тільки long_breakout_rsi14_ema_volume + близькі варіації
STRATEGY_MODES = [
    # База
    "long_breakout_base",

    # Невеликі правки RSI
    "long_breakout_rsi65",
    "long_breakout_rsi60_75",

    # Невеликі правки EMA distance
    "long_breakout_ema_1_3",
    "long_breakout_ema_15_3",

    # Невеликі правки volume
    "long_breakout_volume_15",
    "long_breakout_volume_20",

    # Додаємо ATR як фільтр перегрітості
    "long_breakout_atr_05_12",
    "long_breakout_atr_under_15",

    # Комбіновані сильніші варіанти
    "long_breakout_combo_balanced",
    "long_breakout_combo_strict",
]

INITIAL_DEPOSIT = 1000.0
ENTRY_DEPOSIT_PCT = 0.01
LEVERAGE = 1.0

# Поки 0 для чистого порівняння.
# Потім поставимо 0.0004
FEE_RATE = 0.0
SLIPPAGE_PCT = 0.0

STOP_LOSS_PCT = 0.02

TP_LEVELS = [0.01, 0.02, 0.03, 0.04]
TP_PARTS = [0.25, 0.25, 0.25, 0.25]

MOVE_SL_TO_BREAKEVEN_AFTER_TP_HITS = 1

ONE_OPEN_TRADE_PER_SYMBOL = True
MAX_OPEN_TRADES: int | None = None

SAVE_INDICATOR_SNAPSHOT = True
MAX_SIGNALS_PER_FILE: int | None = None

# Indicator columns
RSI7_COL = "rsi_7"
RSI14_COL = "rsi_14"
RSI21_COL = "rsi_21"

ADX14_COL = "adx_14"
ADX21_COL = "adx_21"

ATR14_PCT_COL = "atr_14_pct"

DIST_EMA50_COL = "dist_to_ema_50_pct"
DIST_VWAP100_COL = "dist_to_vwap_rolling_100_pct"

VOLUME_RATIO20_COL = "volume_ratio_sma_20"

BB_WIDTH20_COL = "bb_width_pct_20"
DONCHIAN_POS100_COL = "donchian_position_100"


# =========================
# HELPERS
# =========================

def ms_to_dt(ms: int | float | None) -> str | None:
    if ms is None or pd.isna(ms):
        return None
    return pd.to_datetime(int(ms), unit="ms", utc=True).isoformat()


def ensure_results_dir() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def load_1m_data(symbol: str) -> dict[str, np.ndarray] | None:
    path = DATA_DIR / symbol / f"{symbol}_1m.csv.gz"

    if not path.exists():
        print(f"SKIP {symbol}: missing 1m data: {path}")
        return None

    df = pd.read_csv(
        path,
        usecols=["open_time", "open", "high", "low", "close"],
    )

    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"])
    df = df.reset_index(drop=True)

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


def load_indicator_file(symbol: str, timeframe: str) -> pd.DataFrame | None:
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
        RSI7_COL,
        RSI14_COL,
        RSI21_COL,
        ADX14_COL,
        ADX21_COL,
        ATR14_PCT_COL,
        DIST_EMA50_COL,
        DIST_VWAP100_COL,
        VOLUME_RATIO20_COL,
        BB_WIDTH20_COL,
        DONCHIAN_POS100_COL,
    ]

    missing = [col for col in required if col not in df.columns]
    if missing:
        print(f"SKIP {symbol} {timeframe}: missing columns: {missing}")
        return None

    df = df[required].copy()

    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"])
    df = df.reset_index(drop=True)

    for col in required:
        if col != "datetime":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# =========================
# EXIT SIMULATION ON 1M
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


def simulate_exit_long(
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
        high = highs[i]
        low = lows[i]

        # Conservative: якщо в 1m свічці був і SL, і TP — рахуємо SL першим
        if low <= current_sl:
            realized_price_pnl_decimal += remaining * ((current_sl - entry_price) / entry_price)
            reason = "SL" if tp_hits == 0 else f"TP{tp_hits}_THEN_SL_BE"
            closed_parts.append(f"SL:{remaining:.2f}@{current_sl:.8f}")

            return {
                "exit_time_ms": int(open_times[i]),
                "exit_price": float(current_sl),
                "exit_reason": reason,
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                "closed_parts": "|".join(closed_parts),
            }

        while tp_hits < len(tp_prices) and high >= tp_prices[tp_hits]:
            part = TP_PARTS[tp_hits]
            tp_price = tp_prices[tp_hits]

            realized_price_pnl_decimal += part * ((tp_price - entry_price) / entry_price)
            remaining -= part
            tp_hits += 1

            closed_parts.append(f"TP{tp_hits}:{part:.2f}@{tp_price:.8f}")

            if tp_hits >= MOVE_SL_TO_BREAKEVEN_AFTER_TP_HITS:
                current_sl = entry_price

            if remaining <= 1e-12:
                return {
                    "exit_time_ms": int(open_times[i]),
                    "exit_price": float(tp_price),
                    "exit_reason": "TP_ALL",
                    "tp_hits": tp_hits,
                    "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                    "closed_parts": "|".join(closed_parts),
                }

        if tp_hits >= MOVE_SL_TO_BREAKEVEN_AFTER_TP_HITS and low <= current_sl:
            realized_price_pnl_decimal += remaining * ((current_sl - entry_price) / entry_price)
            closed_parts.append(f"BE:{remaining:.2f}@{current_sl:.8f}")

            return {
                "exit_time_ms": int(open_times[i]),
                "exit_price": float(current_sl),
                "exit_reason": f"TP{tp_hits}_THEN_BE",
                "tp_hits": tp_hits,
                "weighted_price_pnl_decimal": realized_price_pnl_decimal,
                "closed_parts": "|".join(closed_parts),
            }

    last_close = closes[-1]
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

    result = simulate_exit_long(one_min, entry_idx, entry_price)

    result["entry_time_ms"] = entry_time_ms
    result["entry_price"] = entry_price
    result["hold_minutes"] = (result["exit_time_ms"] - entry_time_ms) / 60_000

    return result


# =========================
# SIGNALS
# =========================

def build_entry_mask(df: pd.DataFrame, strategy_mode: str) -> pd.Series:
    rsi14 = df[RSI14_COL]
    atr14_pct = df[ATR14_PCT_COL]
    dist_ema50_abs = df[DIST_EMA50_COL].abs()
    volume_ratio = df[VOLUME_RATIO20_COL]

    valid = (
        rsi14.notna()
        & atr14_pct.notna()
        & dist_ema50_abs.notna()
        & volume_ratio.notna()
    )

    mask = pd.Series(False, index=df.index)

    # =========================
    # BASE
    # Було:
    # RSI14 >= 60
    # |dist_to_ema50| >= 1.5
    # volume_ratio >= 1.2
    # =========================

    if strategy_mode == "long_breakout_base":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.2)
        )

    # =========================
    # RSI tweaks
    # =========================

    elif strategy_mode == "long_breakout_rsi65":
        mask = (
            valid
            & (rsi14 >= 65)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.2)
        )

    elif strategy_mode == "long_breakout_rsi60_75":
        mask = (
            valid
            & (rsi14 >= 60)
            & (rsi14 <= 75)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.2)
        )

    # =========================
    # EMA distance tweaks
    # =========================

    elif strategy_mode == "long_breakout_ema_1_3":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.0)
            & (dist_ema50_abs <= 3.0)
            & (volume_ratio >= 1.2)
        )

    elif strategy_mode == "long_breakout_ema_15_3":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (dist_ema50_abs <= 3.0)
            & (volume_ratio >= 1.2)
        )

    # =========================
    # Volume tweaks
    # =========================

    elif strategy_mode == "long_breakout_volume_15":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.5)
        )

    elif strategy_mode == "long_breakout_volume_20":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 2.0)
        )

    # =========================
    # ATR tweaks
    # =========================

    elif strategy_mode == "long_breakout_atr_05_12":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.2)
            & (atr14_pct >= 0.5)
            & (atr14_pct <= 1.2)
        )

    elif strategy_mode == "long_breakout_atr_under_15":
        mask = (
            valid
            & (rsi14 >= 60)
            & (dist_ema50_abs >= 1.5)
            & (volume_ratio >= 1.2)
            & (atr14_pct <= 1.5)
        )

    # =========================
    # Combined variants
    # =========================

    elif strategy_mode == "long_breakout_combo_balanced":
        mask = (
            valid
            & (rsi14 >= 60)
            & (rsi14 <= 75)
            & (dist_ema50_abs >= 1.5)
            & (dist_ema50_abs <= 3.0)
            & (volume_ratio >= 1.5)
            & (atr14_pct <= 1.5)
        )

    elif strategy_mode == "long_breakout_combo_strict":
        mask = (
            valid
            & (rsi14 >= 65)
            & (rsi14 <= 75)
            & (dist_ema50_abs >= 1.5)
            & (dist_ema50_abs <= 3.0)
            & (volume_ratio >= 2.0)
            & (atr14_pct >= 0.5)
            & (atr14_pct <= 1.2)
        )

    else:
        raise ValueError(f"Unknown strategy_mode: {strategy_mode}")

    return mask.fillna(False)


def make_indicator_snapshot(row: pd.Series) -> dict[str, Any]:
    if not SAVE_INDICATOR_SNAPSHOT:
        return {}

    snapshot = {}

    for col, value in row.items():
        key = f"ind_{col}"
        snapshot[key] = value

    return snapshot


def collect_candidates_for_file(
    symbol: str,
    timeframe: str,
    one_min: dict[str, np.ndarray],
    strategy_mode: str,
) -> list[dict[str, Any]]:
    df = load_indicator_file(symbol, timeframe)

    if df is None or df.empty:
        return []

    mask = build_entry_mask(df, strategy_mode)
    indices = np.where(mask.to_numpy())[0]

    if MAX_SIGNALS_PER_FILE is not None:
        indices = indices[:MAX_SIGNALS_PER_FILE]

    candidates: list[dict[str, Any]] = []

    for idx in tqdm(
        indices,
        desc=f"{strategy_mode} {symbol} {timeframe}",
        leave=False,
    ):
        row = df.iloc[int(idx)]

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
            "side": "long",
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
            "entry_rule": strategy_mode,
        }

        base.update(make_indicator_snapshot(row))
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
# CAPITAL SIMULATION
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


def apply_capital_management(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deposit_state = {"deposit": INITIAL_DEPOSIT}

    active_trades: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []

    skipped_same_symbol = 0
    skipped_max_open = 0

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

        margin_usd = deposit_before * ENTRY_DEPOSIT_PCT
        notional_usd = margin_usd * LEVERAGE

        gross_pnl_usd = notional_usd * float(candidate["weighted_price_pnl_decimal"])
        fees_usd = notional_usd * FEE_RATE * 2.0
        pnl_usd = gross_pnl_usd - fees_usd

        trade = dict(candidate)

        trade["deposit_before_entry"] = deposit_before
        trade["entry_margin_usd"] = margin_usd
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

def build_summary(
    trades_df: pd.DataFrame,
    global_stats: dict[str, Any],
    strategy_mode: str,
) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(
            [
                {
                    "strategy_mode": strategy_mode,
                    "scope": "GLOBAL",
                    "symbol": "ALL",
                    "timeframe": "ALL",
                    "side": "long",
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "winrate": 0.0,
                    "total_pnl_usd": 0.0,
                    "avg_pnl_usd": 0.0,
                    "avg_pnl_on_margin_pct": 0.0,
                    "avg_tp_hits": 0.0,
                    "avg_hold_minutes": 0.0,
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

    trades_df = trades_df.copy()

    trades_df["is_win"] = pd.to_numeric(trades_df["is_win"], errors="coerce").fillna(0).astype(int)
    trades_df["pnl_usd"] = pd.to_numeric(trades_df["pnl_usd"], errors="coerce").fillna(0.0)
    trades_df["hold_minutes"] = pd.to_numeric(trades_df["hold_minutes"], errors="coerce")
    trades_df["pnl_on_margin_pct"] = pd.to_numeric(trades_df["pnl_on_margin_pct"], errors="coerce")

    rows = []

    total_trades = len(trades_df)
    total_wins = int(trades_df["is_win"].sum())
    total_losses = total_trades - total_wins

    equity = trades_df["deposit_after_exit"].astype(float)
    peak = equity.cummax()
    drawdown = (equity - peak) / peak.replace(0, np.nan) * 100.0
    max_drawdown_pct = float(drawdown.min()) if len(drawdown) else 0.0

    rows.append(
        {
            "strategy_mode": strategy_mode,
            "scope": "GLOBAL",
            "symbol": "ALL",
            "timeframe": "ALL",
            "side": "long",
            "trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "winrate": total_wins / total_trades * 100.0 if total_trades else 0.0,
            "total_pnl_usd": trades_df["pnl_usd"].sum(),
            "avg_pnl_usd": trades_df["pnl_usd"].mean(),
            "avg_pnl_on_margin_pct": trades_df["pnl_on_margin_pct"].mean(),
            "avg_tp_hits": trades_df["tp_hits"].mean(),
            "avg_hold_minutes": trades_df["hold_minutes"].mean(),
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

    group_cols = ["symbol", "timeframe", "side"]
    grouped = trades_df.groupby(group_cols, dropna=False)

    for (symbol, timeframe, side), g in grouped:
        trades = len(g)
        wins = int(g["is_win"].sum())
        losses = trades - wins

        rows.append(
            {
                "strategy_mode": strategy_mode,
                "scope": "GROUP",
                "symbol": symbol,
                "timeframe": timeframe,
                "side": side,
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "winrate": wins / trades * 100.0 if trades else 0.0,
                "total_pnl_usd": g["pnl_usd"].sum(),
                "avg_pnl_usd": g["pnl_usd"].mean(),
                "avg_pnl_on_margin_pct": g["pnl_on_margin_pct"].mean(),
                "avg_tp_hits": g["tp_hits"].mean(),
                "avg_hold_minutes": g["hold_minutes"].mean(),
                "initial_deposit": np.nan,
                "final_deposit": np.nan,
                "roi_pct": np.nan,
                "max_drawdown_pct": np.nan,
                "candidates": np.nan,
                "executed_trades": np.nan,
                "skipped_same_symbol": np.nan,
                "skipped_max_open": np.nan,
            }
        )

    return pd.DataFrame(rows)


# =========================
# RUNNERS
# =========================

def run_single_backtest(strategy_mode: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_results_dir()

    print("=" * 100)
    print(f"LONG BREAKOUT TEST: {strategy_mode.upper()}")
    print(f"Initial deposit: {INITIAL_DEPOSIT}")
    print(f"Entry deposit pct: {ENTRY_DEPOSIT_PCT * 100:.2f}%")
    print(f"Leverage: {LEVERAGE}x")
    print(f"Fee rate: {FEE_RATE}")
    print(f"TP levels: {TP_LEVELS}")
    print(f"SL: {STOP_LOSS_PCT * 100:.2f}%")
    print(f"Entry TFs: {ENTRY_TIMEFRAMES}")
    print(f"Exit TF: 1m")
    print("=" * 100)

    candidates = collect_all_candidates(strategy_mode)

    print("=" * 100)
    print(f"{strategy_mode.upper()} total candidates: {len(candidates):,}")

    executed_trades, global_stats = apply_capital_management(candidates)

    trades_df = pd.DataFrame(executed_trades)
    summary_df = build_summary(trades_df, global_stats, strategy_mode)

    trades_path = RESULTS_DIR / f"manual_backtest_longbreakout_{strategy_mode}_trades.csv"
    summary_path = RESULTS_DIR / f"manual_backtest_longbreakout_{strategy_mode}_summary.csv"

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
    print(f"Initial deposit: {float(global_row['initial_deposit']):.2f}")
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

    compare_path = RESULTS_DIR / "manual_backtest_longbreakout_compare_summary.csv"
    compare_df.to_csv(compare_path, index=False)

    global_compare = compare_df[compare_df["scope"] == "GLOBAL"].copy()

    print("\n" + "=" * 100)
    print("LONG BREAKOUT COMPARE SUMMARY")
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