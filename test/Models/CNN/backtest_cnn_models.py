import os
import gzip
import pandas as pd
import numpy as np
import tensorflow as tf
from tqdm import tqdm
from sklearn.preprocessing import StandardScaler
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

# ⚙️ Конфіг
TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m']
TARGETS = ['target_long', 'target_short']
MODEL_DIR = 'Models/CNN'
DATA_DIR = 'test/data/BTCUSDT'
PLOT_DIR = 'test/plots'
ONE_MIN_DATA = os.path.join(DATA_DIR, 'BTCUSDT_1m.csv.gz')

START_DEPOSIT = 100
RISK_PERCENT = 0.01
LEVERAGE = 50
TEST_YEARS = 2

# 📁 Папка для графіків
os.makedirs(PLOT_DIR, exist_ok=True)

# 📥 1хв дані
def load_1m_data():
    with gzip.open(ONE_MIN_DATA, 'rt') as f:
        df = pd.read_csv(f, header=None)
    df.columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
    ]
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce')
    return df

# 🕒 Фільтрація
def filter_recent_data(df):
    cutoff = datetime.now() - timedelta(days=365 * TEST_YEARS)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce')
    return df[df['open_time'] >= cutoff]

# 📉 Візуалізація трейду
def plot_trade(df, entry_time, entry_price, exit_price, direction, result, filename):
    df = df[df["open_time"] >= entry_time].copy().head(100)
    df.set_index("open_time", inplace=True)
    plt.figure(figsize=(10, 4))
    plt.plot(df["close"], label="Close Price")
    plt.axhline(entry_price, color='blue', linestyle='--', label="Entry")
    plt.axhline(exit_price, color='red' if result == "SL" else 'green', linestyle='--', label="Exit")
    plt.title(f"{direction.upper()} - {result}")
    plt.legend()
    plt.savefig(filename)
    plt.close()

# 📈 TP/SL логіка
def get_exit_price(entry_time, direction, entry_price, df_1m, plot_path):
    tp_levels = [0.005, 0.010, 0.015, 0.020]
    sl_levels = [-0.02, 0.0, 0.002, 0.004]

    sl_price = entry_price * (1 + sl_levels[0] if direction == "long" else 1 - sl_levels[0])
    tp_stage = 0
    take_profits = 0

    df_after = df_1m[df_1m["open_time"] > entry_time].copy()
    if df_after.empty:
        return entry_price, "NONE", None

    for _, row in df_after.iterrows():
        high, low, time = row["high"], row["low"], row["open_time"]

        if direction == "long":
            if high >= entry_price * (1 + tp_levels[tp_stage]):
                take_profits += 1
                tp_stage += 1
                if tp_stage < len(sl_levels):
                    sl_price = entry_price * (1 + sl_levels[tp_stage])
            elif low <= sl_price:
                plot_trade(df_1m, entry_time, entry_price, sl_price, direction, "SL", plot_path)
                return sl_price, "TP" if take_profits else "SL", time
        else:
            if low <= entry_price * (1 - tp_levels[tp_stage]):
                take_profits += 1
                tp_stage += 1
                if tp_stage < len(sl_levels):
                    sl_price = entry_price * (1 - sl_levels[tp_stage])
            elif high >= sl_price:
                plot_trade(df_1m, entry_time, entry_price, sl_price, direction, "SL", plot_path)
                return sl_price, "TP" if take_profits else "SL", time

        if tp_stage == 4:
            final_tp = entry_price * (1 + tp_levels[3] if direction == "long" else 1 - tp_levels[3])
            plot_trade(df_1m, entry_time, entry_price, final_tp, direction, "TP", plot_path)
            return final_tp, "TP", time

    return entry_price, "NONE", None

# 🧪 Бектест моделі
def backtest_model(model_path, data_path, target_name, df_1m, timeframe, threshold=0.4):
    model = tf.keras.models.load_model(model_path)
    df = pd.read_csv(data_path)
    df = filter_recent_data(df)
    df.dropna(inplace=True)

    if df.empty:
        return None

    drop_cols = ["open_time", "close_time", "target_long", "target_short"]
    drop_cols = [col for col in drop_cols if col in df.columns]
    drop_cols += ["open"]  # виключаємо open як зайву фічу
    X = df.drop(columns=drop_cols)

    expected = model.input_shape[-1]
    if X.shape[1] != expected:
        print(f"❌ {os.path.basename(model_path)}: Очікується {expected} фіч, а є {X.shape[1]}")
        print(f"🧾 Колонки: {list(X.columns)}")
        return None

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X).reshape((-1, 1, X.shape[1]))

    preds = model.predict(X_scaled).flatten()
    predictions = (preds > threshold).astype(int)

    print(f"📊 Очікується {expected} фіч, маємо {X.shape[1]}")

    deposit = START_DEPOSIT
    trades = wins = losses = 0
    last_exit_time = None
    direction = "long" if target_name == "target_long" else "short"

    for i in tqdm(range(len(df)), desc=f"{os.path.basename(model_path)}", leave=False):
        if predictions[i] != 1:
            continue

        entry_time = pd.to_datetime(df.iloc[i]["open_time"])
        if last_exit_time and entry_time <= last_exit_time:
            continue

        entry_price = df.iloc[i]["close"]
        plot_path = os.path.join(PLOT_DIR, f"{os.path.basename(model_path)}_{i}.png")
        exit_price, result, exit_time = get_exit_price(entry_time, direction, entry_price, df_1m, plot_path)

        if result == "NONE":
            continue

        position_size = deposit * RISK_PERCENT * LEVERAGE
        pnl = (exit_price - entry_price) / entry_price if direction == "long" else (entry_price - exit_price) / entry_price
        deposit += position_size * pnl

        trades += 1
        wins += (1 if result == "TP" else 0)
        losses += (1 if result == "SL" and not wins else 0)

        last_exit_time = exit_time

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate": (wins / trades * 100) if trades else 0.0,
        "final_deposit": deposit
    }

# 🚀 Основна функція
def main():
    df_1m = load_1m_data()
    results = []

    for tf in TIMEFRAMES:
        for target in TARGETS:
            model_path = os.path.join(MODEL_DIR, f"cnn_{tf}_{target}.keras")
            data_path = os.path.join(DATA_DIR, f"BTCUSDT_{tf}_critical_indicators.csv")
            print(f"\n🧪 Бектест: {tf} - {target}")
            try:
                stats = backtest_model(model_path, data_path, target, df_1m, tf)
                if stats:
                    print(f"✅ Результат: {stats}")
                    results.append({
                        "timeframe": tf,
                        "target": target,
                        **stats
                    })
                else:
                    print("⚠️  Нема сигналів")
            except Exception as e:
                print(f"❌ Помилка {tf} {target}: {e}")

    df_results = pd.DataFrame(results)
    print("\n📈 Підсумкова статистика:")
    print(df_results)

if __name__ == "__main__":
    main()
