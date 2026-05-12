from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]

REQUIRED_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def safe_div(numerator: pd.Series, denominator: pd.Series, default: float = np.nan) -> pd.Series:
    denominator = denominator.replace(0, np.nan)
    result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan).fillna(default)


def normalize_bars_df(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = df.copy()

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

    df = df.dropna(subset=["open_time", "open", "high", "low", "close", "volume"])
    df["open_time"] = df["open_time"].astype("int64")

    df = df.sort_values("open_time")
    df = df.drop_duplicates(subset=["open_time"], keep="last")
    df = df.reset_index(drop=True)

    return df

def defrag_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.copy()

def add_basic_time_features(df: pd.DataFrame) -> pd.DataFrame:
    dt = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    df["open_datetime_utc"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    df["hour_utc"] = dt.dt.hour.astype("int16")
    df["day_of_week_utc"] = dt.dt.dayofweek.astype("int16")

    return df


def add_ema_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]

    for period in [9, 21, 50, 100, 200]:
        df[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()

    for period in [21, 50, 100, 200]:
        ema_col = f"ema{period}"

        df[f"dist_close_ema{period}_pct"] = safe_div(
            df["close"] - df[ema_col],
            df["close"],
        ) * 100.0

        df[f"ema{period}_slope_3_pct"] = safe_div(
            df[ema_col] - df[ema_col].shift(3),
            df[ema_col].shift(3),
        ) * 100.0

        df[f"ema{period}_slope_5_pct"] = safe_div(
            df[ema_col] - df[ema_col].shift(5),
            df[ema_col].shift(5),
        ) * 100.0

        df[f"ema{period}_slope_10_pct"] = safe_div(
            df[ema_col] - df[ema_col].shift(10),
            df[ema_col].shift(10),
        ) * 100.0

    df["trend_ema50_above_ema200"] = (df["ema50"] > df["ema200"]).astype("int8")
    df["trend_close_above_ema50"] = (df["close"] > df["ema50"]).astype("int8")
    df["trend_close_above_ema200"] = (df["close"] > df["ema200"]).astype("int8")

    return df


def add_rsi_features(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    close = df["close"]
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = safe_div(avg_gain, avg_loss)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    df[f"rsi{period}"] = rsi
    df[f"rsi{period}_slope_3"] = df[f"rsi{period}"] - df[f"rsi{period}"].shift(3)
    df[f"rsi{period}_slope_5"] = df[f"rsi{period}"] - df[f"rsi{period}"].shift(5)

    df[f"rsi{period}_above_50"] = (df[f"rsi{period}"] > 50).astype("int8")
    df[f"rsi{period}_above_55"] = (df[f"rsi{period}"] > 55).astype("int8")
    df[f"rsi{period}_below_45"] = (df[f"rsi{period}"] < 45).astype("int8")

    return df


def add_macd_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    df["macd_hist_slope_3"] = df["macd_hist"] - df["macd_hist"].shift(3)
    df["macd_hist_slope_5"] = df["macd_hist"] - df["macd_hist"].shift(5)

    df["macd_bullish"] = (df["macd"] > df["macd_signal"]).astype("int8")
    df["macd_hist_positive"] = (df["macd_hist"] > 0).astype("int8")

    return df


def add_atr_adx_features(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr_1 = high - low
    tr_2 = (high - prev_close).abs()
    tr_3 = (low - prev_close).abs()

    true_range = pd.concat([tr_1, tr_2, tr_3], axis=1).max(axis=1)

    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    plus_dm_smooth = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = safe_div(plus_dm_smooth, atr) * 100.0
    minus_di = safe_div(minus_dm_smooth, atr) * 100.0

    dx = safe_div((plus_di - minus_di).abs(), plus_di + minus_di) * 100.0
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    df[f"atr{period}"] = atr
    df["atr_pct"] = safe_div(atr, close) * 100.0

    df[f"plus_di{period}"] = plus_di
    df[f"minus_di{period}"] = minus_di
    df[f"adx{period}"] = adx

    df["di_bullish"] = (plus_di > minus_di).astype("int8")
    df["adx_above_15"] = (adx > 15).astype("int8")
    df["adx_above_20"] = (adx > 20).astype("int8")
    df["adx_above_25"] = (adx > 25).astype("int8")

    return df


def add_bollinger_features(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    close = df["close"]

    middle = close.rolling(period).mean()
    std = close.rolling(period).std()

    upper = middle + std_mult * std
    lower = middle - std_mult * std

    df["bb_middle"] = middle
    df["bb_upper"] = upper
    df["bb_lower"] = lower

    df["bb_width"] = safe_div(upper - lower, middle) * 100.0
    df["bb_percent"] = safe_div(close - lower, upper - lower) * 100.0

    df["close_above_bb_middle"] = (close > middle).astype("int8")
    df["close_near_bb_upper"] = (df["bb_percent"] > 80).astype("int8")
    df["close_near_bb_lower"] = (df["bb_percent"] < 20).astype("int8")

    return df


def calculate_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    atr_col = f"atr{period}"

    if atr_col in df.columns:
        atr = df[atr_col].to_numpy(dtype=float)
    else:
        temp = df.copy()
        temp = add_atr_adx_features(temp, period=period)
        atr = temp[atr_col].to_numpy(dtype=float)

    hl2 = (high + low) / 2.0

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.full(len(df), np.nan)
    final_lower = np.full(len(df), np.nan)
    direction = np.full(len(df), 1, dtype=np.int8)
    supertrend = np.full(len(df), np.nan)

    for i in range(len(df)):
        if np.isnan(basic_upper[i]) or np.isnan(basic_lower[i]):
            continue

        if i == 0 or np.isnan(final_upper[i - 1]) or np.isnan(final_lower[i - 1]):
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            direction[i] = 1
            supertrend[i] = final_lower[i]
            continue

        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        if direction[i - 1] == -1 and close[i] > final_upper[i]:
            direction[i] = 1
        elif direction[i - 1] == 1 and close[i] < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return (
        pd.Series(supertrend, index=df.index),
        pd.Series(direction, index=df.index),
    )


def add_supertrend_features(df: pd.DataFrame) -> pd.DataFrame:
    # Класичний SuperTrend
    df["supertrend"], df["supertrend_direction"] = calculate_supertrend(
        df,
        period=10,
        multiplier=3.0,
    )

    # Трохи швидший варіант для більш чутливих правил
    df["supertrend_fast"], df["supertrend_fast_direction"] = calculate_supertrend(
        df,
        period=7,
        multiplier=2.0,
    )

    df["supertrend_up"] = (df["supertrend_direction"] == 1).astype("int8")
    df["supertrend_down"] = (df["supertrend_direction"] == -1).astype("int8")

    df["supertrend_fast_up"] = (df["supertrend_fast_direction"] == 1).astype("int8")
    df["supertrend_fast_down"] = (df["supertrend_fast_direction"] == -1).astype("int8")

    df["dist_close_supertrend_pct"] = safe_div(
        df["close"] - df["supertrend"],
        df["close"],
    ) * 100.0

    return df


def add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ma50"] = df["volume"].rolling(50).mean()

    df["volume_ratio"] = safe_div(df["volume"], df["volume_ma20"])
    df["volume_ratio_50"] = safe_div(df["volume"], df["volume_ma50"])

    df["volume_above_ma20"] = (df["volume"] > df["volume_ma20"]).astype("int8")
    df["volume_ratio_above_1_2"] = (df["volume_ratio"] > 1.2).astype("int8")
    df["volume_ratio_above_1_5"] = (df["volume_ratio"] > 1.5).astype("int8")
    df["volume_ratio_above_2_0"] = (df["volume_ratio"] > 2.0).astype("int8")

    if "taker_buy_base_volume" in df.columns:
        df["taker_buy_ratio"] = safe_div(df["taker_buy_base_volume"], df["volume"])
        df["taker_buy_ratio_above_0_55"] = (df["taker_buy_ratio"] > 0.55).astype("int8")
        df["taker_buy_ratio_below_0_45"] = (df["taker_buy_ratio"] < 0.45).astype("int8")

    return df


def add_candle_features(df: pd.DataFrame) -> pd.DataFrame:
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]

    candle_range = high - low
    body = (close - open_).abs()

    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low

    upper_wick = upper_wick.clip(lower=0)
    lower_wick = lower_wick.clip(lower=0)

    df["candle_range"] = candle_range
    df["candle_range_pct"] = safe_div(candle_range, close) * 100.0

    df["body_size"] = body
    df["body_pct"] = safe_div(body, close) * 100.0
    df["body_to_range"] = safe_div(body, candle_range)

    df["upper_wick"] = upper_wick
    df["lower_wick"] = lower_wick

    df["upper_wick_pct"] = safe_div(upper_wick, close) * 100.0
    df["lower_wick_pct"] = safe_div(lower_wick, close) * 100.0

    df["upper_wick_to_range"] = safe_div(upper_wick, candle_range)
    df["lower_wick_to_range"] = safe_div(lower_wick, candle_range)

    df["is_bullish_candle"] = (close > open_).astype("int8")
    df["is_bearish_candle"] = (close < open_).astype("int8")
    df["is_doji"] = (df["body_to_range"] < 0.15).astype("int8")

    df["is_bullish_pinbar"] = (
        (df["lower_wick_to_range"] > 0.5)
        & (df["body_to_range"] < 0.35)
        & (close >= open_)
    ).astype("int8")

    df["is_bearish_pinbar"] = (
        (df["upper_wick_to_range"] > 0.5)
        & (df["body_to_range"] < 0.35)
        & (close <= open_)
    ).astype("int8")

    prev_open = open_.shift(1)
    prev_close = close.shift(1)

    df["is_bullish_engulfing"] = (
        (prev_close < prev_open)
        & (close > open_)
        & (close > prev_open)
        & (open_ < prev_close)
    ).astype("int8")

    df["is_bearish_engulfing"] = (
        (prev_close > prev_open)
        & (close < open_)
        & (open_ > prev_close)
        & (close < prev_open)
    ).astype("int8")

    return df


def add_return_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]

    for period in [1, 2, 3, 5, 10, 20]:
        df[f"return_{period}"] = close.pct_change(period) * 100.0

    df["green_candles_3"] = (df["close"] > df["open"]).rolling(3).sum()
    df["green_candles_5"] = (df["close"] > df["open"]).rolling(5).sum()

    df["red_candles_3"] = (df["close"] < df["open"]).rolling(3).sum()
    df["red_candles_5"] = (df["close"] < df["open"]).rolling(5).sum()

    return df


def add_market_structure_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]

    for period in [20, 50, 100]:
        df[f"high_{period}"] = df["high"].rolling(period).max()
        df[f"low_{period}"] = df["low"].rolling(period).min()

        df[f"prev_high_{period}"] = df[f"high_{period}"].shift(1)
        df[f"prev_low_{period}"] = df[f"low_{period}"].shift(1)

        df[f"dist_to_high_{period}_pct"] = safe_div(
            df[f"high_{period}"] - close,
            close,
        ) * 100.0

        df[f"dist_to_low_{period}_pct"] = safe_div(
            close - df[f"low_{period}"],
            close,
        ) * 100.0

        df[f"break_high_{period}"] = (close > df[f"prev_high_{period}"]).astype("int8")
        df[f"break_low_{period}"] = (close < df[f"prev_low_{period}"]).astype("int8")

    return df


def add_pivot_features(df: pd.DataFrame) -> pd.DataFrame:
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)

    pivot = (prev_high + prev_low + prev_close) / 3.0

    df["pivot"] = pivot
    df["r1"] = 2.0 * pivot - prev_low
    df["s1"] = 2.0 * pivot - prev_high
    df["r2"] = pivot + (prev_high - prev_low)
    df["s2"] = pivot - (prev_high - prev_low)

    df["dist_to_pivot_pct"] = safe_div(df["close"] - df["pivot"], df["close"]) * 100.0
    df["dist_to_r1_pct"] = safe_div(df["r1"] - df["close"], df["close"]) * 100.0
    df["dist_to_s1_pct"] = safe_div(df["close"] - df["s1"], df["close"]) * 100.0
    df["dist_to_r2_pct"] = safe_div(df["r2"] - df["close"], df["close"]) * 100.0
    df["dist_to_s2_pct"] = safe_div(df["close"] - df["s2"], df["close"]) * 100.0

    df["close_above_pivot"] = (df["close"] > df["pivot"]).astype("int8")

    return df


def add_combined_signal_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Це не фінальні правила входу, а зручні бінарні фічі,
    які потім будемо використовувати в генераторі простих правил.
    """

    df["long_trend_basic"] = (
        (df["close"] > df["ema50"])
        & (df["ema50"] > df["ema200"])
        & (df["ema50_slope_5_pct"] > 0)
    ).astype("int8")

    df["short_trend_basic"] = (
        (df["close"] < df["ema50"])
        & (df["ema50"] < df["ema200"])
        & (df["ema50_slope_5_pct"] < 0)
    ).astype("int8")

    df["long_impulse_basic"] = (
        (df["rsi14"] > 50)
        & (df["macd_hist"] > 0)
    ).astype("int8")

    df["short_impulse_basic"] = (
        (df["rsi14"] < 50)
        & (df["macd_hist"] < 0)
    ).astype("int8")

    df["long_volume_confirm"] = (
        (df["volume_ratio"] > 1.0)
        & (df.get("taker_buy_ratio", 0.5) > 0.5)
    ).astype("int8")

    df["short_volume_confirm"] = (
        (df["volume_ratio"] > 1.0)
        & (df.get("taker_buy_ratio", 0.5) < 0.5)
    ).astype("int8")

    df["market_has_min_volatility"] = (df["atr_pct"] > 0.15).astype("int8")
    df["market_too_volatile"] = (df["atr_pct"] > 4.0).astype("int8")

    return df


def build_indicators(df: pd.DataFrame, drop_warmup: bool = True) -> pd.DataFrame:
    df = normalize_bars_df(df)

    df = add_basic_time_features(df)
    df = add_ema_features(df)
    df = add_rsi_features(df, period=14)
    df = add_macd_features(df)
    df = add_atr_adx_features(df, period=14)

    # Для fast supertrend потрібен ATR7.
    df = add_atr_adx_features(df, period=7)

    df = defrag_df(df)

    df = add_bollinger_features(df, period=20, std_mult=2.0)
    df = add_supertrend_features(df)

    df = defrag_df(df)

    df = add_volume_features(df)

    df = defrag_df(df)

    df = add_candle_features(df)

    df = defrag_df(df)

    df = add_return_features(df)

    df = defrag_df(df)

    df = add_market_structure_features(df)

    df = defrag_df(df)

    df = add_pivot_features(df)

    df = defrag_df(df)

    df = add_combined_signal_features(df)

    df = defrag_df(df)

    if drop_warmup:
        required_ready_cols = [
            "ema200",
            "rsi14",
            "macd_hist",
            "atr14",
            "adx14",
            "bb_width",
            "supertrend",
            "volume_ma20",
            "high_100",
            "low_100",
        ]

        df = df.dropna(subset=required_ready_cols).reset_index(drop=True)

    return df


def discover_symbols(data_dir: Path) -> list[str]:
    if not data_dir.exists():
        return []

    symbols = []

    for path in data_dir.iterdir():
        if path.is_dir():
            symbols.append(path.name.upper())

    return sorted(set(symbols))


def find_input_file(data_dir: Path, symbol: str, timeframe: str) -> Path | None:
    symbol_dir = data_dir / symbol

    candidates = [
        symbol_dir / f"{symbol}_{timeframe}.csv.gz",
        symbol_dir / f"{symbol}_{timeframe}.csv",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def make_output_file(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    output_gzip: bool,
) -> Path:
    symbol_dir = data_dir / symbol
    suffix = ".csv.gz" if output_gzip else ".csv"

    return symbol_dir / f"{symbol}_{timeframe}_critical_indicators{suffix}"


def read_csv_any(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="infer")


def write_csv_any(df: pd.DataFrame, path: Path, output_gzip: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if output_gzip:
        df.to_csv(path, index=False, compression="gzip")
    else:
        df.to_csv(path, index=False)


def process_one(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    output_gzip: bool,
    overwrite: bool,
    drop_warmup: bool,
) -> None:
    input_path = find_input_file(data_dir, symbol, timeframe)

    if input_path is None:
        print(f"[SKIP] {symbol} {timeframe}: raw bars file not found")
        return

    output_path = make_output_file(
        data_dir=data_dir,
        symbol=symbol,
        timeframe=timeframe,
        output_gzip=output_gzip,
    )

    if output_path.exists() and not overwrite:
        print(f"[SKIP] {symbol} {timeframe}: output exists -> {output_path}")
        return

    print(f"[READ] {symbol} {timeframe}: {input_path}")
    raw_df = read_csv_any(input_path)

    print(f"[BUILD] {symbol} {timeframe}: rows={len(raw_df):,}")
    indicators_df = build_indicators(raw_df, drop_warmup=drop_warmup)

    print(
        f"[WRITE] {symbol} {timeframe}: "
        f"rows={len(indicators_df):,}, cols={len(indicators_df.columns):,} -> {output_path}"
    )

    write_csv_any(indicators_df, output_path, output_gzip=output_gzip)

    if len(indicators_df) > 0:
        first_time = indicators_df["open_datetime_utc"].iloc[0]
        last_time = indicators_df["open_datetime_utc"].iloc[-1]

        print(
            f"[OK] {symbol} {timeframe}: "
            f"from={first_time}, to={last_time}, "
            f"last_close={indicators_df['close'].iloc[-1]}"
        )
    else:
        print(f"[WARNING] {symbol} {timeframe}: output is empty")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build MeowBot critical indicator files from raw OHLCV bars."
    )

    parser.add_argument(
        "--data-dir",
        type=str,
        default="test/data",
        help="Root data directory. Default: test/data",
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Symbols to process. Example: BTCUSDT ETHUSDT SOLUSDT. If empty, auto-discover.",
    )

    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=DEFAULT_TIMEFRAMES,
        help="Timeframes to process. Default: 15m 30m 1h 4h 1d",
    )

    parser.add_argument(
        "--plain-csv",
        action="store_true",
        help="Write .csv instead of .csv.gz.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing indicator files.",
    )

    parser.add_argument(
        "--keep-warmup",
        action="store_true",
        help="Keep warmup rows with NaN values.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    data_dir = Path(args.data_dir)

    if args.symbols:
        symbols = sorted(set(symbol.upper() for symbol in args.symbols))
    else:
        symbols = discover_symbols(data_dir)

    if not symbols:
        raise RuntimeError(f"No symbols found in {data_dir}")

    output_gzip = not args.plain_csv
    drop_warmup = not args.keep_warmup

    print("=" * 100)
    print("MeowBot indicator builder")
    print(f"Data dir:     {data_dir}")
    print(f"Symbols:      {len(symbols)}")
    print(f"Timeframes:   {args.timeframes}")
    print(f"Output gzip:  {output_gzip}")
    print(f"Drop warmup:  {drop_warmup}")
    print(f"Overwrite:    {args.overwrite}")
    print("=" * 100)

    for symbol in symbols:
        for timeframe in args.timeframes:
            try:
                process_one(
                    data_dir=data_dir,
                    symbol=symbol,
                    timeframe=timeframe,
                    output_gzip=output_gzip,
                    overwrite=args.overwrite,
                    drop_warmup=drop_warmup,
                )
            except Exception as e:
                print(f"[ERROR] {symbol} {timeframe}: {e}")

    print("=" * 100)
    print("Indicator build finished.")
    print("=" * 100)


if __name__ == "__main__":
    main()