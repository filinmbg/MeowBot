# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
try:
    import pandas_ta as ta
except Exception:
    ta = None
try:
    from scipy.signal import argrelextrema
except Exception:
    argrelextrema = None  # трендлайни зробимо опційними

__all__ = ["add_critical_indicators"]

# ----------------- helpers -----------------
def _as_float_series(vals, index) -> pd.Series:
    """Привести будь-що до Series(float64) з правильним index."""
    if isinstance(vals, pd.Series):
        s = vals.reindex(index)
        return pd.to_numeric(s, errors="coerce").astype("float64")
    if isinstance(vals, pd.DataFrame):
        s = vals.iloc[:, 0].reindex(index)
        return pd.to_numeric(s, errors="coerce").astype("float64")
    return pd.Series(vals, index=index, dtype="float64")


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1/length, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _mfi_manual(high: pd.Series, low: pd.Series, close: pd.Series,
                volume: pd.Series, length: int = 14) -> pd.Series:
    """
    Money Flow Index (ручна реалізація, 0..100).
    Уникає внутрішніх присвоєнь pandas_ta -> без FutureWarning.
    """
    tp = (high + low + close) / 3.0
    rmf = tp * volume  # raw money flow

    # розділяємо на позитивний/негативний потоки
    up = tp > tp.shift(1)
    dn = tp < tp.shift(1)
    pmf = rmf.where(up, 0.0)
    nmf = rmf.where(dn, 0.0)

    pmf_sum = pmf.rolling(length, min_periods=1).sum()
    nmf_sum = nmf.rolling(length, min_periods=1).sum()
    mfr = pmf_sum / nmf_sum.replace(0.0, np.nan)  # money flow ratio
    mfi = 100 - (100 / (1 + mfr))
    return mfi.astype("float64")


