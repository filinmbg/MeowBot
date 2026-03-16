from __future__ import annotations

import gzip
import time
from pathlib import Path

import numpy as np
import pandas as pd


DATA_DIR = Path(r"D:\Project\MeowBot\test\data\BTCUSDT")
TIMEFRAMES = ["1m", "15m", "30m", "1h", "2h", "4h"]


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()

    rs = ma_up / ma_down.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line - signal_line


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, period)
    upperband = hl2 + multiplier * atr_val
    lowerband = hl2 - multiplier * atr_val

    direction = pd.Series(index=df.index, dtype="int64")
    direction.iloc[0] = 1

    final_upper = upperband.copy()
    final_lower = lowerband.copy()

    for i in range(1, len(df)):
        if upperband.iloc[i] < final_upper.iloc[i - 1] or df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = upperband.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if lowerband.iloc[i] > final_lower.iloc[i - 1] or df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = lowerband.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        if direction.iloc[i - 1] == -1 and df["close"].iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif direction.iloc[i - 1] == 1 and df["close"].iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

    bullish = (direction == 1).astype(int)
    return bullish


def load_tf(tf: str) -> pd.DataFrame | None:
    path = DATA_DIR / f"BTCUSDT_{tf}.csv.gz"
    if not path.exists():
        print(f"[skip] {tf}: file not found -> {path}", flush=True)
        return None

    print(f"[load] {path}", flush=True)
    df = pd.read_csv(path, compression="gzip")
    print(f"[load] rows={len(df)}", flush=True)

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    df = df.sort_values("close_time").reset_index(drop=True)
    return df


def build_for_tf(tf: str) -> None:
    started = time.time()
    df = load_tf(tf)
    if df is None:
        return

    close = df["close"]

    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)

    df["rsi14"] = rsi(close, 14)
    df["rsi7"] = rsi(close, 7)

    df["atr14"] = atr(df, 14)
    df["atr14_pct"] = df["atr14"] / df["close"]

    df["macd_hist"] = macd_hist(close)
    df["relative_volume20"] = df["volume"] / df["volume"].rolling(20).mean()
    df["relative_volume50"] = df["volume"] / df["volume"].rolling(50).mean()

    df["supertrend_bullish"] = supertrend(df, period=10, multiplier=3.0)

    out_path = DATA_DIR / f"BTCUSDT_{tf}_indicators.csv.gz"
    df.to_csv(out_path, index=False, compression="gzip")

    print(f"[save] {out_path}", flush=True)
    print(f"[done] {tf}: rows={len(df)} elapsed={time.time() - started:.1f}s", flush=True)


def main() -> None:
    total_started = time.time()
    for tf in TIMEFRAMES:
        print(f"\n=== PROCESS {tf} ===", flush=True)
        build_for_tf(tf)
    print(f"\nALL DONE elapsed={time.time() - total_started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
