import os
import gzip
import torch
import pandas as pd
import numpy as np
from torch import nn
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

MODEL_DIR = "Models/LSTM/saved_models"
DATA_DIR = "test/data/BTCUSDT"
RESULT_CSV = "Models/LSTM/lstm_backtest_results.csv"
M1_FILE = os.path.join(DATA_DIR, "BTCUSDT_1m.csv.gz")
BALANCE_INIT = 100.0
RISK_PCT = 0.01
LEVERAGE = 20
TP_PCT = 0.01
SL_PCT = 0.02
MAX_WORKERS = 5
BATCH_SIZE = 1024

class CNNLSTMModel(nn.Module):
    def __init__(self, input_size):
        super().__init__()
        self.conv1 = nn.Conv1d(1, 16, kernel_size=3, padding=1)
        self.relu = nn.ReLU()
        self.lstm = nn.LSTM(input_size=16, hidden_size=32, batch_first=True)
        self.fc = nn.Linear(32, 1)

    def forward(self, x):
        x = x.unsqueeze(1)
        x = self.relu(self.conv1(x))
        x = x.permute(0, 2, 1)
        _, (hn, _) = self.lstm(x)
        return self.fc(hn[-1]).squeeze()

def load_m1_data():
    df = pd.read_csv(M1_FILE, compression="gzip")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("time", inplace=True)
    return df

def backtest_model(model_path):
    try:
        name = os.path.basename(model_path).replace(".pt", "")
        _, timeframe, target, variant = name.split("_")
        csv_path = os.path.join(DATA_DIR, f"BTCUSDT_{timeframe}_critical_indicators.csv")
        if not os.path.exists(csv_path):
            return {"model": name, "status": "❌ no critical CSV"}

        df = pd.read_csv(csv_path).dropna()

        # Обробка часу
        if "open_time" in df.columns:
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        elif "time" in df.columns:
            df["open_time"] = pd.to_datetime(df["time"])
        elif "timestamp" in df.columns:
            df["open_time"] = pd.to_datetime(df["timestamp"])
        else:
            return {"model": name, "status": "💥 no timestamp column"}

        # Фільтр по останніх 2 роках
        cutoff = pd.Timestamp.now() - timedelta(days=730)
        df = df[df["open_time"] >= cutoff]
        df_full = df.copy()
        if df.empty:
            return {"model": name, "signals": 0, "trades": 0, "status": "🚫 no data last 2y"}

        print(f"🕒 {name} open_time min: {df['open_time'].min()}, max: {df['open_time'].max()}, len: {len(df)}")

        X = df.select_dtypes(include=[np.number])
        print(f"📐 {name} X shape: {X.shape}")

        model = CNNLSTMModel(input_size=X.shape[1])
        model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
        model.eval()

        preds = []
        with torch.no_grad():
            for i in range(0, len(X), BATCH_SIZE):
                batch = torch.tensor(X.values[i:i+BATCH_SIZE], dtype=torch.float32)
                batch_preds = torch.sigmoid(model(batch)).numpy()
                preds.extend(batch_preds)
        preds = np.array(preds)

        print(f"📈 {name} preds: min={preds.min():.4f}, max={preds.max():.4f}, mean={preds.mean():.4f}")

        df_full["signal"] = (preds > 0.3).astype(int)

        # Перевірка на NaT
        ot_min = df_full["open_time"].min()
        if pd.isnull(ot_min):
            return {"model": name, "signals": 0, "trades": 0, "status": "💥 open_time is NaT"}

        m1 = load_m1_data()
        m1 = m1[m1.index >= ot_min]

        open_trade = False
        trades_data = []
        capital = BALANCE_INIT

        for _, row in df_full.iterrows():
            if row["signal"] != 1 or open_trade:
                continue

            entry_time = row["open_time"]
            entry_price = row["open"]

            direction = 1 if target == "long" else -1
            tp = entry_price * (1 + TP_PCT * direction)
            sl = entry_price * (1 - SL_PCT * direction)

            m1_trade = m1[m1.index > entry_time]
            if m1_trade.empty:
                continue

            for bar_time, bar in m1_trade.iterrows():
                price_high = bar["high"]
                price_low = bar["low"]
                result = None
                exit_price = None

                if direction == 1:
                    if price_high >= tp:
                        exit_price = tp
                        result = "win"
                    elif price_low <= sl:
                        exit_price = sl
                        result = "loss"
                else:
                    if price_low <= tp:
                        exit_price = tp
                        result = "win"
                    elif price_high >= sl:
                        exit_price = sl
                        result = "loss"

                if result:
                    pnl_pct = (exit_price - entry_price) / entry_price * direction
                    pnl_usd = LEVERAGE * RISK_PCT * capital * pnl_pct
                    capital += pnl_usd
                    trades_data.append({
                        "entry_time": entry_time,
                        "exit_time": bar_time,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "result": result,
                        "pnl_%": round(pnl_pct * 100, 2),
                        "pnl_$": round(pnl_usd, 2)
                    })
                    open_trade = False
                    break

            open_trade = True

        if not trades_data:
            return {"model": name, "signals": int(df_full["signal"].sum()), "trades": 0, "status": "🚫 no trades"}

        df_trades = pd.DataFrame(trades_data)
        wins = df_trades[df_trades["result"] == "win"]
        losses = df_trades[df_trades["result"] == "loss"]
        winrate = len(wins) / len(df_trades) * 100

        return {
            "model": name,
            "timeframe": timeframe,
            "target": target,
            "variant": variant,
            "signals": int(df_full["signal"].sum()),
            "trades": len(df_trades),
            "wins": len(wins),
            "losses": len(losses),
            "winrate_%": round(winrate, 2),
            "final_balance": round(capital, 2),
            "avg_pnl_%": round(df_trades["pnl_%"].mean(), 2),
            "avg_pnl_$": round(df_trades["pnl_$"].mean(), 2),
            "status": "✅"
        }

    except Exception as e:
        return {"model": os.path.basename(model_path), "status": f"💥 {str(e)}", "signals": 0, "trades": 0}





def main():
    model_paths = [os.path.join(MODEL_DIR, f) for f in os.listdir(MODEL_DIR) if f.endswith(".pt")]
    results = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(backtest_model, path): path for path in model_paths}
        for future in as_completed(futures):
            res = future.result()
            print(f"🧠 {res['model']}: signals={res.get('signals', 0)}, trades={res.get('trades', 0)} → {res['status']}")
            results.append(res)

    pd.DataFrame(results).to_csv(RESULT_CSV, index=False)
    print(f"\n📊 Результати збережено в {RESULT_CSV}")

if __name__ == "__main__":
    main()
