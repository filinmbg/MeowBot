from __future__ import annotations

import gc
import warnings
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning
from tqdm import tqdm

warnings.simplefilter("ignore", PerformanceWarning)
# =========================
# CONFIG
# =========================

DATA_DIR = Path("test-manual/data")
OUT_DIR = Path("test-manual/indicators")

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

TIMEFRAMES = [
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
]

EMA_PERIODS = [5, 9, 12, 20, 21, 26, 50, 100, 200]
SMA_PERIODS = [5, 9, 20, 50, 100, 200]
WMA_PERIODS = [9, 20, 50]
HMA_PERIODS = [20, 50]
VWMA_PERIODS = [20, 50, 100]

RSI_PERIODS = [7, 14, 21]
ATR_PERIODS = [7, 14, 21]
ADX_PERIODS = [14, 21]

ROLLING_LEVEL_PERIODS = [10, 20, 50, 100, 200]

# Для online-safe трендових ліній
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
TRENDLINE_POINTS = [3, 5]

DOWNSIZE_FLOATS = True


# =========================
# BASIC HELPERS
# =========================

def safe_div(a, b):
    return np.where(np.abs(b) > 1e-12, a / b, np.nan)


def pct_distance(price: pd.Series, level: pd.Series) -> pd.Series:
    return (price - level) / level.replace(0, np.nan) * 100.0


def to_numeric_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "trades",
        "open_time",
        "close_time",
    ]

    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def downsize_float_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not DOWNSIZE_FLOATS:
        return df

    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].astype("float32")

    return df


# =========================
# MOVING AVERAGES
# =========================

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)

    return series.rolling(period, min_periods=period).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True,
    )


def hma(series: pd.Series, period: int) -> pd.Series:
    half = max(period // 2, 1)
    sqrt_period = max(int(np.sqrt(period)), 1)

    fast = wma(series, half)
    slow = wma(series, period)

    return wma(2 * fast - slow, sqrt_period)


def vwma(close: pd.Series, volume: pd.Series, period: int) -> pd.Series:
    pv = close * volume
    return pv.rolling(period, min_periods=period).sum() / volume.rolling(
        period,
        min_periods=period,
    ).sum()


# =========================
# MOMENTUM
# =========================

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, 12) - ema(close, 26)
    signal = ema(macd_line, 9)
    hist = macd_line - signal
    return macd_line, signal, hist


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(k_period, min_periods=k_period).min()
    highest_high = high.rolling(k_period, min_periods=k_period).max()

    k = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    d = k.rolling(d_period, min_periods=d_period).mean()

    return k, d


def stoch_rsi(
    close: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    r = rsi(close, rsi_period)

    r_min = r.rolling(stoch_period, min_periods=stoch_period).min()
    r_max = r.rolling(stoch_period, min_periods=stoch_period).max()

    k = (r - r_min) / (r_max - r_min).replace(0, np.nan) * 100
    d = k.rolling(d_period, min_periods=d_period).mean()

    return k, d


def cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    tp = (high + low + close) / 3
    tp_sma = tp.rolling(period, min_periods=period).mean()

    mean_dev = tp.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))),
        raw=True,
    )

    return (tp - tp_sma) / (0.015 * mean_dev.replace(0, np.nan))


def roc(close: pd.Series, period: int = 12) -> pd.Series:
    return close.pct_change(periods=period) * 100


def momentum(close: pd.Series, period: int = 10) -> pd.Series:
    return close - close.shift(period)


def williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    highest_high = high.rolling(period, min_periods=period).max()
    lowest_low = low.rolling(period, min_periods=period).min()

    return -100 * (highest_high - close) / (highest_high - lowest_low).replace(0, np.nan)


def awesome_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    median = (high + low) / 2
    return sma(median, 5) - sma(median, 34)


def tsi(close: pd.Series, slow: int = 25, fast: int = 13) -> pd.Series:
    diff = close.diff()

    double_smoothed = ema(ema(diff, slow), fast)
    double_smoothed_abs = ema(ema(diff.abs(), slow), fast)

    return 100 * double_smoothed / double_smoothed_abs.replace(0, np.nan)


