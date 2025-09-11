import numpy as np
import pandas as pd
import pandas_ta as ta
from scipy.signal import argrelextrema

__all__ = ["add_critical_indicators"]

def add_critical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Очікує колонки: open, high, low, close, volume, open_time, close_time (UTC ms).
    Рахує той самий набір індикаторів, що використовувався під час навчання моделей.
    """
    df = df.copy()

    if "open_time" in df.columns:
        df = df.sort_values("open_time").reset_index(drop=True)

    # Нормалізуємо типи — уникнемо dtype warning-ів
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    def safe_add(name, func):
        try:
            res = func()
            if res is None or (isinstance(res, pd.DataFrame) and res.empty):
                return
            s = res.iloc[:, 0] if isinstance(res, pd.DataFrame) else res
            s = pd.to_numeric(s, errors="coerce").astype("float64")
            if name in df.columns:
                df.drop(columns=[name], inplace=True)
            df[name] = pd.Series(index=df.index, dtype="float64")
            df.loc[:, name] = s.values
        except Exception as e:
            print(f"[!] {name} failed: {e}")

    # === Trend & Momentum ===
    safe_add("macd", lambda: ta.macd(df["close"])["MACD_12_26_9"])
    safe_add("stc", lambda: ta.stc(df["close"]))
    safe_add("supertrend", lambda: ta.supertrend(df["high"], df["low"], df["close"])["SUPERT_7_3.0"])
    safe_add("rsi_14", lambda: ta.rsi(df["close"], length=14))

    # === Moving Averages ===
    safe_add("sma_20", lambda: ta.sma(df["close"], length=20))
    safe_add("ema_20", lambda: ta.ema(df["close"], length=20))
    safe_add("wma_20", lambda: ta.wma(df["close"], length=20))
    safe_add("hma_20", lambda: ta.hma(df["close"], length=20))
    safe_add("zl_ema_20", lambda: ta.zlma(df["close"], length=20))

    # === EMA-related ===
    try:
        df["price_from_ema_%"] = (df["close"] - df["ema_20"]) / df["ema_20"] * 100
        df["ema_env"] = df["ema_20"]
        df["ema_env_upper"] = df["ema_env"] * 1.02
        df["ema_env_lower"] = df["ema_env"] * 0.98
    except Exception:
        pass

    # === Bollinger Bands ===
    try:
        bb = ta.bbands(df["close"], length=20, std=2)
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_middle"] = bb["BBM_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
    except Exception:
        pass

    # === SAR ===
    try:
        psar = ta.psar(df["high"], df["low"], df["close"])
        df["sar"] = psar["PSARl_0.02_0.2"]
    except Exception as e:
        print(f"[!] psar failed: {e}")

    # === Oscillators ===
    safe_add("stoch_rsi", lambda: ta.stochrsi(df["close"])["STOCHRSIk_14_14_3_3"])
    safe_add("cci_20", lambda: ta.cci(df["high"], df["low"], df["close"], length=20))
    safe_add("roc_14", lambda: ta.roc(df["close"], length=14))
    safe_add("mom_10", lambda: ta.mom(df["close"], length=10))
    safe_add("williams_r", lambda: ta.willr(df["high"], df["low"], df["close"], length=14))
    safe_add("fisher", lambda: ta.fisher(df["high"], df["low"], length=9))

    # === Volume & Flow ===
    safe_add("obv", lambda: ta.obv(df["close"], df["volume"]))
    safe_add("ad", lambda: ta.ad(df["high"], df["low"], df["close"], df["volume"]))
    # важливо: volume у float64, щоб уникнути dtype warning
    safe_add("mfi_14", lambda: ta.mfi(df["high"], df["low"], df["close"], df["volume"].astype("float64"), length=14))
    try:
        df["rvol_20"] = df["volume"] / df["volume"].rolling(20).mean()
    except Exception:
        pass

    # === Z-score ===
    try:
        df["zscore_20"] = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).std()
    except Exception:
        pass

    # === Pivot Points ===
    try:
        pivot = (df["high"] + df["low"] + df["close"]) / 3
        df["pivot"] = pivot
        df["r1"] = 2 * pivot - df["low"]
        df["s1"] = 2 * pivot - df["high"]
        df["r2"] = pivot + (df["high"] - df["low"])
        df["s2"] = pivot - (df["high"] - df["low"])
    except Exception as e:
        print(f"[!] pivot levels failed: {e}")

    # === Heikin Ashi — без pandas_ta.ha (щоб не було chained assignment warning) ===
    try:
        HA_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        HA_open = HA_close.copy()
        if len(HA_open) > 0:
            HA_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
            for i in range(1, len(HA_open)):
                HA_open.iloc[i] = 0.5 * (HA_open.iloc[i - 1] + HA_close.iloc[i - 1])
        HA_high = pd.concat([df["high"], HA_open, HA_close], axis=1).max(axis=1)
        HA_low  = pd.concat([df["low"],  HA_open, HA_close], axis=1).min(axis=1)

        # створюємо цільові колонки як float64 і пишемо через .loc
        for name, series in (("ha_open", HA_open), ("ha_close", HA_close), ("ha_high", HA_high), ("ha_low", HA_low)):
            if name in df.columns:
                df.drop(columns=[name], inplace=True)
            df[name] = pd.Series(index=df.index, dtype="float64")
            df.loc[:, name] = pd.to_numeric(series, errors="coerce").astype("float64").values
    except Exception:
        # на випадок, якщо бракує даних — не падаємо
        pass

    # === Trend Lines (Up & Down) — як у твоєму пайплайні ===
    def extract_trend_line(series, order=10, max_deviation=0.01, max_distance=50):
        idx = argrelextrema(series.values, np.less_equal if series.name == "low" else np.greater_equal, order=order)[0]
        if len(idx) < 2:
            return 0, 0.0, 0, 0.0, 0.0, 0, 0
        x = idx[-2:]
        y = series.iloc[x].values
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

    try:
        up_valid, up_slope, up_conf, up_angle, up_dist, up_break_above, up_break_below = extract_trend_line(df["low"])
        down_valid, down_slope, down_conf, down_angle, down_dist, down_break_above, down_break_below = extract_trend_line(df["high"])

        df["trend_up_valid"] = up_valid
        df["trend_up_slope"] = up_slope
        df["trend_up_confirmed"] = up_conf
        df["trend_up_angle_deg"] = up_angle
        df["trend_up_distance"] = up_dist
        df["trend_up_break_above"] = up_break_above
        df["trend_up_break_below"] = up_break_below

        df["trend_down_valid"] = down_valid
        df["trend_down_slope"] = down_slope
        df["trend_down_confirmed"] = down_conf
        df["trend_down_angle_deg"] = down_angle
        df["trend_down_distance"] = down_dist
        df["trend_down_break_above"] = down_break_above
        df["trend_down_break_below"] = down_break_below

        def classify_trend(row, threshold=0.001):
            if row["trend_up_valid"] and row["trend_up_slope"] > threshold:
                return "uptrend"
            elif row["trend_down_valid"] and row["trend_down_slope"] < -threshold:
                return "downtrend"
            elif row["trend_up_valid"] and row["trend_down_valid"] and \
                 abs(row["trend_up_slope"]) <= threshold and abs(row["trend_down_slope"]) <= threshold:
                return "flat"
            else:
                return "undefined"

        df["trend_type"] = df.apply(classify_trend, axis=1)

    except Exception as e:
        print(f"[!] trend line detection failed: {e}")

    # фінальне заповнення без застарілого синтаксису
    df = df.replace([np.inf, -np.inf], np.nan).ffill().bfill()
    return df
