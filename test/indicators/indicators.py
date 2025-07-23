import pandas as pd
import pandas_ta as ta
import os
import glob
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)


def add_critical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def safe_add(name, func):
        try:
            res = func()
            if res is not None and not res.empty:
                if isinstance(res, pd.Series):
                    df[name] = res
                elif isinstance(res, pd.DataFrame):
                    df[name] = res.iloc[:, 0]
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
    except:
        pass

    # === Bollinger Bands ===
    try:
        bb = ta.bbands(df["close"], length=20, std=2)
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_middle"] = bb["BBM_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
    except:
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
    safe_add("mfi_14", lambda: ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14))
    try:
        df["rvol_20"] = df["volume"] / df["volume"].rolling(20).mean()
    except:
        pass

    # === Z-score ===
    try:
        df["zscore_20"] = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).std()
    except:
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

    # === Heikin Ashi ===
    try:
        ha = ta.ha(df["open"], df["high"], df["low"], df["close"])
        df["ha_open"] = ha["HA_open"]
        df["ha_close"] = ha["HA_close"]
    except:
        pass

    df = df.fillna(method="ffill").fillna(method="bfill")
    return df




if __name__ == "__main__":
    base_dir = "test/data/BTCUSDT"
    files = glob.glob(os.path.join(base_dir, "*.csv.gz"))

    for file_path in files:
        if "BTCUSDT_1m" in file_path:
            print(f"⏭ Пропускаємо 1m файл: {file_path}")
            continue

        print(f"📂 Обробка {file_path}...")
        df = pd.read_csv(file_path, header=None)
        df.columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
        ]

        df = df.astype({
            "open": "float64", "high": "float64", "low": "float64",
            "close": "float64", "volume": "float64",
            "quote_asset_volume": "float64",
            "taker_buy_base_volume": "float64",
            "taker_buy_quote_volume": "float64"
        })

        # Додаємо критичні індикатори
        df = add_critical_indicators(df)

        # Зберігаємо
        name = os.path.basename(file_path).replace(".csv.gz", "_critical_indicators.csv")
        output_path = os.path.join(base_dir, name)
        print(f"💾 Збереження у {output_path}")
        df.to_csv(output_path, index=False)
        print(f"✅ Готово: {len(df)} рядків")