def ultimate_oscillator(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    p1: int = 7,
    p2: int = 14,
    p3: int = 28,
) -> pd.Series:
    prev_close = close.shift(1)

    bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
    tr = pd.concat([high, prev_close], axis=1).max(axis=1) - pd.concat(
        [low, prev_close],
        axis=1,
    ).min(axis=1)

    avg1 = bp.rolling(p1, min_periods=p1).sum() / tr.rolling(p1, min_periods=p1).sum()
    avg2 = bp.rolling(p2, min_periods=p2).sum() / tr.rolling(p2, min_periods=p2).sum()
    avg3 = bp.rolling(p3, min_periods=p3).sum() / tr.rolling(p3, min_periods=p3).sum()

    return 100 * ((4 * avg1) + (2 * avg2) + avg3) / 7


# =========================
# VOLATILITY
# =========================

def true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> pd.Series:
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std()

    upper = mid + std_mult * std
    lower = mid - std_mult * std

    width_pct = (upper - lower) / mid.replace(0, np.nan) * 100
    pos = (close - lower) / (upper - lower).replace(0, np.nan)

    return upper, mid, lower, width_pct, pos


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = ema(close, period)
    a = atr(high, low, close, period)

    upper = mid + atr_mult * a
    lower = mid - atr_mult * a

    width_pct = (upper - lower) / mid.replace(0, np.nan) * 100
    pos = (close - lower) / (upper - lower).replace(0, np.nan)

    return upper, mid, lower, width_pct, pos


def donchian_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    upper = high.rolling(period, min_periods=period).max()
    lower = low.rolling(period, min_periods=period).min()
    mid = (upper + lower) / 2

    width_pct = (upper - lower) / mid.replace(0, np.nan) * 100
    pos = (close - lower) / (upper - lower).replace(0, np.nan)

    return upper, mid, lower, width_pct, pos


def historical_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    log_return = np.log(close / close.shift(1))
    return log_return.rolling(period, min_periods=period).std() * np.sqrt(period) * 100


# =========================
# TREND STRENGTH
# =========================

