import os
import torch
import torch.nn as nn
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix

from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm
from copy import deepcopy



from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/LSTM/saved_models'
RESULT_CSV = 'Models/LSTM/lstm_training_results.csv'
os.makedirs(MODEL_DIR, exist_ok=True)

# ----- MODEL -----
class CNNLSTMModel(nn.Module):
    def __init__(self, input_size):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels=1, out_channels=16, kernel_size=3, padding=1)
        self.relu = nn.ReLU()
        self.lstm = nn.LSTM(input_size=16, hidden_size=32, batch_first=True)
        self.fc = nn.Linear(32, 1)

    def forward(self, x):
        x = x.unsqueeze(1)
        x = self.relu(self.conv1(x))
        x = x.permute(0, 2, 1)
        _, (hn, _) = self.lstm(x)
        out = self.fc(hn[-1])
        return out.squeeze()

# ----- TRAIN FUNCTION -----
def train_model(model, train_loader, criterion, optimizer):
    model.train()
    for x_batch, y_batch in train_loader:
        x_batch = x_batch.float().to(DEVICE)
        y_batch = y_batch.float().to(DEVICE)
        optimizer.zero_grad()
        output = model(x_batch)
        loss = criterion(output, y_batch)
        loss.backward()
        optimizer.step()

# ----- EVALUATE FUNCTION -----
def evaluate_model(model, X_test, y_test):
    model.eval()
    with torch.no_grad():
        X_tensor = torch.tensor(X_test, dtype=torch.float32).to(DEVICE)
        preds = torch.sigmoid(model(X_tensor))
        y_pred = (preds.cpu().numpy() > 0.5).astype(int)
        acc = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)

        # 🔍 Додаткове логування
        cm = confusion_matrix(y_test, y_pred)
        if cm.shape == (2, 2):
            tn, fp, fn, tp = cm.ravel()
            print(f"    Confusion matrix: TN={tn}, FP={fp}, FN={fn}, TP={tp}")
        else:
            print(f"    Confusion matrix: {cm.tolist()}")

        return acc, prec, rec, f1

# ----- MAIN LOOP -----
results = []

for timeframe in tqdm(TIMEFRAMES, desc="Timeframes"):
    for target in TARGETS:
        file_path = os.path.join(DATA_DIR, f'BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv')
        if not os.path.exists(file_path):
            print(f"❌ Файл не знайдено: {file_path}")
            continue

        df = pd.read_csv(file_path).dropna()

        for variant_name, features in tqdm(VARIANT_FEATURE_SETS.items(), desc=f"{timeframe}-{target}", leave=False):
            missing = [f for f in features if f not in df.columns]
            target_col = f"target_{target}"
            if target_col not in df.columns:
                missing.append(target_col)
            if missing:
                print(f"⛔ Пропущено {timeframe}-{target}-{variant_name} через відсутні фічі: {missing}")
                continue

            X = df[features].values
            y = df[target_col].values

            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
            X_train_tensor = torch.tensor(X_train, dtype=torch.float32).to(DEVICE)
            y_train_tensor = torch.tensor(y_train, dtype=torch.float32).to(DEVICE)

            # === POS WEIGHT ===
            pos_count = np.sum(y_train == 1)
            neg_count = np.sum(y_train == 0)
            if pos_count == 0:
                print(f"⛔ Пропущено {timeframe}-{target}-{variant_name} бо немає позитивних прикладів.")
                continue
            pos_weight_value = neg_count / (pos_count + 1e-5)
            pos_weight = torch.tensor([pos_weight_value]).to(DEVICE)
            criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

            train_dataset = TensorDataset(X_train_tensor, y_train_tensor)
            train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)

            model = CNNLSTMModel(input_size=len(features)).to(DEVICE)
            optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

            best_f1 = 0
            best_state = None
            patience_counter = 0
            f1_scores = []

            for epoch in range(100):
                train_model(model, train_loader, criterion, optimizer)
                acc, prec, rec, f1 = evaluate_model(model, X_test, y_test)
                f1_scores.append(f1)

                if f1 > best_f1:
                    best_f1 = f1
                    best_state = deepcopy(model.state_dict())
                    patience_counter = 0
                else:
                    patience_counter += 1

                print(f"🧠 Epoch {epoch+1:03d} | F1: {f1:.4f} | Best: {best_f1:.4f} | Patience: {patience_counter}/20")

                if patience_counter >= 20:
                    print(f"⏹️ Рання зупинка на епосі {epoch+1}")
                    break

            if best_state:
                model.load_state_dict(best_state)

            acc, prec, rec, f1 = evaluate_model(model, X_test, y_test)

            model_name = f'LSTM_{timeframe}_{target}_{variant_name}.pt'
            model_path = os.path.join(MODEL_DIR, model_name)
            torch.save(model.state_dict(), model_path)

            results.append({
                'timeframe': timeframe,
                'target': target,
                'variant': variant_name,
                'accuracy': acc,
                'precision': prec,
                'recall': rec,
                'f1_score': f1,
                'model_path': model_path
            })

# Save results
pd.DataFrame(results).to_csv(RESULT_CSV, index=False)
print(f"\n✅ Результати збережено в {RESULT_CSV}")
