# -*- coding: utf-8 -*-
"""
backtest/check_models.py

Префлайт-перевірка перед бектестом:
  • Переконуємось, що є parquet для 1m (виходи) та для всіх обраних TF по кожному символу.
  • Для КОЖНОЇ з 7 моделей × КОЖНОГО TF × (long & short) є рівно 15 варіантів сигналів.
  • Підтримуються формати сигналів:
      - separate: backtest/signals/<MODEL>/<SYMBOL>_<TF>_<SIDE>_V<K>.csv
      - unified:  backtest/signals/<MODEL>/<SYMBOL>_<TF>.csv  (колонки side, variant_k)
Якщо щось відсутнє/порожнє/некоректне — виводимо список і завершуємо з кодом 2.
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path
from typing import List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "backtest" / "data"
SIGNALS_DIR = ROOT / "backtest" / "signals"

EXPECTED_MODELS = ["CNN","LSTM","DQN","PPO","QLearning","Transformer","XGBoost"]
REQUIRED_VARIANTS = 15

def _str2bool(x: str) -> bool:
    return str(x).lower() in ("1","true","t","yes","y","on")

def _parquet_path(symbol: str, tf: str) -> Path:
    return DATA_DIR / symbol / f"{tf}.parquet"

def _csv_has_valid_signal(p: Path) -> Tuple[bool, str]:
    """Перевіряє, що CSV існує, не порожній, містить close_time і ≥1 валідний рядок."""
    if not p.exists():
        return False, "немає файлу"
    try:
        if p.stat().st_size == 0:
            return False, "порожній файл (0 байт)"
        df = pd.read_csv(p)
        if "close_time" not in df.columns:
            lc = {c.lower(): c for c in df.columns}
            cand = lc.get("close_time") or lc.get("closetime") or lc.get("t") or lc.get("timestamp")
            if cand:
                df.rename(columns={cand: "close_time"}, inplace=True)
        if "close_time" not in df.columns:
            return False, "немає колонки close_time"
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")
        df = df.dropna(subset=["close_time"])
        if df.empty:
            return False, "немає валідних рядків (close_time NaN/порожньо)"
    except Exception as e:
        return False, f"помилка читання: {type(e).__name__}: {e}"
    return True, ""

def _unified_csv_has_variant(p: Path, side: str, k: int) -> Tuple[bool, str]:
    """Для unified-формату: є рядок з (side, variant_k=k) і валідним close_time."""
    if not p.exists():
        return False, "немає файлу"
    try:
        if p.stat().st_size == 0:
            return False, "порожній файл (0 байт)"
        df = pd.read_csv(p)
        if "close_time" not in df.columns:
            lc = {c.lower(): c for c in df.columns}
            cand = lc.get("close_time") or lc.get("closetime") or lc.get("t") or lc.get("timestamp")
            if cand:
                df.rename(columns={cand: "close_time"}, inplace=True)
        if "close_time" not in df.columns:
            return False, "немає колонки close_time"
        if "side" not in df.columns or "variant_k" not in df.columns:
            return False, "немає обов'язкових колонок side та/або variant_k"
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")
        df["variant_k"] = pd.to_numeric(df["variant_k"], errors="coerce")
        df = df.dropna(subset=["close_time","variant_k"])
        if df.empty:
            return False, "немає валідних рядків (close_time/variant_k NaN)"
        sub = df[(df["side"].str.lower()==side.lower()) & (df["variant_k"].astype(int)==int(k))]
        if sub.empty:
            return False, f"немає рядка для side={side}, variant_k={k}"
    except Exception as e:
        return False, f"помилка читання: {type(e).__name__}: {e}"
    return True, ""

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ALL", help="Список символів через кому або 'ALL' (твої 20 монет)")
    ap.add_argument("--timeframes", default="15m,30m,1h,4h,1d", help="TF без 1m (1m використовується лише для виходів)")
    ap.add_argument("--signals_mode", default="separate", choices=["separate","unified"],
                    help="Формат сигналів: окремі файли V<K> або один файл з колонкою variant_k")
    ap.add_argument("--strict_1m", default="true", help="Вимагати 1m parquet для кожного символу (true/false)")
    return ap.parse_args()

DEFAULT_SYMBOLS_20 = [
    "ADAUSDT","ATOMUSDT","BATUSDT","BCHUSDT","BNBUSDT","BTCUSDT","DASHUSDT",
    "ETCUSDT","ETHUSDT","IOTAUSDT","LINKUSDT","LTCUSDT","ONTUSDT","TRXUSDT",
    "VETUSDT","XLMUSDT","XMRUSDT","XRPUSDT","XTZUSDT","ZECUSDT"
]

def main():
    args = parse_args()
    strict_1m = _str2bool(args.strict_1m)

    symbols = DEFAULT_SYMBOLS_20 if args.symbols.strip().upper()=="ALL" else \
              [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip() and t.strip()!="1m"]
    models = EXPECTED_MODELS[:]         # фіксуємо склад з 7 моделей
    sides = ["long","short"]
    kmax = REQUIRED_VARIANTS            # 15 варіантів фіксовано

    problems: List[str] = []

    # 1) Перевірка parquet (1m + обрані TF)
    for sym in symbols:
        p1m = _parquet_path(sym, "1m")
        if strict_1m and (not p1m.exists() or p1m.stat().st_size == 0):
            problems.append(f"[DATA] {sym} 1m: parquet відсутній або порожній -> {p1m}")
        for tf in tfs:
            ptf = _parquet_path(sym, tf)
            if not ptf.exists() or ptf.stat().st_size == 0:
                problems.append(f"[DATA] {sym} {tf}: parquet відсутній або порожній -> {ptf}")

    # 2) Сигнали: для КОЖНОЇ моделі × КОЖНОГО TF × (long & short) — 15 варіантів
    for m in models:
        for sym in symbols:
            for tf in tfs:
                for side in sides:
                    ok_count = 0
                    for k in range(1, kmax+1):
                        if args.signals_mode == "separate":
                            p = SIGNALS_DIR / m / f"{sym}_{tf}_{side}_V{k}.csv"
                            ok, why = _csv_has_valid_signal(p)
                            if ok:
                                ok_count += 1
                            else:
                                problems.append(f"[SIGNAL] {m} {sym} {tf} {side} V{k}: {why} -> {p}")
                        else:
                            p = SIGNALS_DIR / m / f"{sym}_{tf}.csv"
                            ok, why = _unified_csv_has_variant(p, side, k)
                            if ok:
                                ok_count += 1
                            else:
                                problems.append(f"[SIGNAL] {m} {sym} {tf} {side} V{k}: {why} -> {p}")

                    if ok_count != kmax:
                        problems.append(f"[SIGNAL] {m} {sym} {tf} {side}: знайдено {ok_count}/{kmax} варіантів")

    if problems:
        print("\n❌ Префлайт-перевірка НЕ пройдена. Знайдені проблеми:")
        for line in problems:
            print(" •", line)
        print("\nЗупинка виконання. Виправ файли/дані та запусти знову.")
        sys.exit(2)

    print("✅ Префлайт-перевірка пройдена: усі 7 моделей × усі TF × long/short мають по 15 варіантів, parquet на місці.")

if __name__ == "__main__":
    main()
