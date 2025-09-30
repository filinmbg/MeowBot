# -*- coding: utf-8 -*-
"""
backtest/backtest_all_in_one.py

Бектест по монетах та TF (крім 1m) з перевіркою виходів на 1m барах.

Важливе:
 • ВХОДИ — ЛИШЕ з підготовлених файлів сигналів (без автогенерації):
      separate: backtest/signals/<MODEL>/<SYMBOL>_<TF>_<SIDE>_V<K>.csv
      unified:  backtest/signals/<MODEL>/<SYMBOL>_<TF>.csv  (колонки side, variant_k)
 • "Варіанти" k = 1..15: на КОЖНУ модель і КОЖЕН side (long/short) беремо рівно ОДИН сигнал для V<K>.
 • TP/SL:
      TP1 = +1% → закриваємо 50% і ПЕРЕНОСИМО SL у BE (ціна входу)
      TP2 = +2% → закриваємо 70% від залишку
      TP3 = +3% → закриваємо весь залишок
      SL  = -1% (змінюється прапорцем --sl)
 • Вихід тільки по 1m барах.
 • trades.csv — усі трейди; summary.csv — зведення по монетах.
"""

from __future__ import annotations
import sys
import argparse
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "backtest" / "data"
SIGNALS_DIR = ROOT / "backtest" / "signals"
RESULTS_DIR = ROOT / "backtest" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED_MODELS = ["CNN","LSTM","DQN","PPO","QLearning","Transformer","XGBoost"]

DEFAULT_TIMEFRAMES = ["15m","30m","1h","4h","1d"]
DEFAULT_SYMBOLS_20 = [
    "ADAUSDT","ATOMUSDT","BATUSDT","BCHUSDT","BNBUSDT","BTCUSDT","DASHUSDT",
    "ETCUSDT","ETHUSDT","IOTAUSDT","LINKUSDT","LTCUSDT","ONTUSDT","TRXUSDT",
    "VETUSDT","XLMUSDT","XMRUSDT","XRPUSDT","XTZUSDT","ZECUSDT"
]

REQUIRED_VARIANTS = 15  # фіксовано

def _parquet_path(symbol: str, tf: str) -> Path:
    return DATA_DIR / symbol / f"{tf}.parquet"

def _ensure_df(df: pd.DataFrame) -> pd.DataFrame:
    need = {"open_time","close_time","open","high","low","close","volume"}
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"Parquet не має колонок {miss}")
    return df.sort_values("close_time").reset_index(drop=True)

