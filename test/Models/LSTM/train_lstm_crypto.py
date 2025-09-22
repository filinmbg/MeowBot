import os
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from imblearn.over_sampling import RandomOverSampler
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# 🔧 Конфігурація
TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m']
TARGETS = ['target_long', 'target_short']
SEQ_LEN = 20
BATCH_SIZE = 32
EPOCHS = 50
HIDDEN_SIZE = 64
N_LAYERS = 2
LEARNING_RATE = 0.001
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/LSTM'
os.makedirs(MODEL_DIR, exist_ok=True)


# 🧠 Модель LSTM
class LSTMClassifier(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim=2, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        _, (hn, _) = self.lstm(x)
        out = self.fc(hn[-1])
        return out


# 🎞️ Створення послідовностей
def create_sequences(X, y, seq_len):
    X_seq, y_seq = [], []
    for i in range(len(X) - seq_len):
        X_seq.append(X[i:i + seq_len])
        y_seq.append(y[i + seq_len])
    return np.array(X_seq), np.array(y_seq)


# 🚀 Навчання для однієї моделі
def train_model_for(tf, target):
    print(f"\n📈 Навчання {tf} - {target}")

    suffix = target.split('_')[1]
    filename = f"BTCUSDT_{tf}_critical_indicators_with_targets_{suffix}.csv"
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        print(f"⚠️ Пропущено — файл не знайдено: {filename}")
        return

    df = pd.read_csv(path).dropna()
    drop_cols = ["open_time", "close_time", "open", "close", "target_long", "target_short"]
    drop_cols.remove(target)
    drop_cols += [target]

    X = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")
    y = df[target]

    # 💾 Збереження фіч
    feature_path = os.path.join(MODEL_DIR, f"features_{tf}_{target}.txt")
    with open(feature_path, "w") as f:
        f.write("\n".join(X.columns))
    print(f"📊 Кількість фіч: {X.shape[1]}")

    # Масштабування
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_seq, y_seq = create_sequences(X_scaled, y.values, SEQ_LEN)

    # Балансування
    ros = RandomOverSampler()
    X_flat, y_bal = ros.fit_resample(X_seq.reshape(X_seq.shape[0], -1), y_seq)
    X_seq_bal = X_flat.reshape(-1, SEQ_LEN, X.shape[1])

    # Розділення
    X_train, X_val, y_train, y_val = train_test_split(X_seq_bal, y_bal, test_size=0.2, stratify=y_bal, random_state=42)

    train_ds = TensorDataset(torch.tensor(X_train, dtype=torch.float32), torch.tensor(y_train, dtype=torch.long))
    val_ds = TensorDataset(torch.tensor(X_val, dtype=torch.float32), torch.tensor(y_val, dtype=torch.long))
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

    # Модель
    model = LSTMClassifier(input_dim=X.shape[1], hidden_dim=HIDDEN_SIZE, num_layers=N_LAYERS)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    loss_fn = nn.CrossEntropyLoss()

    # Тренування
    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0
        for xb, yb in train_loader:
            optimizer.zero_grad()
            preds = model(xb)
            loss = loss_fn(preds, yb)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"🧪 Epoch {epoch + 1}/{EPOCHS} - Loss: {total_loss / len(train_loader):.4f}")

    # Оцінка
    model.eval()
    correct, total = 0, 0
    with torch.no_grad():
        for xb, yb in val_loader:
            preds = model(xb)
            predicted = preds.argmax(dim=1)
            correct += (predicted == yb).sum().item()
            total += yb.size(0)

    acc = correct / total
    print(f"✅ Validation Accuracy: {acc:.4f}")

    # Збереження моделі
    model_path = os.path.join(MODEL_DIR, f"lstm_{tf}_{target}.pt")
    torch.save(model.state_dict(), model_path)
    print(f"💾 Збережено: {model_path}")


# 🔁 Запуск всіх моделей
for tf in TIMEFRAMES:
    for target in TARGETS:
        try:
            train_model_for(tf, target)
        except Exception as e:
            print(f"❌ Помилка {tf} {target}: {e}")
