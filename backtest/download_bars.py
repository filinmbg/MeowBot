# backtest/download_bars.py
# ----------------------------------------------------------
# Завантаження OHLCV з Binance (python-binance) + індикатори.
# Зберігає ДАНІ САМЕ В: backtest/data/<SYMBOL>/...
#
# • Таймфрейми: 1m (для виходів), 15m, 30m, 1h, 4h, 1d
# • За замовчуванням: останні DOWNLOAD_YEARS років (end = "зараз")
# • --from-listing : качати від дати лістингу (onboardDate), фолбек: earliest_kline
# • --end          : обрізати завантаження до вказаної дати (інклюзивно, UTC)
# • Перевіряє вже скачані бари і:
#     - Скіпає TF, якщо покриття повне (до end_global)
#     - Дозавантажує лише «хвіст», якщо часткове покриття
# • Індикатори рахує тільки якщо були нові сирі бари або файл відсутній
# • --force — ігнорувати кеш і перекачати з нуля під обраний інтервал
# ----------------------------------------------------------
from __future__ import annotations
import os
import sys
import gzip
import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
from binance.client import Client

# --------- ЛОКАЛЬНІ НАЛАШТУВАННЯ ----------
DATA_ROOT = os.path.join("backtest", "data")
DOWNLOAD_YEARS = 2
MAIN_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]  # індикаторні ТФ

# ---- мапа інтервалів Binance ----
TF_MAP = {
    "1m":  Client.KLINE_INTERVAL_1MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "30m": Client.KLINE_INTERVAL_30MINUTE,
    "1h":  Client.KLINE_INTERVAL_1HOUR,
    "4h":  Client.KLINE_INTERVAL_4HOUR,
    "1d":  Client.KLINE_INTERVAL_1DAY,
}

# ---- тривалість свічки для розрахунків «+1 інтервал» ----
TF_DELTA = {
    "1m":  pd.Timedelta(minutes=1),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h":  pd.Timedelta(hours=1),
    "4h":  pd.Timedelta(hours=4),
    "1d":  pd.Timedelta(days=1),
}

# --------- імпорт індикаторів ---------------------------------------------
ROOT = os.path.dirname(os.path.dirname(__file__))  # корінь проекту
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

def _import_add_critical():
    try:
        from test.indicators.indicators import add_critical_indicators  # type: ignore
        return add_critical_indicators
    except Exception:
        import importlib.util, pathlib
        cand = pathlib.Path(ROOT) / "test" / "indicators" / "indicators.py"
        if not cand.exists():
            raise ImportError("Не знайдено test/indicators/indicators.py з add_critical_indicators")
        spec = importlib.util.spec_from_file_location("ind_mod", str(cand))
        ind_mod = importlib.util.module_from_spec(spec)  # type: ignore
        assert spec and spec.loader
        spec.loader.exec_module(ind_mod)                 # type: ignore
        return ind_mod.add_critical_indicators          # type: ignore

add_critical_indicators = _import_add_critical()

# ---------- утиліти прогресу ----------
def _print_bar(prefix: str, frac: float, width: int = 28):
    frac = max(0.0, min(1.0, float(frac)))
    done = int(round(width * frac))
    bar = "█" * done + "·" * (width - done)
    pct = int(frac * 100)
    print(f"\r{prefix} [{bar}] {pct:3d}% ", end="", flush=True)

def _end_bar():
    print("", flush=True)