def _load_parquet(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    p = _parquet_path(symbol, tf)
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        df = pd.read_parquet(p)
        return _ensure_df(df)
    except Exception as e:
        print(f"[SKIP] {symbol} {tf}: помилка читання parquet ({type(e).__name__}: {e}) -> {p}")
        return None

# ---------- Лоадери сигналів ----------

def _csv_normalize_close_time(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if "close_time" not in df.columns:
        lc = {c.lower(): c for c in df.columns}
        cand = lc.get("close_time") or lc.get("closetime") or lc.get("t") or lc.get("timestamp")
        if cand:
            df = df.rename(columns={cand: "close_time"})
        else:
            return None
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["close_time"]).astype({"close_time":"int64"})
    return df

def _load_variant_signal_separate(model: str, symbol: str, tf: str, side: str, k: int) -> Optional[pd.Series]:
    """Читає перший сигнал з файлу: backtest/signals/<MODEL>/<SYMBOL>_<TF>_<SIDE>_V<K>.csv"""
    p = SIGNALS_DIR / model / f"{symbol}_{tf}_{side}_V{k}.csv"
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(p)
        df = _csv_normalize_close_time(df)
        if df is None or df.empty:
            return None
        df = df.sort_values("close_time")
        return df.iloc[0]
    except Exception:
        return None

def _load_variant_signal_unified(model: str, symbol: str, tf: str, side: str, k: int) -> Optional[pd.Series]:
    """Читає перший сигнал з об’єднаного CSV: backtest/signals/<MODEL>/<SYMBOL>_<TF>.csv (є side, variant_k)."""
    p = SIGNALS_DIR / model / f"{symbol}_{tf}.csv"
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(p)
        df = _csv_normalize_close_time(df)
        if df is None or df.empty:
            return None
        if "side" not in df.columns or "variant_k" not in df.columns:
            return None
        df["variant_k"] = pd.to_numeric(df["variant_k"], errors="coerce").astype("Int64")
        df = df.dropna(subset=["variant_k"]).astype({"variant_k":"int64"})
        sub = df[(df["side"].str.lower()==side.lower()) & (df["variant_k"]==int(k))]
        if sub.empty:
            return None
        sub = sub.sort_values("close_time")
        return sub.iloc[0]
    except Exception:
        return None

def _find_entry_price_at_time(df_tf: pd.DataFrame, close_time_ms: int) -> float:
    row = df_tf[df_tf["close_time"] == close_time_ms]
    if not row.empty:
        return float(row["close"].iloc[0])
    prev = df_tf[df_tf["close_time"] <= close_time_ms].tail(1)
    if not prev.empty:
        return float(prev["close"].iloc[0])
    return float(df_tf["close"].iloc[0])

# ---------- Екзит на 1m з BE після TP1 ----------

def _simulate_exit_on_1m(df_1m: pd.DataFrame, entry_time_ms: int, entry_price: float, side: str,
                         tp_pcts: List[float], tp_close_fracs: List[float], sl_pct: float) -> Tuple[float,int,str,float]:
    """
    Повертає: (realized_pnl_pct, exit_time_ms, exit_reason, mae_pct)
    • TP1 => переносимо SL у беззбиток (entry_price) для залишку позиції.
    """
    qty_total = 1.0
    remain = qty_total
    realized = 0.0

    if side == "long":
        tp_levels = [entry_price * (1.0 + x) for x in tp_pcts]
        sl_level = entry_price * (1.0 - sl_pct)
    else:
        tp_levels = [entry_price * (1.0 - x) for x in tp_pcts]
        sl_level = entry_price * (1.0 + sl_pct)

    mae_pct = 0.0
    path = df_1m[df_1m["close_time"] > entry_time_ms].copy()
    if path.empty:
        return 0.0, entry_time_ms, "NO_DATA", 0.0

    hit = [False, False, False]
    exit_time_ms = int(path["close_time"].iloc[-1])
    exit_reason = "END"

    for _, r in path.iterrows():
        high = float(r["high"]); low = float(r["low"]); t = int(r["close_time"])

        if side == "long":
            mae_pct = min(mae_pct, (low / entry_price - 1.0) * 100.0)
        else:
            mae_pct = min(mae_pct, (entry_price / high - 1.0) * 100.0)

        # SL
        if side == "long" and low <= sl_level and remain > 0:
            realized += remain * ((sl_level / entry_price) - 1.0) * 100.0
            remain = 0.0; exit_time_ms = t; exit_reason = "SL"; break
        if side == "short" and high >= sl_level and remain > 0:
            realized += remain * ((entry_price / sl_level) - 1.0) * 100.0
            remain = 0.0; exit_time_ms = t; exit_reason = "SL"; break

        # TP
        for i, level in enumerate(tp_levels):
            if hit[i]:
                continue
            if side == "long" and high >= level and remain > 0:
                close_frac = tp_close_fracs[i] if i < len(tp_close_fracs) else 1.0
                qty = min(remain, remain * close_frac)
                realized += qty * ((level / entry_price) - 1.0) * 100.0
                remain -= qty; hit[i] = True
                if i == 0 and remain > 0:
                    sl_level = entry_price  # BE
                if remain <= 1e-9:
                    exit_time_ms = t; exit_reason = f"TP{i+1}"; break

            elif side == "short" and low <= level and remain > 0:
                close_frac = tp_close_fracs[i] if i < len(tp_close_fracs) else 1.0
                qty = min(remain, remain * close_frac)
                realized += qty * ((entry_price / level) - 1.0) * 100.0
                remain -= qty; hit[i] = True
                if i == 0 and remain > 0:
                    sl_level = entry_price  # BE
                if remain <= 1e-9:
                    exit_time_ms = t; exit_reason = f"TP{i+1}"; break
        if remain <= 1e-9:
            break

    if remain > 0:
        last_close = float(path["close"].iloc[-1])
        if side == "long":
            realized += remain * ((last_close / entry_price) - 1.0) * 100.0
        else:
            realized += remain * ((entry_price / last_close) - 1.0) * 100.0
        exit_time_ms = int(path["close_time"].iloc[-1])
        if exit_reason == "END":
            for i in reversed(range(len(hit))):
                if hit[i]:
                    exit_reason = f"TP{i+1}_PARTIAL"; break

    return realized, exit_time_ms, exit_reason, mae_pct

# ---------- Основна логіка ----------

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ALL", help="Список символів через кому або 'ALL'")
    ap.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES), help="TF без 1m (1m лише для виходів)")
    ap.add_argument("--signals_mode", default="separate", choices=["separate","unified"],
                    help="Формат сигналів: окремі V<K> файли або один файл з variant_k")
    ap.add_argument("--sl", type=float, default=0.01, help="SL у частках (0.01=1%)")
    ap.add_argument("--tp", default="0.01,0.02,0.03", help="TP рівні у частках, напр. 0.01,0.02,0.03")
    ap.add_argument("--tp_fracs", default="0.5,0.7,1.0", help="Фракції закриття на TP1..TPn")
    ap.add_argument("--outdir", default=str(RESULTS_DIR), help="Куди писати trades.csv та summary.csv")
    return ap.parse_args()

