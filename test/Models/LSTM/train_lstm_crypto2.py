import os
import torch
import torch.nn as nn
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, confusion_matrix, roc_auc_score
)
from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm
from copy import deepcopy
import logging
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

# Налаштування
logging.basicConfig(
    filename='lstm_training.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/LSTM/saved_models'
os.makedirs(MODEL_DIR, exist_ok=True)


class CNNLSTMModel(nn.Module):
    def __init__(self, input_size, seq_len=1):
        super().__init__()
        self.seq_len = seq_len
        self.conv1 = nn.Conv1d(
            in_channels=input_size,
            out_channels=16,
            kernel_size=3,
            padding=1
        )
        self.bn1 = nn.BatchNorm1d(16)
        self.relu = nn.ReLU()
        self.lstm = nn.LSTM(
            input_size=16,
            hidden_size=32,
            batch_first=True
        )
        self.fc = nn.Linear(32, 1)

    def forward(self, x):
        if x.dim() == 2:
            x = x.unsqueeze(1)
        x = x.permute(0, 2, 1)
        x = self.relu(self.bn1(self.conv1(x)))
        x = x.permute(0, 2, 1)
        _, (hn, _) = self.lstm(x)
        out = self.fc(hn[-1])
        return out.squeeze()


def train_model(model, train_loader, criterion, optimizer):
    model.train()
    total_loss = 0.0
    for x_batch, y_batch in train_loader:
        x_batch = x_batch.float().to(DEVICE)
        y_batch = y_batch.float().to(DEVICE)
        optimizer.zero_grad()
        output = model(x_batch)
        loss = criterion(output, y_batch)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    return total_loss / len(train_loader)


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
        auc = roc_auc_score(y_test, preds.cpu().numpy())

        cm = confusion_matrix(y_test, y_pred)
        cm_str = f"Confusion Matrix: {cm.tolist()} | Acc: {acc:.4f} | Prec: {prec:.4f} | Rec: {rec:.4f} | F1: {f1:.4f} | AUC: {auc:.4f}"
        print("    " + cm_str)
        logging.info(cm_str)

        return acc, prec, rec, f1, auc


# Головний цикл з детальним логуванням
results = []
for timeframe in tqdm(TIMEFRAMES, desc="Timeframes"):
    header = f"=== Processing {timeframe} ==="
    print(f"\n{header}")
    logging.info(header)

    for target in TARGETS:
        file_path = os.path.join(DATA_DIR, f'BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv')
        msg = f"  Target: {target}"
        print(f"\n{msg}")
        logging.info(msg)

        if not os.path.exists(file_path):
            err = f"❌ File not found: {file_path}"
            print(err)
            logging.warning(err)
            continue

        df = pd.read_csv(file_path).dropna()
        loaded = f"  Loaded data: {len(df)} samples"
        print(loaded)
        logging.info(loaded)

        for variant_name, features in VARIANT_FEATURE_SETS.items():
            print(f"\n    Variant: {variant_name}")
            logging.info(f"Variant {variant_name}: {features}")

            model_name = f'LSTM_{timeframe}_{target}_{variant_name}.pt'
            model_path = os.path.join(MODEL_DIR, model_name)
            if os.path.exists(model_path):
                msg = f"⏭️ Skipping already trained model: {model_name}"
                print(msg)
                logging.info(msg)
                continue

            # Перевірка фіч
            target_col = f"target_{target}"
            missing = [f for f in features if f not in df.columns]
            if target_col not in df.columns:
                missing.append(target_col)
            if missing:
                err = f"⛔ Missing features: {missing}"
                print(err)
                logging.warning(err)
                continue

            # Підготовка даних
            X = df[features].values
            y = df[target_col].values
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

            # Нормалізація
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X_train)
            X_test = scaler.transform(X_test)
            msg = f"    Train/Test split: {len(X_train)}/{len(X_test)}"
            print(msg)
            logging.info(msg)

            # Баланс класів
            pos_count = np.sum(y_train == 1)
            neg_count = np.sum(y_train == 0)
            print(f"    Class balance: 0={neg_count} | 1={pos_count} (ratio={neg_count / (pos_count + 1e-5):.2f})")
            logging.info(f"Class balance: 0={neg_count}, 1={pos_count}")
            print(f"    Test class balance: {np.bincount(y_test)}")
            logging.info(f"Test class balance: {np.bincount(y_test).tolist()}")

            if pos_count == 0:
                err = "⛔ No positive samples - skipping"
                print(err)
                logging.warning(err)
                continue

            # Ваги та критерій
            pos_weight = torch.tensor([neg_count / (pos_count + 1e-5)]).to(DEVICE)
            criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

            # Даталоадери
            train_dataset = TensorDataset(
                torch.tensor(X_train, dtype=torch.float32),
                torch.tensor(y_train, dtype=torch.float32)
            )
            train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)

            # Ініціалізація моделі
            model = CNNLSTMModel(input_size=len(features), seq_len=1).to(DEVICE)
            optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'max', patience=5)

            # Навчання
            best_f1 = 0
            best_state = None
            start_msg = "\n    Starting training..."
            print(start_msg)
            logging.info(start_msg)
            header = "    Epoch  | Loss   | Acc    | Prec   | Rec    | F1     | AUC    | LR"
            print(header)
            logging.info(header)
            print("    " + "-" * 65)
            logging.info("    " + "-" * 65)

            for epoch in range(100):
                train_loss = train_model(model, train_loader, criterion, optimizer)
                acc, prec, rec, f1, auc = evaluate_model(model, X_test, y_test)
                scheduler.step(f1)
                lr = optimizer.param_groups[0]['lr']

                log_str = (
                    f"    {epoch + 1:5d}  | {train_loss:.4f} | {acc:.4f} | {prec:.4f} | {rec:.4f} | {f1:.4f} | {auc:.4f} | {lr:.6f}"
                )
                print(log_str)
                logging.info(log_str)

                # Збереження найкращої моделі
                if f1 > best_f1:
                    best_f1 = f1
                    best_state = deepcopy(model.state_dict())

            # Підсумки
            if best_state:
                model.load_state_dict(best_state)
                torch.save({
                    'model_state_dict': best_state,
                    'scaler_mean': scaler.mean_,
                    'scaler_scale': scaler.scale_
                }, model_path)

                summary = "\n    Final evaluation with best model:"
                print(summary)
                logging.info(summary)
                acc, prec, rec, f1, auc = evaluate_model(model, X_test, y_test)
                summary2 = f"    ⭐ F1: {f1:.4f} | AUC: {auc:.4f}"
                print(summary2)
                logging.info(summary2)

                results.append({
                    'timeframe': timeframe,
                    'target': target,
                    'variant': variant_name,
                    'accuracy': acc,
                    'precision': prec,
                    'recall': rec,
                    'f1_score': f1,
                    'auc': auc
                })

# Збереження результатів
pd.DataFrame(results).to_csv('lstm_results.csv', index=False)
print("\n✅ Training complete! Results saved to lstm_results.csv")
logging.info("Training complete. Results saved to lstm_results.csv")
