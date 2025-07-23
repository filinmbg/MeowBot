import os
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from datetime import datetime, timedelta
from tqdm import tqdm

# 🔧 Конфігурація
TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m']
TARGETS = ['target_long', 'target_short']
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/LSTM'
MINUTE_DATA = 'test/data/BTCUSDT/BTCUSDT_1m.csv.gz'
START_DEPOSIT = 100
TRADE_PCT = 0.01
LEVERAGE = 20
TEST_YEARS = 2
SEQ_LEN = 20

# 🧠 LSTM-клас
class LSTMClassifier(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, output_dim=2, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        _, (hn, _) = self.lstm(x)
        return self.fc(hn[-1])

# 📥 Завантаження моделі без попередження
def load_model(tf, target, input_dim):
    model = LSTMClassifier(input_dim=input_dim)
    path = os.path.join(MODEL_DIR, f"lstm_{tf}_{target}.pt")
    model.load_state_dict(torch.load(path, weights_only=True))
    model.eval()
    return model

# 🔮 Прогноз моделі
def predict_entry(model, X):
    with torch.no_grad():
        output = model(torch.tensor(X, dtype=torch.float32))
        return torch.argmax(output, dim=1).numpy()

# 🚀 Бектест
def run_backtest():
    stats = []
    model_stats = []

    columns = [
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ]
    df_1m_full = pd.read_csv(MINUTE_DATA, names=columns, header=None)
    df_1m_full['timestamp'] = pd.to_datetime(df_1m_full['open_time'], unit='ms')

    min_date = datetime.now() - timedelta(days=365*TEST_YEARS)
    combinations = [(tf, target) for tf in TIMEFRAMES for target in TARGETS]

    for tf, target in combinations:
        print(f"\n🚀 Модель: {tf}-{target}")
        with tqdm(total=1, desc=f"{tf}-{target}", bar_format="{l_bar}{bar} [ ETA: {remaining} ]") as pbar:
            try:
                deposit = START_DEPOSIT
                wins, losses = 0, 0

                # Завантаження даних
                suffix = target.split('_')[1]
                file = f"BTCUSDT_{tf}_critical_indicators_with_targets_{suffix}.csv"
                path = os.path.join(DATA_DIR, file)
                df = pd.read_csv(path)
                timestamp_col = next((c for c in df.columns if 'time' in c.lower()), None)
                df[timestamp_col] = pd.to_datetime(df[timestamp_col], unit='ms' if df[timestamp_col].max() > 1e12 else 's')
                df.rename(columns={timestamp_col: 'timestamp'}, inplace=True)
                df = df[df['timestamp'] >= min_date].reset_index(drop=True)

                # Обробка фіч
                with open(os.path.join(MODEL_DIR, f"features_{tf}_{target}.txt")) as f:
                    features = f.read().splitlines()
                scaler = StandardScaler()
                X = scaler.fit_transform(df[features])
                X_seq = np.array([X[i-SEQ_LEN:i] for i in range(SEQ_LEN, len(X))])
                df = df.iloc[SEQ_LEN:].reset_index(drop=True)

                # Прогноз
                model = load_model(tf, target, X.shape[1])
                preds = predict_entry(model, X_seq)

                # Трейдинг
                open_trade = False
                entry_price = None
                direction = 'long' if target == 'target_long' else 'short'

                for i in range(len(preds)):
                    row = df.iloc[i]
                    timestamp = row['timestamp']
                    close = row['close']

                    if open_trade:
                        df_1m = df_1m_full[df_1m_full['timestamp'] > entry_time].head(500)
                        filled = 0
                        sl = entry_price * 0.98 if direction == 'long' else entry_price * 1.02
                        tp1 = entry_price * 1.005 if direction == 'long' else entry_price * 0.995
                        tp2 = entry_price * 1.01 if direction == 'long' else entry_price * 0.99
                        tp3 = entry_price * 1.015 if direction == 'long' else entry_price * 0.985
                        tp4 = entry_price * 1.02 if direction == 'long' else entry_price * 0.98
                        sl1 = entry_price
                        sl2 = entry_price * 1.002 if direction == 'long' else entry_price * 0.998
                        sl3 = entry_price * 1.004 if direction == 'long' else entry_price * 0.996

                        for _, m in df_1m.iterrows():
                            high, low = m['high'], m['low']
                            if filled < 1 and ((direction == 'long' and high >= tp1) or (direction == 'short' and low <= tp1)):
                                filled = 1; continue
                            if filled == 1 and ((direction == 'long' and high >= tp2) or (direction == 'short' and low <= tp2)):
                                filled = 2; continue
                            if filled == 2 and ((direction == 'long' and high >= tp3) or (direction == 'short' and low <= tp3)):
                                filled = 3; continue
                            if filled == 3 and ((direction == 'long' and high >= tp4) or (direction == 'short' and low <= tp4)):
                                filled = 4; break
                            stop = sl if filled == 0 else sl1 if filled == 1 else sl2 if filled == 2 else sl3
                            if (direction == 'long' and low <= stop) or (direction == 'short' and high >= stop):
                                break

                        result = 'WIN' if filled > 0 else 'LOSS'
                        reward = 0.0025 * filled if result == 'WIN' else -0.02
                        pnl = START_DEPOSIT * TRADE_PCT * reward * LEVERAGE
                        deposit += pnl
                        wins += int(result == 'WIN')
                        losses += int(result == 'LOSS')
                        stats.append({
                            'timeframe': tf,
                            'target': target,
                            'entry_time': entry_time,
                            'entry_price': round(entry_price, 2),
                            'result': result
                        })
                        open_trade = False

                    if not open_trade and preds[i] == 1:
                        entry_price = close
                        entry_time = timestamp
                        open_trade = True

                model_stats.append({
                    'timeframe': tf,
                    'target': target,
                    'trades': wins + losses,
                    'wins': wins,
                    'losses': losses,
                    'winrate': round(100 * wins / (wins + losses), 2) if (wins + losses) > 0 else 0.0,
                    'final_deposit': round(deposit, 2)
                })

            except Exception as e:
                print(f"❌ Помилка {tf}-{target}: {e}")
            finally:
                pbar.update(1)

    # 📊 Вивід
    df_stats = pd.DataFrame(stats)
    print("\n📊 Усі трейди:")
    print(df_stats.to_string(index=False))

    print("\n📈 Статистика по кожній моделі:")
    df_models = pd.DataFrame(model_stats)
    print(df_models.to_string(index=False))

if __name__ == '__main__':
    run_backtest()
