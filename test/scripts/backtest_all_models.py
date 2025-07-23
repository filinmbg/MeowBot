import os
import json
import joblib
import pandas as pd
import numpy as np
import torch
from model import CNNLSTMClassifier
from config import CONFIG

MODEL_DIR = "Models/CNN"
TEST_CSV = "test/data/BTCUSDT/BTCUSDT_1m_indicators.csv"
START_DATE = "2022-01-01"
SEQ_LEN = 30
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def load_minute_data():
    df = pd.read_csv(TEST_CSV)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df[df["open_time"] >= pd.to_datetime(START_DATE)]
    return df

def main():
    df_minute = load_minute_data()
    results = []

    for tf in CONFIG["timeframes"]:
        for target in CONFIG["targets"]:
            print(f"\n🔍 Тестуємо модель {tf}_{target}")

            model_path = os.path.join(MODEL_DIR, f"cnn_lstm_{tf}_{target}.pt")
            scaler_path = os.path.join(MODEL_DIR, f"scaler_{tf}_{target}.pkl")
            features_path = os.path.join(MODEL_DIR, f"features_{tf}_{target}.json")
            config_path = os.path.join(MODEL_DIR, f"model_config_{tf}_{target}.json")

            if not all(map(os.path.exists, [model_path, scaler_path, features_path, config_path])):
                print(f"❌ Пропущено {tf}_{target} — модель або дані не знайдено")
                continue

            # Load config
            with open(config_path) as f:
                cfg = json.load(f)

            model = CNNLSTMClassifier(
                input_length=cfg["input_size"],
                cnn_channels=cfg["cnn_channels"],
                lstm_hidden=cfg["lstm_hidden"],
                fc_hidden=cfg["fc_hidden"],
                fc_out=cfg["fc_out"]
            ).to(DEVICE)

            model.load_state_dict(torch.load(model_path))
            model.eval()

            # Load scaler + features
            scaler = joblib.load(scaler_path)
            features = json.load(open(features_path))

            df = df_minute.copy()
            df = df.dropna()
            if len(df) < SEQ_LEN + 1:
                print(f"⚠️ Пропущено {tf}_{target} — недостатньо даних")
                continue

            X = df[features].values
            X_scaled = scaler.transform(X)

            # останній трейд
            seq = X_scaled[-SEQ_LEN:]
            X_input = torch.tensor(seq, dtype=torch.float32).unsqueeze(0).to(DEVICE)
            pred = model(X_input).argmax().item()

            # Симуляція трейду
            trade_open_time = df.iloc[-1]["open_time"]
            direction = "LONG" if (target == "target_long" and pred == 1) else \
                        "SHORT" if (target == "target_short" and pred == 1) else None

            if not direction:
                print(f"ℹ️ Модель не відкрила трейд")
                continue

            start_price = df.iloc[-1]["close"]
            future_df = df_minute[df_minute["open_time"] > trade_open_time].copy()

            result = {"model": f"{tf}_{target}", "direction": direction, "start_price": start_price}
            deposit = 100
            risk_per_trade = deposit * 0.01 * 20

            for i, row in future_df.iterrows():
                high = row["high"]
                low = row["low"]

                if direction == "LONG":
                    if high >= start_price * 1.005:
                        result["result"] = "win"
                        deposit += risk_per_trade * 0.005
                        break
                    elif low <= start_price * 0.98:
                        result["result"] = "loss"
                        deposit -= risk_per_trade * 0.02
                        break

                elif direction == "SHORT":
                    if low <= start_price * 0.995:
                        result["result"] = "win"
                        deposit += risk_per_trade * 0.005
                        break
                    elif high >= start_price * 1.02:
                        result["result"] = "loss"
                        deposit -= risk_per_trade * 0.02
                        break

            else:
                result["result"] = "no_exit"

            result["final_deposit"] = deposit
            results.append(result)

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        summary = df_results.groupby("model").agg(
            trades=("result", "count"),
            wins=("result", lambda x: (x == "win").sum()),
            losses=("result", lambda x: (x == "loss").sum()),
            winrate=("result", lambda x: round((x == "win").sum() / len(x) * 100, 2)),
            final_deposit=("final_deposit", "last")
        ).reset_index()

        print("\n📈 Зведена статистика по моделях:")
        print(summary)

        summary.to_csv(os.path.join(MODEL_DIR, "backtest_summary.csv"), index=False)
    else:
        print("⚠️ Немає даних для зведеної статистики.")

if __name__ == "__main__":
    main()