def main():
    args = parse_args()

    symbols = DEFAULT_SYMBOLS_20 if args.symbols.strip().upper()=="ALL" else \
              [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip() and t.strip()!="1m"]
    models = EXPECTED_MODELS[:]                 # фіксуємо 7 моделей
    sides = ["long","short"]
    kmax = REQUIRED_VARIANTS                    # 15

    tp_pcts = [float(x) for x in args.tp.split(",") if x.strip()]
    tp_fracs = [float(x) for x in args.tp_fracs.split(",") if x.strip()]
    sl_pct = float(args.sl)

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    # 1m для екзитів — читаємо раз на символ
    all_trades, all_summary = [], []

    for sym in symbols:
        df_1m = _load_parquet(sym, "1m")
        if df_1m is None or df_1m.empty:
            print(f"[SKIP] {sym}: немає 1m для екзитів"); continue

        trades_rows = []

        for tf in tfs:
            df_tf = _load_parquet(sym, tf)
            if df_tf is None or df_tf.empty:
                print(f"[WARN] {sym} {tf}: немає TF-даних"); continue

            for k in range(1, kmax+1):
                for m in models:
                    for side in sides:
                        # беремо рівно один сигнал для (m, sym, tf, side, V=k)
                        if args.signals_mode == "separate":
                            cand = _load_variant_signal_separate(m, sym, tf, side, k)
                        else:
                            cand = _load_variant_signal_unified(m, sym, tf, side, k)
                        if cand is None:
                            continue

                        ct = int(cand["close_time"])
                        entry_price = _find_entry_price_at_time(df_tf, ct)
                        pnl_pct, exit_t, reason, mae_pct = _simulate_exit_on_1m(
                            df_1m=df_1m, entry_time_ms=ct, entry_price=entry_price,
                            side=side, tp_pcts=tp_pcts, tp_close_fracs=tp_fracs, sl_pct=sl_pct
                        )
                        time_to_close_min = max(0, int(round((exit_t - ct) / 60_000)))

                        trades_rows.append({
                            "symbol": sym,
                            "tf": tf,
                            "model": m,
                            "variant_k": k,
                            "side": side,
                            "entry_time_ms": ct,
                            "entry_time": datetime.utcfromtimestamp(ct/1000).strftime("%Y-%m-%d %H:%M"),
                            "entry_price": round(entry_price, 8),
                            "exit_time_ms": exit_t,
                            "exit_time": datetime.utcfromtimestamp(exit_t/1000).strftime("%Y-%m-%d %H:%M"),
                            "exit_reason": reason,
                            "pnl_pct": round(pnl_pct, 5),
                            "mae_pct": round(mae_pct, 5),
                            "time_to_close_min": time_to_close_min,
                        })

        trades_df = pd.DataFrame(trades_rows)
        if trades_df.empty:
            continue

        def _is_win(row):
            return row["exit_reason"].startswith("TP") or (row["pnl_pct"] > 0.0)

        trades_df["win"] = trades_df.apply(_is_win, axis=1)
        grp = trades_df.groupby("symbol", as_index=False).agg(
            trades=("pnl_pct","count"),
            wins=("win","sum"),
            losses=("win", lambda s: int((~s).sum())),
            winrate=("win", lambda s: round(100.0*float(s.sum())/max(1,len(s)), 2)),
            avg_pnl=("pnl_pct","mean"),
            total_pnl=("pnl_pct","sum"),
        )
        all_trades.append(trades_df); all_summary.append(grp)

    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(
        columns=["symbol","tf","model","variant_k","side","entry_time_ms","entry_time","entry_price",
                 "exit_time_ms","exit_time","exit_reason","pnl_pct","mae_pct","time_to_close_min","win"]
    )
    summary_df = pd.concat(all_summary, ignore_index=True) if all_summary else pd.DataFrame(
        columns=["symbol","trades","wins","losses","winrate","avg_pnl","total_pnl"]
    )

    trades_csv = outdir / "trades.csv"
    summary_csv = outdir / "summary.csv"
    trades_df.to_csv(trades_csv, index=False)
    summary_df.to_csv(summary_csv, index=False)

    print(f"\n[WRITE] {summary_csv} ({len(summary_df)} рядків)")
    print(f"[WRITE] {trades_csv} ({len(trades_df)} рядків)")
    print("Готово ✅")

if __name__ == "__main__":
    main()
