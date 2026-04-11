from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


START_BALANCE = 100000.0
STAKE_PCT = 0.01
LEVERAGE = 10
FEE_RATE = 0.0004
LOSS_COOLDOWN_MS = 3 * 60 * 60 * 1000
MAX_M1_BARS_PER_TRADE = 4320

SL_PCT = 0.02
TP_STEP_PCT = 0.005
SL_TRAIL_STEP_PCT = 0.0002
TRAIL_EVERY_MS = 15 * 60 * 1000

LOW_LEVELS = [20, 25, 30, 35, 40, 45]
RECLAIM_LEVELS = [40, 45, 50, 55, 60]
LOOKBACKS = [3, 5, 8, 10, 14]


@dataclass
class StrategyDef:
    strategy_id: str
    side: str
    low_level: int
    reclaim_level: int
    lookback: int


@dataclass
class TradeResult:
    strategy_id: str
    side: str
    tf: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    tp_hit_count: int
    realized_pnl_usd: float
    gross_pnl_usd: float
    fees_usd: float
    win: int
    stake_usd: float
    qty: float


def build_strategies() -> list[StrategyDef]:
    strategies: list[StrategyDef] = []
    idx = 1

    for low in LOW_LEVELS:
        for reclaim in RECLAIM_LEVELS:
            if reclaim <= low:
                continue
            for lookback in LOOKBACKS:
                sid = f"RSI_REBOUND_ST_{idx:03d}"
                strategies.append(
                    StrategyDef(
                        strategy_id=sid,
                        side="LONG",
                        low_level=low,
                        reclaim_level=reclaim,
                        lookback=lookback,
                    )
                )
                idx += 1

    return strategies


STRATEGIES = build_strategies()


