import os
import pandas as pd
import numpy as np
from tqdm import tqdm


def calculate_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def add_adaptive_targets(df, atr_multiplier_tp=0.5, atr_multiplier_sl=1.0, lookahead=10):
    atr = calculate_atr(df)
    df["ATR"] = atr

    df["target_long"] = 0
    df["target_short"] = 0

    for idx in tqdm(range(len(df) - lookahead)):
        entry_price = df.loc[idx, "close"]
        atr_value = df.loc[idx, "ATR"]

        if pd.isna(atr_value):
            continue

        tp_long = entry_price + atr_value * atr_multiplier_tp
        sl_long = entry_price - atr_value * atr_multiplier_sl

        tp_short = entry_price - atr_value * atr_multiplier_tp
        sl_short = entry_price + atr_value * atr_multiplier_sl

        future_data = df.iloc[idx + 1: idx + lookahead + 1]

        # LONG
        high_hit = (future_data["high"] >= tp_long).any()
        low_hit = (future_data["low"] <= sl_long).any()
        if high_hit and not low_hit:
            df.at[idx, "target_long"] = 1

        # SHORT
        low_hit = (future_data["low"] <= tp_short).any()
        high_hit = (future_data["high"] >= sl_short).any()
        if low_hit and not high_hit:
            df.at[idx, "target_short"] = 1

    return df


def process_file(file_path, output_dir):
    df = pd.read_csv(file_path)
    df.columns = [col.strip().lower() for col in df.columns]

    df = add_adaptive_targets(df)

    filename = os.path.basename(file_path)
    base, ext = os.path.splitext(filename)

    out_long = os.path.join(output_dir, f"{base}_with_targets_long.csv")
    out_short = os.path.join(output_dir, f"{base}_with_targets_short.csv")

    df.to_csv(out_long, index=False, columns=list(df.columns[:-2]) + ["target_long"])
    df.to_csv(out_short, index=False, columns=list(df.columns[:-2]) + ["target_short"])

    print(f"✅ Збережено: {out_long}")
    print(f"✅ Збережено: {out_short}")


def main():
    input_dir = "test/data/BTCUSDT"
    output_dir = "test/data/BTCUSDT"

    for file in os.listdir(input_dir):
        if not file.endswith("_indicators.csv"):
            continue
        print(f"📂 Обробка: {file}")
        try:
            process_file(os.path.join(input_dir, file), output_dir)
        except Exception as e:
            print(f"❌ Помилка обробки {file}: {e}")


if __name__ == "__main__":
    main()