def _save_csv_gz(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        df.to_csv(f, index=False)

def _save_parquet(df: pd.DataFrame, path: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
    except Exception:
        pass  # parquet — опційно

# ---------- I/O шляхи ----------
def _raw_csv_path(sym: str, tf: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_{tf}.csv.gz")

def _raw_parquet_path(sym: str, tf: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_{tf}.parquet")

def _m1_csv_path(sym: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_1m.csv.gz")

def _m1_parquet_path(sym: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_1m.parquet")

def _ind_csv_path(sym: str, tf: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_{tf}_critical_indicators.csv")

def _ind_parquet_path(sym: str, tf: str) -> str:
    return os.path.join(DATA_ROOT, sym, f"{sym}_{tf}_critical_indicators.parquet")

# ---------- читання існуючого покриття ----------
def _existing_range_raw(sym: str, tf: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """
    Повертає (min_time, max_time) існуючих СИРИХ свічок для TF (за open_time).
    Читає Parquet якщо є, інакше CSV.GZ (тільки потрібні колонки).
    """
    p_pq = _raw_parquet_path(sym, tf)
    p_csv = _raw_csv_path(sym, tf)
    if os.path.exists(p_pq):
        try:
            df = pd.read_parquet(p_pq, columns=["open_time"])
            if df.empty: return (None, None)
            s = pd.to_datetime(df["open_time"], utc=True)
            return (s.min(), s.max())
        except Exception:
            pass
    if os.path.exists(p_csv):
        try:
            df = pd.read_csv(p_csv, usecols=["open_time"])
            if df.empty: return (None, None)
            s = pd.to_datetime(df["open_time"], utc=True)
            return (s.min(), s.max())
        except Exception:
            pass
    return (None, None)

def _existing_range_m1(sym: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    p_pq = _m1_parquet_path(sym)
    p_csv = _m1_csv_path(sym)
    if os.path.exists(p_pq):
        try:
            df = pd.read_parquet(p_pq, columns=["open_time"])
            if df.empty: return (None, None)
            s = pd.to_datetime(df["open_time"], utc=True)
            return (s.min(), s.max())
        except Exception:
            pass
    if os.path.exists(p_csv):
        try:
            df = pd.read_csv(p_csv, usecols=["open_time"])
            if df.empty: return (None, None)
            s = pd.to_datetime(df["open_time"], utc=True)
            return (s.min(), s.max())
        except Exception:
            pass
    return (None, None)

# ---------- прогресивне завантаження батчами ----------
def _fetch_klines_progress(client: Client, symbol: str, interval: str,
                           start: datetime, end: datetime,
                           label: str) -> pd.DataFrame:
    """
    Тягнемо історію батчами (limit=1000) і малюємо відсоткову шкалу на основі close_time.
    """
    limit = 1000
    start_ms = int(start.timestamp() * 1000)
    end_ms   = int(end.timestamp() * 1000)

    cols_full = ["open_time","open","high","low","close","volume",
                 "close_time","quote_asset_volume","number_of_trades",
                 "taker_buy_base_volume","taker_buy_quote_volume","ignore"]

    all_rows = []
    cur = start_ms
    last_close_seen = start_ms

    while cur < end_ms:
        raw = client.get_klines(symbol=symbol, interval=interval,
                                startTime=cur, endTime=end_ms, limit=limit)
        if not raw:
            break
        all_rows.extend(raw)

        last_close = int(raw[-1][6])  # closeTime
        last_close_seen = max(last_close_seen, last_close)

        frac = (last_close_seen - start_ms) / max(1, (end_ms - start_ms))
        _print_bar(label, frac)

        nxt = last_close + 1
        if nxt <= cur:  # страховка
            break
        cur = nxt

        if len(raw) < limit:
            break

    _end_bar()

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=cols_full)
    num_cols = ["open","high","low","close","volume",
                "quote_asset_volume","taker_buy_base_volume","taker_buy_quote_volume"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"]  = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.dropna().sort_values("open_time").reset_index(drop=True)
    return df

def _top50_usdt_symbols(client: Client) -> list[str]:
    info = client.get_exchange_info()
    rows = []
    for s in info["symbols"]:
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
            od = int(s.get("onboardDate", 0))
            rows.append((s["symbol"], od))
    rows.sort(key=lambda x: x[1])
    return [r[0] for r in rows[:50]]

def _earliest_exchange_ts(client: Client, symbol: str, intervals: list[str]) -> datetime | None:
    ts_list = []
    for iv in intervals:
        try:
            ms = client._get_earliest_valid_timestamp(symbol, iv)
            if ms:
                ts_list.append(ms)
        except Exception:
            pass
    if not ts_list:
        return None
    ms_min = min(ts_list)
    return datetime.fromtimestamp(ms_min / 1000, tz=timezone.utc)

# ---------- merge & save ----------
def _merge_and_save_raw(sym: str, tf: str, df_new: pd.DataFrame) -> pd.DataFrame:
    """
    Зливає нові свічки з існуючими (якщо є), прибирає дублікати по open_time,
    сортує і перезаписує файли. Повертає об'єднаний DataFrame.
    """
    p_pq = _raw_parquet_path(sym, tf)
    p_csv = _raw_csv_path(sym, tf)

    df_old = None
    if os.path.exists(p_pq):
        try:
            df_old = pd.read_parquet(p_pq)
        except Exception:
            df_old = None
    if df_old is None and os.path.exists(p_csv):
        try:
            df_old = pd.read_csv(p_csv)
        except Exception:
            df_old = None

    if df_old is not None and not df_old.empty:
        out = pd.concat([df_old, df_new], ignore_index=True)
        out["open_time"] = pd.to_datetime(out["open_time"], utc=True)
        out = out.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    else:
        out = df_new.copy()

    _save_csv_gz(out, p_csv)
    _save_parquet(out, p_pq)
    return out

def _save_indicators(sym: str, tf: str, dft: pd.DataFrame):
    """
    Перерахувати індикатори на повному сирому наборі (простий і коректний варіант)
    та перезаписати файли індикаторів.
    """
    try:
        ind = add_critical_indicators(dft.copy())
        _save_csv_gz(ind, _ind_csv_path(sym, tf))
        _save_parquet(ind, _ind_parquet_path(sym, tf))
        print(f"✓ {sym} {tf} індикатори збережено ({len(ind)} рядків)")
    except Exception as e:
        print(f"❌ {sym} {tf} індикатори: {e}")

# ====================== helpers ======================
def _parse_end_date(s: str) -> pd.Timestamp:
    """
    Приймає 'YYYY-MM-DD' або 'DD.MM.YYYY' чи 'DD.MM.YY' і повертає
    кінець дня (23:59:59.999 UTC) як tz-aware Timestamp.
    """
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            dt = datetime.strptime(s, fmt)
            ts = pd.Timestamp(dt, tz=timezone.utc)
            # кінець дня інклюзивно
            return ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
        except Exception:
            continue
    raise ValueError(f"Невірний формат дати для --end: {s}")

# ===========================================================
#                          main()
# ===========================================================
def main():
    ap = argparse.ArgumentParser(description="Завантаження OHLCV з Binance + індикатори (збереження в backtest/data)")
    ap.add_argument("--symbols", default="", help="Кома-сепарований список символів SPOT (напр., BTCUSDT,ETHUSDT). Порожньо -> авто-топ50 USDT")
    ap.add_argument("--from-listing", action="store_true", help="Починати від дати лістингу (onboardDate), фолбек: earliest_kline")
    ap.add_argument("--years", type=int, default=DOWNLOAD_YEARS, help="Якщо не --from-listing: скільки років тягнути (деф: 2)")
    ap.add_argument("--force", action="store_true", help="Ігнорувати кеш та перекачати вказаний інтервал заново")
    ap.add_argument("--end", default="", help="Обрізати завантаження до цієї дати (інклюзивно). Формати: YYYY-MM-DD або DD.MM.YYYY")
    args = ap.parse_args()

    os.makedirs(DATA_ROOT, exist_ok=True)
    client = Client()

    # Кінець інтервалу (end_global): або задана дата, або "зараз"
    if args.end.strip():
        end_global = _parse_end_date(args.end)
    else:
        end_global = pd.Timestamp(datetime.now(timezone.utc))

    # Початок (коли НЕ --from-listing): end - N років
    start_global = end_global - pd.Timedelta(days=365 * max(1, args.years))

    # список монет
    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = _top50_usdt_symbols(client)

    total = len(symbols)
    print(f"🧾 Символів до завантаження: {total}")
    print("Список:", ", ".join(symbols))

    # exchangeInfo → onboardDate
    info = client.get_exchange_info()
    onboard_map = {s["symbol"]: int(s.get("onboardDate", 0)) for s in info["symbols"]}

    for idx, sym in enumerate(symbols, start=1):
        sym_dir = os.path.join(DATA_ROOT, sym)
        os.makedirs(sym_dir, exist_ok=True)

        # визначимо старт для конкретного символу
        sym_start = start_global
        src = []
        if args.from_listing:
            od = onboard_map.get(sym, 0)
            if od and od > 0:
                sym_start = datetime.fromtimestamp(od / 1000, tz=timezone.utc)
                src.append("onboardDate")
            else:
                probe = _earliest_exchange_ts(client, sym, [TF_MAP["1d"], TF_MAP["1m"]])
                if probe is not None:
                    sym_start = probe
                    src.append("earliest_kline")
                else:
                    src.append(f"last_{args.years}y")
            if sym_start > end_global:
                sym_start = end_global
        else:
            src.append(f"last_{args.years}y")

        head = f"({idx}/{total}) {sym}"
        print(f"\n{head} — базовий інтервал: {sym_start.date()} .. {pd.Timestamp(end_global).date()} (UTC) [{', '.join(src)}]")

        # ============ 1) 1m (для виходів) ============
        m1_min, m1_max = (None, None) if args.force else _existing_range_m1(sym)
        if not args.force and m1_max is not None and m1_max >= end_global - TF_DELTA["1m"]:
            print(f"{head}  1m — вже актуально (до {m1_max.date()}) — скіп")
        else:
            m1_start = sym_start if args.force or m1_max is None else max(sym_start, m1_max + TF_DELTA["1m"])
            if m1_start > end_global:
                print(f"{head}  1m — немає що догружати")
            else:
                label = f"{head}  1m"
                d1m_new = _fetch_klines_progress(client, sym, TF_MAP["1m"], pd.Timestamp(m1_start).to_pydatetime(), pd.Timestamp(end_global).to_pydatetime(), label=label)
                if d1m_new.empty and m1_max is None:
                    print(f"⚠️ {sym}: 1m пусто — пропускаю символ")
                    continue
                elif d1m_new.empty:
                    print(f"{head}  1m — нових барів нема")
                else:
                    d1m_all = _merge_and_save_raw(sym, "1m", d1m_new)
                    print(f"✓ {sym} 1m збережено ({len(d1m_all)} рядків)")

        # ============ 2) решта ТФ + індикатори ============
        for tf in MAIN_TIMEFRAMES:
            tf_min, tf_max = (None, None) if args.force else _existing_range_raw(sym, tf)
            # якщо вже актуально — скіп і індикатори теж
            if not args.force and tf_max is not None and tf_max >= end_global - TF_DELTA[tf]:
                print(f"{head}  {tf} — вже актуально (до {tf_max.date()}) — скіп")
                # якщо немає файлу індикаторів — порахуємо з сирих (вони вже є)
                if not os.path.exists(_ind_csv_path(sym, tf)) and not os.path.exists(_ind_parquet_path(sym, tf)):
                    base_df = None
                    p_pq = _raw_parquet_path(sym, tf)
                    p_csv = _raw_csv_path(sym, tf)
                    if os.path.exists(p_pq):
                        try: base_df = pd.read_parquet(p_pq)
                        except Exception: base_df = None
                    if base_df is None and os.path.exists(p_csv):
                        try: base_df = pd.read_csv(p_csv)
                        except Exception: base_df = None
                    if base_df is not None and not base_df.empty:
                        _save_indicators(sym, tf, base_df)
                continue

            # треба качати (або force)
            tf_start = sym_start if args.force or tf_max is None else max(sym_start, tf_max + TF_DELTA[tf])
            if tf_start > end_global:
                print(f"{head}  {tf} — немає що догружати")
                continue

            label = f"{head}  {tf}"
            dft_new = _fetch_klines_progress(client, sym, TF_MAP[tf], pd.Timestamp(tf_start).to_pydatetime(), pd.Timestamp(end_global).to_pydatetime(), label=label)
            if dft_new.empty and tf_max is None:
                print(f"⚠️ {sym} {tf}: пусто")
                continue
            elif dft_new.empty:
                print(f"{head}  {tf} — нових барів нема")
                continue

            # merge + save raw
            dft_all = _merge_and_save_raw(sym, tf, dft_new)
            print(f"✓ {sym} {tf} сирі свічки збережено ({len(dft_all)} рядків)")

            # індикатори — перерахунок на ВСЬОМУ наборі (щоб lookback був коректний)
            _save_indicators(sym, tf, dft_all)

    print("\n✅ Завершено. Дані у папці:", DATA_ROOT)

if __name__ == "__main__":
    main()