def now_str(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def calc_stake_usd(balance: float) -> float:
    return balance * STAKE_PCT


def calc_qty(entry_price: float, stake_usd: float) -> float:
    return (stake_usd * LEVERAGE) / entry_price


def calc_gross_pnl(side: str, entry_price: float, exit_price: float, qty: float) -> float:
    if side == "LONG":
        return (exit_price - entry_price) * qty * LEVERAGE
    return (entry_price - exit_price) * qty * LEVERAGE


def calc_fee_usd(price: float, qty: float) -> float:
    notional = price * qty * LEVERAGE
    return notional * FEE_RATE


def calc_net_reduce_pnl(side: str, entry_price: float, exit_price: float, qty: float) -> tuple[float, float, float]:
    gross = calc_gross_pnl(side, entry_price, exit_price, qty)
    fee = calc_fee_usd(exit_price, qty)
    net = gross - fee
    return net, gross, fee


def build_entries(tf_df: pd.DataFrame, strategy: StrategyDef) -> pd.DataFrame:
    work = tf_df.copy()

    rolling_min_col = f"rsi14_min_prev_{strategy.lookback}"
    work[rolling_min_col] = work["rsi14"].shift(1).rolling(strategy.lookback).min()

    mask = (
        (work["supertrend_bullish"] == 1) &
        (work[rolling_min_col] <= strategy.low_level) &
        (work["rsi14"] >= strategy.reclaim_level)
    )

    entries = work.loc[mask, ["open_time", "close_time", "close", "rsi14", rolling_min_col]].copy()
    entries["strategy_id"] = strategy.strategy_id
    entries["side"] = strategy.side
    entries["low_level"] = strategy.low_level
    entries["reclaim_level"] = strategy.reclaim_level
    entries["lookback"] = strategy.lookback

    return entries.reset_index(drop=True)


def find_first_m1_index_after_close(m1_close_times: pd.Series, ts_ms: int) -> Optional[int]:
    idx = m1_close_times.searchsorted(ts_ms + 1, side="left")
    if idx >= len(m1_close_times):
        return None
    return int(idx)


def simulate_trade_on_m1(
    m1_df: pd.DataFrame,
    m1_close_times: pd.Series,
    side: str,
    entry_price: float,
    entry_tf_close_time: int,
    qty: float,
) -> Optional[tuple[int, float, str, int, float, float, float]]:
    start_idx = find_first_m1_index_after_close(m1_close_times, entry_tf_close_time)
    if start_idx is None:
        return None

    end_idx = min(len(m1_df), start_idx + MAX_M1_BARS_PER_TRADE)
    m1_slice = m1_df.iloc[start_idx:end_idx]

    qty_remaining = qty
    tp_hit_count = 0

    realized_net_pnl = 0.0
    realized_gross_pnl = 0.0
    realized_fees = 0.0

    entry_fee = calc_fee_usd(entry_price, qty)
    realized_net_pnl -= entry_fee
    realized_fees += entry_fee

    if side == "LONG":
        sl_price = entry_price * (1 - SL_PCT)
        tp1 = entry_price * (1 + TP_STEP_PCT)
        tp2 = entry_price * (1 + TP_STEP_PCT * 2)
        tp3 = entry_price * (1 + TP_STEP_PCT * 3)
        tp4 = entry_price * (1 + TP_STEP_PCT * 4)
    else:
        sl_price = entry_price * (1 + SL_PCT)
        tp1 = entry_price * (1 - TP_STEP_PCT)
        tp2 = entry_price * (1 - TP_STEP_PCT * 2)
        tp3 = entry_price * (1 - TP_STEP_PCT * 3)
        tp4 = entry_price * (1 - TP_STEP_PCT * 4)

    last_trail_step = 0

    for _, bar in m1_slice.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])
        close_time = int(bar["close_time"])

        if side == "LONG":
            if tp_hit_count == 0 and high >= tp1:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp1, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 1
                sl_price = entry_price * (1 + SL_TRAIL_STEP_PCT)
                last_trail_step = 1

            if tp_hit_count == 1 and high >= tp2:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp2, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 2

            if tp_hit_count == 2 and high >= tp3:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp3, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 3

            if tp_hit_count == 3 and high >= tp4:
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp4, qty_remaining)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                return close_time, tp4, "TP4_HIT", 4, realized_net_pnl, realized_gross_pnl, realized_fees

            if tp_hit_count >= 1:
                elapsed_ms = close_time - entry_tf_close_time
                trail_steps = 1 + (elapsed_ms // TRAIL_EVERY_MS)
                if trail_steps > last_trail_step:
                    new_sl = entry_price * (1 + SL_TRAIL_STEP_PCT * trail_steps)
                    if new_sl > sl_price:
                        sl_price = new_sl
                    last_trail_step = int(trail_steps)

            if low <= sl_price:
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, sl_price, qty_remaining)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                return close_time, sl_price, "SL_HIT", tp_hit_count, realized_net_pnl, realized_gross_pnl, realized_fees

        else:
            if tp_hit_count == 0 and low <= tp1:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp1, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 1
                sl_price = entry_price * (1 - SL_TRAIL_STEP_PCT)
                last_trail_step = 1

            if tp_hit_count == 1 and low <= tp2:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp2, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 2

            if tp_hit_count == 2 and low <= tp3:
                qty_cut = qty * 0.25
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp3, qty_cut)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                qty_remaining -= qty_cut
                tp_hit_count = 3

            if tp_hit_count == 3 and low <= tp4:
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, tp4, qty_remaining)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                return close_time, tp4, "TP4_HIT", 4, realized_net_pnl, realized_gross_pnl, realized_fees

            if tp_hit_count >= 1:
                elapsed_ms = close_time - entry_tf_close_time
                trail_steps = 1 + (elapsed_ms // TRAIL_EVERY_MS)
                if trail_steps > last_trail_step:
                    new_sl = entry_price * (1 - SL_TRAIL_STEP_PCT * trail_steps)
                    if new_sl < sl_price:
                        sl_price = new_sl
                    last_trail_step = int(trail_steps)

            if high >= sl_price:
                net, gross, fee = calc_net_reduce_pnl(side, entry_price, sl_price, qty_remaining)
                realized_net_pnl += net
                realized_gross_pnl += gross
                realized_fees += fee
                return close_time, sl_price, "SL_HIT", tp_hit_count, realized_net_pnl, realized_gross_pnl, realized_fees

    return None


def run_backtest_for_tf(symbol: str, tf: str, data_dir: Path, results_dir: Path) -> None:
    tf_path = data_dir / f"{symbol}_{tf}_indicators.csv.gz"
    m1_path = data_dir / f"{symbol}_1m_indicators.csv.gz"

    if not tf_path.exists():
        raise FileNotFoundError(f"TF file not found: {tf_path}")
    if not m1_path.exists():
        raise FileNotFoundError(f"1m file not found: {m1_path}")

    results_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    print(f"Loading TF data: {tf_path}", flush=True)
    tf_df = pd.read_csv(tf_path, compression="gzip").sort_values("close_time").reset_index(drop=True)

    print(f"Loading 1m data: {m1_path}", flush=True)
    m1_df = pd.read_csv(m1_path, compression="gzip").sort_values("close_time").reset_index(drop=True)
    m1_close_times = m1_df["close_time"]

    print(
        f"TF rows={len(tf_df)}  "
        f"range=[{pd.to_datetime(tf_df['close_time'].min(), unit='ms')} -> {pd.to_datetime(tf_df['close_time'].max(), unit='ms')}]",
        flush=True,
    )
    print(f"Total strategies: {len(STRATEGIES)}", flush=True)

    summary_rows = []
    trade_rows = []

    for i, strategy in enumerate(STRATEGIES, start=1):
        print(
            f"[{i}/{len(STRATEGIES)}] {strategy.strategy_id} "
            f"low<={strategy.low_level} reclaim>={strategy.reclaim_level} lookback={strategy.lookback}",
            flush=True,
        )

        entries = build_entries(tf_df, strategy)
        balance = START_BALANCE
        start_balance = balance
        next_entry_allowed_after_ms = -1
        trades: list[TradeResult] = []

        for _, entry in entries.iterrows():
            tf_close_time = int(entry["close_time"])
            if tf_close_time <= next_entry_allowed_after_ms:
                continue

            entry_price = float(entry["close"])
            stake_usd = calc_stake_usd(balance)
            qty = calc_qty(entry_price, stake_usd)

            sim = simulate_trade_on_m1(
                m1_df=m1_df,
                m1_close_times=m1_close_times,
                side=strategy.side,
                entry_price=entry_price,
                entry_tf_close_time=tf_close_time,
                qty=qty,
            )
            if sim is None:
                continue

            exit_time, exit_price, exit_reason, tp_hit_count, realized_net_pnl, realized_gross_pnl, realized_fees = sim
            balance += realized_net_pnl

            if realized_net_pnl < 0:
                next_entry_allowed_after_ms = exit_time + LOSS_COOLDOWN_MS
            else:
                next_entry_allowed_after_ms = exit_time

            trades.append(
                TradeResult(
                    strategy_id=strategy.strategy_id,
                    side=strategy.side,
                    tf=tf,
                    entry_time=tf_close_time,
                    entry_price=entry_price,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    tp_hit_count=tp_hit_count,
                    realized_pnl_usd=realized_net_pnl,
                    gross_pnl_usd=realized_gross_pnl,
                    fees_usd=realized_fees,
                    win=1 if realized_net_pnl > 0 else 0,
                    stake_usd=stake_usd,
                    qty=qty,
                )
            )

        trades_count = len(trades)
        wins = sum(t.win for t in trades)
        losses = trades_count - wins
        winrate = (wins / trades_count * 100.0) if trades_count > 0 else 0.0
        net_profit = balance - start_balance

        win_pnls = [t.realized_pnl_usd for t in trades if t.realized_pnl_usd > 0]
        loss_pnls = [t.realized_pnl_usd for t in trades if t.realized_pnl_usd < 0]

        gross_profit = sum(win_pnls)
        gross_loss_abs = abs(sum(loss_pnls))
        profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else 0.0

        avg_trade = net_profit / trades_count if trades_count > 0 else 0.0
        avg_win = (sum(win_pnls) / len(win_pnls)) if win_pnls else 0.0
        avg_loss = (sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0.0
        total_fees = sum(t.fees_usd for t in trades)

        summary_rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "side": strategy.side,
                "tf": tf,
                "low_level": strategy.low_level,
                "reclaim_level": strategy.reclaim_level,
                "lookback": strategy.lookback,
                "signals": len(entries),
                "trades": trades_count,
                "wins": wins,
                "losses": losses,
                "winrate_pct": round(winrate, 4),
                "start_balance": round(start_balance, 6),
                "final_balance": round(balance, 6),
                "net_profit_usd": round(net_profit, 6),
                "gross_profit_usd": round(gross_profit, 6),
                "gross_loss_usd": round(-gross_loss_abs, 6),
                "profit_factor": round(profit_factor, 6),
                "avg_trade_usd": round(avg_trade, 6),
                "avg_win_usd": round(avg_win, 6),
                "avg_loss_usd": round(avg_loss, 6),
                "fees_usd": round(total_fees, 6),
            }
        )

        for t in trades:
            trade_rows.append(
                {
                    "strategy_id": t.strategy_id,
                    "side": t.side,
                    "tf": t.tf,
                    "entry_time": t.entry_time,
                    "entry_price": round(t.entry_price, 6),
                    "exit_time": t.exit_time,
                    "exit_price": round(t.exit_price, 6),
                    "exit_reason": t.exit_reason,
                    "tp_hit_count": t.tp_hit_count,
                    "realized_pnl_usd": round(t.realized_pnl_usd, 6),
                    "gross_pnl_usd": round(t.gross_pnl_usd, 6),
                    "fees_usd": round(t.fees_usd, 6),
                    "stake_usd": round(t.stake_usd, 6),
                    "qty": round(t.qty, 10),
                    "win": t.win,
                }
            )

    summary_df = pd.DataFrame(summary_rows).sort_values(
        ["final_balance", "profit_factor", "winrate_pct"],
        ascending=[False, False, False]
    ).reset_index(drop=True)

    trades_df = pd.DataFrame(trade_rows)

    summary_out = results_dir / f"{symbol}_{tf}_rsi_rebound_supertrend_summary.csv"
    trades_out = results_dir / f"{symbol}_{tf}_rsi_rebound_supertrend_trades.csv"

    summary_df.to_csv(summary_out, index=False)
    trades_df.to_csv(trades_out, index=False)

    print("\nSaved summary:", summary_out, flush=True)
    print("Saved trades: ", trades_out, flush=True)
    print("Total time:   ", now_str(time.time() - started), flush=True)
    print("\nTop 20:", flush=True)
    print(summary_df.head(20).to_string(index=False), flush=True)

if __name__ == "__main__":
    symbol = "BTCUSDT"
    tf = "1h"

    data_dir = Path("test/data/BTCUSDT")
    results_dir = Path("test/results/backtest")

    run_backtest_for_tf(
        symbol=symbol,
        tf=tf,
        data_dir=data_dir,
        results_dir=results_dir,
    )