def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move) & (up_move > 0),
        up_move,
        0.0,
    )

    minus_dm = np.where(
        (down_move > up_move) & (down_move > 0),
        down_move,
        0.0,
    )

    tr = true_range(high, low, close)

    atr_wilder = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean() / atr_wilder.replace(0, np.nan)

    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean() / atr_wilder.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_value = dx.ewm(alpha=1 / period, adjust=False).mean()

    return adx_value, plus_di, minus_di


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    hl2 = (high + low) / 2
    atr_value = atr(high, low, close, period)

    upper_basic = hl2 + multiplier * atr_value
    lower_basic = hl2 - multiplier * atr_value

    upper = upper_basic.copy()
    lower = lower_basic.copy()

    direction = np.ones(len(close), dtype=np.int8)
    st = np.full(len(close), np.nan, dtype=np.float64)

    close_values = close.to_numpy()
    upper_values = upper.to_numpy()
    lower_values = lower.to_numpy()

    for i in range(1, len(close)):
        if np.isnan(upper_values[i - 1]) or np.isnan(lower_values[i - 1]):
            continue

        if upper_values[i] > upper_values[i - 1] and close_values[i - 1] <= upper_values[i - 1]:
            upper_values[i] = upper_values[i - 1]

        if lower_values[i] < lower_values[i - 1] and close_values[i - 1] >= lower_values[i - 1]:
            lower_values[i] = lower_values[i - 1]

        if close_values[i] > upper_values[i - 1]:
            direction[i] = 1
        elif close_values[i] < lower_values[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

        st[i] = lower_values[i] if direction[i] == 1 else upper_values[i]

    return pd.Series(st, index=close.index), pd.Series(direction, index=close.index)


def parabolic_sar(
    high: pd.Series,
    low: pd.Series,
    step: float = 0.02,
    max_step: float = 0.2,
) -> pd.Series:
    high_values = high.to_numpy()
    low_values = low.to_numpy()

    n = len(high_values)
    sar = np.full(n, np.nan, dtype=np.float64)

    if n < 2:
        return pd.Series(sar, index=high.index)

    uptrend = True
    af = step
    ep = high_values[0]
    sar[0] = low_values[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]
        sar[i] = prev_sar + af * (ep - prev_sar)

        if uptrend:
            sar[i] = min(sar[i], low_values[i - 1])

            if i > 1:
                sar[i] = min(sar[i], low_values[i - 2])

            if low_values[i] < sar[i]:
                uptrend = False
                sar[i] = ep
                ep = low_values[i]
                af = step
            else:
                if high_values[i] > ep:
                    ep = high_values[i]
                    af = min(af + step, max_step)
        else:
            sar[i] = max(sar[i], high_values[i - 1])

            if i > 1:
                sar[i] = max(sar[i], high_values[i - 2])

            if high_values[i] > sar[i]:
                uptrend = True
                sar[i] = ep
                ep = high_values[i]
                af = step
            else:
                if low_values[i] < ep:
                    ep = low_values[i]
                    af = min(af + step, max_step)

    return pd.Series(sar, index=high.index)


# =========================
# VOLUME
# =========================

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    tp = (high + low + close) / 3
    raw_money_flow = tp * volume

    direction = tp.diff()

    positive = raw_money_flow.where(direction > 0, 0.0)
    negative = raw_money_flow.where(direction < 0, 0.0)

    pos_sum = positive.rolling(period, min_periods=period).sum()
    neg_sum = negative.rolling(period, min_periods=period).sum()

    money_ratio = pos_sum / neg_sum.replace(0, np.nan)

    return 100 - (100 / (1 + money_ratio))


def cmf(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> pd.Series:
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * volume

    return mfv.rolling(period, min_periods=period).sum() / volume.rolling(
        period,
        min_periods=period,
    ).sum().replace(0, np.nan)


def rolling_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int,
) -> pd.Series:
    typical = (high + low + close) / 3
    pv = typical * volume

    return pv.rolling(period, min_periods=period).sum() / volume.rolling(
        period,
        min_periods=period,
    ).sum().replace(0, np.nan)


def cumulative_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    typical = (high + low + close) / 3
    pv = typical * volume

    return pv.cumsum() / volume.cumsum().replace(0, np.nan)


def force_index(close: pd.Series, volume: pd.Series, period: int = 13) -> pd.Series:
    raw = close.diff() * volume
    return ema(raw, period)


def ease_of_movement(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    distance_moved = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    box_ratio = volume / (high - low).replace(0, np.nan)

    eom = distance_moved / box_ratio.replace(0, np.nan)

    return sma(eom, period)


def accumulation_distribution(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * volume

    return mfv.cumsum()


# =========================
# CANDLE STRUCTURE
# =========================

def add_candle_features(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]

    candle_range = (h - l).replace(0, np.nan)
    body = (c - o).abs()

    df["candle_range_pct"] = (h - l) / c.replace(0, np.nan) * 100
    df["body_pct"] = body / candle_range * 100
    df["upper_wick_pct"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / candle_range * 100
    df["lower_wick_pct"] = (pd.concat([o, c], axis=1).min(axis=1) - l) / candle_range * 100
    df["close_position_in_candle"] = (c - l) / candle_range

    df["is_green"] = (c > o).astype("int8")
    df["is_red"] = (c < o).astype("int8")
    df["is_doji"] = (df["body_pct"] < 10).astype("int8")

    df["is_pinbar_upper"] = (
        (df["upper_wick_pct"] > 55)
        & (df["body_pct"] < 35)
    ).astype("int8")

    df["is_pinbar_lower"] = (
        (df["lower_wick_pct"] > 55)
        & (df["body_pct"] < 35)
    ).astype("int8")

    prev_o = o.shift(1)
    prev_c = c.shift(1)

    df["bullish_engulfing"] = (
        (c > o)
        & (prev_c < prev_o)
        & (c >= prev_o)
        & (o <= prev_c)
    ).astype("int8")

    df["bearish_engulfing"] = (
        (c < o)
        & (prev_c > prev_o)
        & (c <= prev_o)
        & (o >= prev_c)
    ).astype("int8")

    df["inside_bar"] = (
        (h < h.shift(1))
        & (l > l.shift(1))
    ).astype("int8")

    df["outside_bar"] = (
        (h > h.shift(1))
        & (l < l.shift(1))
    ).astype("int8")

    range_sma_20 = (h - l).rolling(20, min_periods=20).mean()

    df["big_candle"] = ((h - l) > range_sma_20 * 1.5).astype("int8")
    df["small_candle"] = ((h - l) < range_sma_20 * 0.5).astype("int8")

    return df


# =========================
# LEVELS / PIVOTS
# =========================

def add_rolling_levels(df: pd.DataFrame) -> pd.DataFrame:
    h = df["high"]
    l = df["low"]
    c = df["close"]

    for period in ROLLING_LEVEL_PERIODS:
        rh = h.rolling(period, min_periods=period).max()
        rl = l.rolling(period, min_periods=period).min()
        mid = (rh + rl) / 2

        df[f"rolling_high_{period}"] = rh
        df[f"rolling_low_{period}"] = rl
        df[f"rolling_mid_{period}"] = mid

        df[f"dist_to_high_{period}_pct"] = pct_distance(c, rh)
        df[f"dist_to_low_{period}_pct"] = pct_distance(c, rl)
        df[f"dist_to_mid_{period}_pct"] = pct_distance(c, mid)

        df[f"breakout_high_{period}"] = (c > rh.shift(1)).astype("int8")
        df[f"breakdown_low_{period}"] = (c < rl.shift(1)).astype("int8")

    prev_h = h.shift(1)
    prev_l = l.shift(1)
    prev_c = c.shift(1)

    pivot = (prev_h + prev_l + prev_c) / 3
    r1 = 2 * pivot - prev_l
    s1 = 2 * pivot - prev_h
    r2 = pivot + (prev_h - prev_l)
    s2 = pivot - (prev_h - prev_l)
    r3 = prev_h + 2 * (pivot - prev_l)
    s3 = prev_l - 2 * (prev_h - pivot)

    df["pivot"] = pivot
    df["r1"] = r1
    df["s1"] = s1
    df["r2"] = r2
    df["s2"] = s2
    df["r3"] = r3
    df["s3"] = s3

    for level_name in ["pivot", "r1", "s1", "r2", "s2", "r3", "s3"]:
        df[f"dist_to_{level_name}_pct"] = pct_distance(c, df[level_name])

    return df


# =========================
# ONLINE-SAFE TREND LINES
# =========================

def confirmed_pivots(
    high: pd.Series,
    low: pd.Series,
    left: int = 3,
    right: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Online-safe pivot logic.

    Pivot на свічці i можна знати тільки після right майбутніх свічок.
    Тому ми записуємо підтверджений pivot у момент i + right.
    Це не підглядає в майбутнє при online роботі.
    """
    window = left + right + 1

    rolling_max = high.rolling(window, center=True, min_periods=window).max()
    rolling_min = low.rolling(window, center=True, min_periods=window).min()

    raw_pivot_high = high.where(high == rolling_max)
    raw_pivot_low = low.where(low == rolling_min)

    confirmed_high = raw_pivot_high.shift(right)
    confirmed_low = raw_pivot_low.shift(right)

    return confirmed_high, confirmed_low


def build_trendline_features(
    price: pd.Series,
    confirmed_points: pd.Series,
    points_count: int,
    prefix: str,
) -> pd.DataFrame:
    n = len(price)

    line_value = np.full(n, np.nan, dtype=np.float64)
    slope = np.full(n, np.nan, dtype=np.float64)
    slope_pct = np.full(n, np.nan, dtype=np.float64)
    distance_pct = np.full(n, np.nan, dtype=np.float64)

    point_indices: list[int] = []
    point_prices: list[float] = []

    values = confirmed_points.to_numpy(dtype=np.float64)
    close_values = price.to_numpy(dtype=np.float64)

    current_slope = np.nan
    current_intercept = np.nan

    for i in range(n):
        point_price = values[i]

        if not np.isnan(point_price):
            # Реальний pivot був PIVOT_RIGHT свічок назад
            pivot_index = max(i - PIVOT_RIGHT, 0)

            point_indices.append(pivot_index)
            point_prices.append(point_price)

            if len(point_indices) > points_count:
                point_indices = point_indices[-points_count:]
                point_prices = point_prices[-points_count:]

            if len(point_indices) >= 2:
                x = np.asarray(point_indices, dtype=np.float64)
                y = np.asarray(point_prices, dtype=np.float64)

                current_slope, current_intercept = np.polyfit(x, y, 1)

        if not np.isnan(current_slope):
            value = current_slope * i + current_intercept
            line_value[i] = value
            slope[i] = current_slope

            if close_values[i] != 0:
                slope_pct[i] = current_slope / close_values[i] * 100
                distance_pct[i] = (close_values[i] - value) / close_values[i] * 100

    return pd.DataFrame(
        {
            f"{prefix}_line_{points_count}p": line_value,
            f"{prefix}_slope_{points_count}p": slope,
            f"{prefix}_slope_pct_{points_count}p": slope_pct,
            f"{prefix}_distance_pct_{points_count}p": distance_pct,
        },
        index=price.index,
    )


def add_trendline_features(df: pd.DataFrame) -> pd.DataFrame:
    confirmed_high, confirmed_low = confirmed_pivots(
        high=df["high"],
        low=df["low"],
        left=PIVOT_LEFT,
        right=PIVOT_RIGHT,
    )

    df["confirmed_pivot_high"] = confirmed_high
    df["confirmed_pivot_low"] = confirmed_low

    df["has_confirmed_pivot_high"] = confirmed_high.notna().astype("int8")
    df["has_confirmed_pivot_low"] = confirmed_low.notna().astype("int8")

    trendline_frames = []

    for points_count in TRENDLINE_POINTS:
        high_lines = build_trendline_features(
            price=df["close"],
            confirmed_points=confirmed_high,
            points_count=points_count,
            prefix="resistance_trend",
        )

        low_lines = build_trendline_features(
            price=df["close"],
            confirmed_points=confirmed_low,
            points_count=points_count,
            prefix="support_trend",
        )

        trendline_frames.append(high_lines)
        trendline_frames.append(low_lines)

    trend_df = pd.concat(trendline_frames, axis=1)

    df = pd.concat([df, trend_df], axis=1)

    for points_count in TRENDLINE_POINTS:
        resistance_col = f"resistance_trend_line_{points_count}p"
        support_col = f"support_trend_line_{points_count}p"

        df[f"break_resistance_trend_{points_count}p"] = (
            df["close"] > df[resistance_col]
        ).astype("int8")

        df[f"break_support_trend_{points_count}p"] = (
            df["close"] < df[support_col]
        ).astype("int8")

    return df


# =========================
# MAIN INDICATOR GENERATION
# =========================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = to_numeric_ohlcv(df)

    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    # Basic returns
    df["return_1"] = c.pct_change(1) * 100
    df["return_3"] = c.pct_change(3) * 100
    df["return_5"] = c.pct_change(5) * 100
    df["return_10"] = c.pct_change(10) * 100
    df["log_return_1"] = np.log(c / c.shift(1))

    # EMA / SMA
    for period in EMA_PERIODS:
        col = f"ema_{period}"
        df[col] = ema(c, period)
        df[f"dist_to_{col}_pct"] = pct_distance(c, df[col])
        df[f"{col}_slope_1"] = df[col].diff()
        df[f"{col}_slope_pct_1"] = df[col].pct_change() * 100

    for period in SMA_PERIODS:
        col = f"sma_{period}"
        df[col] = sma(c, period)
        df[f"dist_to_{col}_pct"] = pct_distance(c, df[col])
        df[f"{col}_slope_1"] = df[col].diff()
        df[f"{col}_slope_pct_1"] = df[col].pct_change() * 100

    for period in WMA_PERIODS:
        col = f"wma_{period}"
        df[col] = wma(c, period)
        df[f"dist_to_{col}_pct"] = pct_distance(c, df[col])

    for period in HMA_PERIODS:
        col = f"hma_{period}"
        df[col] = hma(c, period)
        df[f"dist_to_{col}_pct"] = pct_distance(c, df[col])

    for period in VWMA_PERIODS:
        col = f"vwma_{period}"
        df[col] = vwma(c, v, period)
        df[f"dist_to_{col}_pct"] = pct_distance(c, df[col])

    # EMA alignment
    df["ema_bull_alignment_20_50_100_200"] = (
        (df["ema_20"] > df["ema_50"])
        & (df["ema_50"] > df["ema_100"])
        & (df["ema_100"] > df["ema_200"])
    ).astype("int8")

    df["ema_bear_alignment_20_50_100_200"] = (
        (df["ema_20"] < df["ema_50"])
        & (df["ema_50"] < df["ema_100"])
        & (df["ema_100"] < df["ema_200"])
    ).astype("int8")

    df["price_above_ema_20"] = (c > df["ema_20"]).astype("int8")
    df["price_above_ema_50"] = (c > df["ema_50"]).astype("int8")
    df["price_above_ema_100"] = (c > df["ema_100"]).astype("int8")
    df["price_above_ema_200"] = (c > df["ema_200"]).astype("int8")

    df = df.copy()

    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # RSI
    for period in RSI_PERIODS:
        df[f"rsi_{period}"] = rsi(c, period)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["macd_hist_slope"] = df["macd_hist"].diff()

    # Stochastic
    df["stoch_k_14"], df["stoch_d_14"] = stochastic(h, l, c, 14, 3)

    # Stoch RSI
    df["stoch_rsi_k_14"], df["stoch_rsi_d_14"] = stoch_rsi(c, 14, 14, 3)

    # Other momentum
    df["cci_20"] = cci(h, l, c, 20)
    df["roc_12"] = roc(c, 12)
    df["momentum_10"] = momentum(c, 10)
    df["williams_r_14"] = williams_r(h, l, c, 14)
    df["awesome_oscillator"] = awesome_oscillator(h, l)
    df["tsi"] = tsi(c, 25, 13)
    df["ultimate_oscillator"] = ultimate_oscillator(h, l, c)

    # ATR / volatility
    tr = true_range(h, l, c)
    df["true_range"] = tr
    df["true_range_pct"] = tr / c.replace(0, np.nan) * 100

    for period in ATR_PERIODS:
        col = f"atr_{period}"
        df[col] = atr(h, l, c, period)
        df[f"{col}_pct"] = df[col] / c.replace(0, np.nan) * 100

    for period in [20, 50]:
        upper, mid, lower, width, pos = bollinger_bands(c, period, 2.0)

        df[f"bb_upper_{period}"] = upper
        df[f"bb_mid_{period}"] = mid
        df[f"bb_lower_{period}"] = lower
        df[f"bb_width_pct_{period}"] = width
        df[f"bb_position_{period}"] = pos
        df[f"dist_to_bb_upper_{period}_pct"] = pct_distance(c, upper)
        df[f"dist_to_bb_lower_{period}_pct"] = pct_distance(c, lower)

    kc_upper, kc_mid, kc_lower, kc_width, kc_pos = keltner_channel(h, l, c, 20, 2.0)
    df["kc_upper_20"] = kc_upper
    df["kc_mid_20"] = kc_mid
    df["kc_lower_20"] = kc_lower
    df["kc_width_pct_20"] = kc_width
    df["kc_position_20"] = kc_pos

    for period in [20, 50, 100]:
        dc_upper, dc_mid, dc_lower, dc_width, dc_pos = donchian_channel(h, l, c, period)

        df[f"donchian_upper_{period}"] = dc_upper
        df[f"donchian_mid_{period}"] = dc_mid
        df[f"donchian_lower_{period}"] = dc_lower
        df[f"donchian_width_pct_{period}"] = dc_width
        df[f"donchian_position_{period}"] = dc_pos

    for period in [20, 50]:
        df[f"historical_volatility_{period}"] = historical_volatility(c, period)

    # ADX / DI
    for period in ADX_PERIODS:
        adx_value, plus_di, minus_di = adx(h, l, c, period)

        df[f"adx_{period}"] = adx_value
        df[f"plus_di_{period}"] = plus_di
        df[f"minus_di_{period}"] = minus_di
        df[f"di_diff_{period}"] = plus_di - minus_di

    # SuperTrend
    df["supertrend_10_3"], df["supertrend_dir_10_3"] = supertrend(h, l, c, 10, 3.0)
    df["dist_to_supertrend_10_3_pct"] = pct_distance(c, df["supertrend_10_3"])

    # Parabolic SAR
    df["parabolic_sar"] = parabolic_sar(h, l)
    df["dist_to_parabolic_sar_pct"] = pct_distance(c, df["parabolic_sar"])

    # Ichimoku raw / online-safe
    tenkan_high = h.rolling(9, min_periods=9).max()
    tenkan_low = l.rolling(9, min_periods=9).min()
    kijun_high = h.rolling(26, min_periods=26).max()
    kijun_low = l.rolling(26, min_periods=26).min()
    span_b_high = h.rolling(52, min_periods=52).max()
    span_b_low = l.rolling(52, min_periods=52).min()

    df["ichimoku_tenkan"] = (tenkan_high + tenkan_low) / 2
    df["ichimoku_kijun"] = (kijun_high + kijun_low) / 2
    df["ichimoku_span_a_raw"] = (df["ichimoku_tenkan"] + df["ichimoku_kijun"]) / 2
    df["ichimoku_span_b_raw"] = (span_b_high + span_b_low) / 2

    df["dist_to_ichimoku_tenkan_pct"] = pct_distance(c, df["ichimoku_tenkan"])
    df["dist_to_ichimoku_kijun_pct"] = pct_distance(c, df["ichimoku_kijun"])
    df["dist_to_ichimoku_span_a_pct"] = pct_distance(c, df["ichimoku_span_a_raw"])
    df["dist_to_ichimoku_span_b_pct"] = pct_distance(c, df["ichimoku_span_b_raw"])

    df["ichimoku_bull"] = (
        (c > df["ichimoku_span_a_raw"])
        & (c > df["ichimoku_span_b_raw"])
        & (df["ichimoku_tenkan"] > df["ichimoku_kijun"])
    ).astype("int8")

    df["ichimoku_bear"] = (
        (c < df["ichimoku_span_a_raw"])
        & (c < df["ichimoku_span_b_raw"])
        & (df["ichimoku_tenkan"] < df["ichimoku_kijun"])
    ).astype("int8")

    # Volume
    for period in [20, 50, 100]:
        df[f"volume_sma_{period}"] = sma(v, period)
        df[f"volume_ema_{period}"] = ema(v, period)
        df[f"volume_ratio_sma_{period}"] = v / df[f"volume_sma_{period}"].replace(0, np.nan)
        df[f"volume_ratio_ema_{period}"] = v / df[f"volume_ema_{period}"].replace(0, np.nan)

    df["obv"] = obv(c, v)
    df["obv_slope"] = df["obv"].diff()
    df["mfi_14"] = mfi(h, l, c, v, 14)
    df["cmf_20"] = cmf(h, l, c, v, 20)
    df["adl"] = accumulation_distribution(h, l, c, v)
    df["adl_slope"] = df["adl"].diff()
    df["force_index_13"] = force_index(c, v, 13)
    df["ease_of_movement_14"] = ease_of_movement(h, l, v, 14)

    df["vwap_cumulative"] = cumulative_vwap(h, l, c, v)
    df["dist_to_vwap_cumulative_pct"] = pct_distance(c, df["vwap_cumulative"])

    for period in [20, 50, 100]:
        df[f"vwap_rolling_{period}"] = rolling_vwap(h, l, c, v, period)
        df[f"dist_to_vwap_rolling_{period}_pct"] = pct_distance(
            c,
            df[f"vwap_rolling_{period}"],
        )

    # Taker buy metrics
    if "taker_buy_base_volume" in df.columns:
        df["taker_buy_ratio"] = df["taker_buy_base_volume"] / v.replace(0, np.nan)
        df["taker_buy_ratio_sma_20"] = sma(df["taker_buy_ratio"], 20)

    # Candle structure
    df = add_candle_features(df)

    # Levels
    df = add_rolling_levels(df)

    # Online-safe trend lines
    df = add_trendline_features(df)

    # Cleanup infinities
    df = df.replace([np.inf, -np.inf], np.nan)

    return df


# =========================
# FILE PROCESSING
# =========================

def process_file(symbol: str, timeframe: str) -> None:
    input_path = DATA_DIR / symbol / f"{symbol}_{timeframe}.csv.gz"
    output_dir = OUT_DIR / symbol
    output_path = output_dir / f"{symbol}_{timeframe}_indicators.csv.gz"

    if not input_path.exists():
        print(f"SKIP missing: {input_path}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print(f"PROCESS {symbol} {timeframe}")
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")

    df = pd.read_csv(input_path)

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=True)

    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"])
    df = df.reset_index(drop=True)

    print(f"Rows: {len(df):,}")

    df = add_indicators(df)
    df = downsize_float_columns(df)

    df.to_csv(output_path, index=False, compression="gzip")

    print(f"DONE {symbol} {timeframe}")
    print(f"Columns: {len(df.columns)}")
    print("=" * 100)

    del df
    gc.collect()


def process_all(
    symbols: Iterable[str],
    timeframes: Iterable[str],
) -> None:
    tasks = [(symbol, tf) for symbol in symbols for tf in timeframes]

    for symbol, tf in tqdm(tasks, desc="Generate indicators"):
        try:
            process_file(symbol, tf)
        except Exception as e:
            print(f"ERROR {symbol} {tf}: {e}")


if __name__ == "__main__":
    process_all(SYMBOLS, TIMEFRAMES)