# ----------------- main -----------------
def add_critical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Очікує колонки: open, high, low, close, volume, open_time, close_time (UTC ms).
    Повертає копію df з доданими індикаторами. Усі числові індикатори -> float64.
    """
    df = df.copy()
    if "open_time" in df.columns:
        df = df.sort_values("open_time").reset_index(drop=True)

    # 1) Нормалізуємо типи OHLCV
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    # 2) Безпечне додавання колонки:
    #    використовуємо df.insert(...), а не df[name]= / df.loc[:,name]=,
    #    щоб уникнути конфліктів із int-блоками та FutureWarning.
    def safe_add(name: str, fn):
        try:
            res = fn()
            s = _as_float_series(res, df.index).astype("float64")
        except Exception:
            s = pd.Series(np.nan, index=df.index, dtype="float64")
        if name in df.columns:
            df.drop(columns=[name], inplace=True)
        df.insert(len(df.columns), name, s)

    close = df["close"]

    # === MA / EMA / MACD ======================================================
    safe_add("ema12", lambda: close.ewm(span=12, adjust=False).mean())
    safe_add("ema26", lambda: close.ewm(span=26, adjust=False).mean())
    if ta is not None and hasattr(ta, "macd"):
        macd_df = ta.macd(close)
        safe_add("macd",        lambda: macd_df["MACD_12_26_9"])
        safe_add("macd_signal", lambda: macd_df["MACDs_12_26_9"])
        safe_add("macd_hist",   lambda: macd_df["MACDh_12_26_9"])
    else:
        macd = df["ema12"] - df["ema26"]
        signal = macd.ewm(span=9, adjust=False).mean()
        safe_add("macd",        lambda: macd)
        safe_add("macd_signal", lambda: signal)
        safe_add("macd_hist",   lambda: macd - signal)

    # === RSI ==================================================================
    if ta is not None and hasattr(ta, "rsi"):
        safe_add("rsi_14", lambda: ta.rsi(close, length=14))
    else:
        safe_add("rsi_14", lambda: _rsi(close, 14))

    # === STC / Supertrend (якщо є pandas_ta) ==================================
    if ta is not None:
        if hasattr(ta, "stc"):
            safe_add("stc", lambda: ta.stc(close))
        if hasattr(ta, "supertrend"):
            st = ta.supertrend(df["high"], df["low"], close)
            col = next((c for c in st.columns if c.startswith("SUPERT_")), st.columns[0])
            safe_add("supertrend", lambda: st[col])

    # === Ковзні середні для фіч ===============================================
    if ta is not None:
        safe_add("sma_20",    lambda: ta.sma(close, length=20))
        safe_add("ema_20",    lambda: ta.ema(close, length=20))
        safe_add("wma_20",    lambda: ta.wma(close, length=20))
        safe_add("hma_20",    lambda: ta.hma(close, length=20))
        safe_add("zl_ema_20", lambda: ta.zlma(close, length=20))
    else:
        safe_add("sma_20",    lambda: close.rolling(20, min_periods=1).mean())
        safe_add("ema_20",    lambda: close.ewm(span=20, adjust=False).mean())

    # EMA environment + відсоткове відхилення
    safe_add("price_from_ema_%", lambda: (df["close"] - df["ema_20"]) / df["ema_20"] * 100.0)
    safe_add("ema_env",          lambda: df["ema_20"])
    safe_add("ema_env_upper",    lambda: df["ema_env"] * 1.02)
    safe_add("ema_env_lower",    lambda: df["ema_env"] * 0.98)

    # === Bollinger Bands =======================================================
    if ta is not None and hasattr(ta, "bbands"):
        bb = ta.bbands(close, length=20, std=2)
        safe_add("bb_upper",  lambda: bb["BBU_20_2.0"])
        safe_add("bb_middle", lambda: bb["BBM_20_2.0"])
        safe_add("bb_lower",  lambda: bb["BBL_20_2.0"])
    else:
        ma20 = close.rolling(20, min_periods=1).mean()
        std20 = close.rolling(20, min_periods=1).std(ddof=0)
        safe_add("bb_middle", lambda: ma20)
        safe_add("bb_upper",  lambda: ma20 + 2 * std20)
        safe_add("bb_lower",  lambda: ma20 - 2 * std20)

    # === SAR ===================================================================
    if ta is not None and hasattr(ta, "psar"):
        psar = ta.psar(df["high"], df["low"], close)
        if isinstance(psar, pd.DataFrame):
            col = next((c for c in psar.columns if c.startswith("PSARl")), psar.columns[0])
            safe_add("sar", lambda: psar[col])
        else:
            safe_add("sar", lambda: psar)

    # === Oscillators ===========================================================
    if ta is not None:
        if hasattr(ta, "stochrsi"):
            safe_add("stoch_rsi",  lambda: ta.stochrsi(close)["STOCHRSIk_14_14_3_3"])
        if hasattr(ta, "cci"):
            safe_add("cci_20",     lambda: ta.cci(df["high"], df["low"], close, length=20))
        if hasattr(ta, "roc"):
            safe_add("roc_14",     lambda: ta.roc(close, length=14))
        if hasattr(ta, "mom"):
            safe_add("mom_10",     lambda: ta.mom(close, length=10))
        if hasattr(ta, "willr"):
            safe_add("williams_r", lambda: ta.willr(df["high"], df["low"], close, length=14))
        if hasattr(ta, "fisher"):
            safe_add("fisher",     lambda: ta.fisher(df["high"], df["low"], length=9))

    # === Volume & Flow =========================================================
    if ta is not None and hasattr(ta, "obv"):
        safe_add("obv", lambda: ta.obv(close, df["volume"]))
    if ta is not None and hasattr(ta, "ad"):
        safe_add("ad",  lambda: ta.ad(df["high"], df["low"], close, df["volume"]))

    # --- MFI(14): тільки ручна реалізація (без pandas_ta), щоб прибрати FutureWarning
    safe_add("mfi_14", lambda: _mfi_manual(df["high"], df["low"], close, df["volume"].astype("float64"), length=14))

    # Relative Volume
    safe_add("rvol_20", lambda: df["volume"] / df["volume"].rolling(20).mean())

    # === Z-score ===============================================================
    safe_add("zscore_20", lambda: (close - close.rolling(20).mean()) / close.rolling(20).std(ddof=0))

    # === Pivot Levels ==========================================================
    safe_add("pivot", lambda: (df["high"] + df["low"] + close) / 3.0)
    safe_add("r1",    lambda: 2.0 * df["pivot"] - df["low"])
    safe_add("s1",    lambda: 2.0 * df["pivot"] - df["high"])
    safe_add("r2",    lambda: df["pivot"] + (df["high"] - df["low"]))
    safe_add("s2",    lambda: df["pivot"] - (df["high"] - df["low"]))

    # === Heikin Ashi (без chained assignment) =================================
    try:
        HA_close = (df["open"] + df["high"] + df["low"] + close) / 4.0
        HA_open = HA_close.copy()
        if len(HA_open) > 0:
            HA_open.iloc[0] = (df["open"].iloc[0] + close.iloc[0]) / 2.0
            for i in range(1, len(HA_open)):
                HA_open.iloc[i] = 0.5 * (HA_open.iloc[i - 1] + HA_close.iloc[i - 1])
        HA_high = pd.concat([df["high"], HA_open, HA_close], axis=1).max(axis=1)
        HA_low  = pd.concat([df["low"],  HA_open, HA_close], axis=1).min(axis=1)

        safe_add("ha_open",  lambda: HA_open)
        safe_add("ha_close", lambda: HA_close)
        safe_add("ha_high",  lambda: HA_high)
        safe_add("ha_low",   lambda: HA_low)
    except Exception:
        pass

    # === Trend Lines (Up & Down) ==============================================
    if argrelextrema is not None:
        def _extract_trend_line(series, is_low: bool, order=10, max_deviation=0.01, max_distance=50):
            idx = argrelextrema(series.values, np.less_equal if is_low else np.greater_equal, order=order)[0]
            if len(idx) < 2:
                return 0, 0.0, 0, 0.0, 0.0, 0, 0
            x = idx[-2:]; y = series.iloc[x].values
            slope, intercept = np.polyfit(x, y, 1)
            if x[1] - x[0] > max_distance:
                return 0, 0.0, 0, 0.0, 0.0, 0, 0
            confirmed = 0
            for i in idx[:-2]:
                pred = slope * i + intercept
                actual = series.iloc[i]
                deviation = abs(actual - pred) / (abs(pred) + 1e-8)
                if deviation <= max_deviation:
                    confirmed = 1
                    break
            i = len(series) - 1
            price_now = series.iloc[i]
            trend_now = slope * i + intercept
            distance = price_now - trend_now
            angle_deg = np.degrees(np.arctan(slope))
            prev_price = series.iloc[i - 1] if i >= 1 else price_now
            prev_trend = slope * (i - 1) + intercept if i >= 1 else trend_now
            break_above = int(price_now > trend_now and prev_price <= prev_trend)
            break_below = int(price_now < trend_now and prev_price >= prev_trend)
            return 1, slope, confirmed, angle_deg, distance, break_above, break_below

        up = _extract_trend_line(df["low"],  is_low=True)
        dn = _extract_trend_line(df["high"], is_low=False)

        # усе додаємо як float64 через safe_add
        safe_add("trend_up_valid",        lambda: float(up[0]))
        safe_add("trend_up_slope",        lambda: float(up[1]))
        safe_add("trend_up_confirmed",    lambda: float(up[2]))
        safe_add("trend_up_angle_deg",    lambda: float(up[3]))
        safe_add("trend_up_distance",     lambda: float(up[4]))
        safe_add("trend_up_break_above",  lambda: float(up[5]))
        safe_add("trend_up_break_below",  lambda: float(up[6]))

        safe_add("trend_down_valid",      lambda: float(dn[0]))
        safe_add("trend_down_slope",      lambda: float(dn[1]))
        safe_add("trend_down_confirmed",  lambda: float(dn[2]))
        safe_add("trend_down_angle_deg",  lambda: float(dn[3]))
        safe_add("trend_down_distance",   lambda: float(dn[4]))
        safe_add("trend_down_break_above",lambda: float(dn[5]))
        safe_add("trend_down_break_below",lambda: float(dn[6]))

        # текстова класифікація (object)
        def classify_trend(row, thr=0.001):
            if row["trend_up_valid"] and row["trend_up_slope"] > thr:
                return "uptrend"
            if row["trend_down_valid"] and row["trend_down_slope"] < -thr:
                return "downtrend"
            if row["trend_up_valid"] and row["trend_down_valid"] and \
               abs(row["trend_up_slope"]) <= thr and abs(row["trend_down_slope"]) <= thr:
                return "flat"
            return "undefined"
        df["trend_type"] = df.apply(classify_trend, axis=1)

    # фінальне очищення
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.ffill().bfill()
    return df
