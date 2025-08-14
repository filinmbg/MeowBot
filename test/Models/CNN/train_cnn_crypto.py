import sys as _sys
from . import configs as _cnn_configs
_sys.modules.setdefault("configs", _cnn_configs)
import os
import json
import warnings
import numpy as np
import pandas as pd
from tqdm import tqdm
import argparse

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score

from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

warnings.filterwarnings("ignore", category=FutureWarning)

# ===== Reproducibility =====
SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

# ===== Device =====
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class EnhancedCNN(nn.Module):
    def __init__(self, input_size: int):
        super().__init__()
        # input: (B, 1, F)
        self.conv1 = nn.Conv1d(1, 32, kernel_size=3, padding=1)
        self.bn1   = nn.BatchNorm1d(32)
        self.conv2 = nn.Conv1d(32, 64, kernel_size=3, padding=1)
        self.bn2   = nn.BatchNorm1d(64)
        self.pool  = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(p=0.2)
        self.fc1   = nn.Linear(64, 32)
        self.fc2   = nn.Linear(32, 1)
        # logits out (use BCEWithLogitsLoss)

    def forward(self, x):
        x = torch.relu(self.bn1(self.conv1(x)))   # (B,32,F)
        x = torch.relu(self.bn2(self.conv2(x)))   # (B,64,F)
        x = self.pool(x)                          # (B,64,1)
        x = x.view(x.size(0), -1)                 # (B,64)
        x = self.dropout(torch.relu(self.fc1(x))) # (B,32)
        logits = self.fc2(x)                      # (B,1)
        return logits


def is_finite_array(arr: np.ndarray) -> bool:
    return np.isfinite(arr).all()


def ensure_dirs(models_dir: str, logs_dir: str):
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)


def save_feature_spec(save_dir: str,
                      model_name: str,
                      timeframe: str,
                      target: str,
                      variant_id: int,
                      features: list,
                      scaler: StandardScaler,
                      symbol: str,
                      seed: int = SEED):
    """
    Зберігає два файли поруч із моделлю:
      1) <model_name>.features.json — список фіч (порядок!), метадані тренування.
      2) <model_name>.scaler.npz   — параметри стандартизації (mean, scale, var).
    """
    os.makedirs(save_dir, exist_ok=True)
    base = os.path.join(save_dir, model_name)

    spec = {
        "model_name": model_name,
        "symbol": symbol,
        "timeframe": timeframe,
        "target": target,
        "variant_id": int(variant_id),
        "features": list(features),              # порядок критично важливий
        "n_features": int(len(features)),
        "preprocessing": {
            "scaler": "StandardScaler",
            "with_mean": True,
            "with_std": True,
            "nan_policy": "error",
        },
        "architecture_hint": {
            "input_shape": "(B, 1, F)",
            "notes": "Conv1d по осі F; під час інференсу подавайте ті ж фічі, у тій же послідовності."
        },
        "seed": int(seed)
    }
    with open(base + ".features.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    np.savez_compressed(
        base + ".scaler.npz",
        mean_=scaler.mean_.astype(np.float32),
        scale_=scaler.scale_.astype(np.float32),
        var_=getattr(scaler, "var_", np.square(scaler.scale_).astype(np.float32)).astype(np.float32)
    )


def train_model(symbol: str,
                data_dir: str,
                models_dir: str,
                logs_dir: str,
                timeframe: str,
                target: str,
                variant_id: int,
                features,
                batch_size=128,
                lr=1e-3,
                max_epochs=100,
                patience=12):

    model_name = f"CNN_{timeframe}_{target}_V{variant_id}"  # не додаю символ у назву
    save_path  = os.path.join(models_dir, f"{model_name}.pt")
    log_path   = os.path.join(logs_dir, f"{model_name}_log.csv")
    csv_path   = os.path.join(data_dir, f"{symbol}_{timeframe}_critical_indicators_with_targets_{target}.csv")

    # Якщо модель вже є — не перетреновуємо
    if os.path.exists(save_path):
        # якщо відсутні артефакти — відновимо з CSV (скейлер/список фіч)
        if (not os.path.exists(save_path.replace(".pt", ".features.json"))
            or not os.path.exists(save_path.replace(".pt", ".scaler.npz"))):
            if os.path.exists(csv_path):
                try:
                    df = pd.read_csv(csv_path)
                    missing = [f for f in features if f not in df.columns]
                    if not missing:
                        X = df[features].values.astype(np.float32)
                        if is_finite_array(X):
                            scaler = StandardScaler().fit(X)
                            save_feature_spec(models_dir, model_name, timeframe, target, variant_id, features, scaler, symbol)
                except Exception:
                    pass
        return model_name, None, None, "⏭️ already exists"

    if not os.path.exists(csv_path):
        return model_name, None, None, f"❌ file missing: {csv_path}"

    try:
        df = pd.read_csv(csv_path)

        # check features
        missing = [f for f in features if f not in df.columns]
        if missing:
            return model_name, None, None, f"❌ missing features: {missing}"

        target_column = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_column not in df.columns:
            return model_name, None, None, f"❌ missing target column: {target_column}"

        X = df[features].values.astype(np.float32)
        y = df[target_column].values.astype(np.float32)

        if not is_finite_array(X) or not is_finite_array(y):
            return model_name, None, None, "❌ NaN/Inf in raw data"

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X).astype(np.float32)
        if not is_finite_array(X_scaled):
            return model_name, None, None, "❌ NaN/Inf after scaling"

        # зберегти фічі + скейлер біля моделі
        save_feature_spec(models_dir, model_name, timeframe, target, variant_id, features, scaler, symbol)

        # reshape for Conv1d: (N, 1, F)
        X_scaled = X_scaled.reshape((X_scaled.shape[0], 1, X_scaled.shape[1]))

        stratify_opt = y if len(np.unique(y)) == 2 else None
        X_train, X_val, y_train, y_val = train_test_split(
            X_scaled, y, test_size=0.2, random_state=SEED, stratify=stratify_opt
        )

        X_train_tensor = torch.tensor(X_train, dtype=torch.float32)
        y_train_tensor = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
        X_val_tensor   = torch.tensor(X_val,   dtype=torch.float32)
        y_val_tensor   = torch.tensor(y_val,   dtype=torch.float32).unsqueeze(1)

        train_ds = TensorDataset(X_train_tensor, y_train_tensor)
        val_ds   = TensorDataset(X_val_tensor, y_val_tensor)

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=False)
        val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, drop_last=False)

        model = EnhancedCNN(input_size=X_train_tensor.shape[2]).to(DEVICE)
        criterion = nn.BCEWithLogitsLoss()
        optimizer = optim.Adam(model.parameters(), lr=lr)

        best_f1 = -1.0
        best_val_loss = float("inf")
        counter = 0

        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("epoch,train_loss,val_loss,val_acc,val_f1,best_f1\n")

        for epoch in range(1, max_epochs + 1):
            model.train()
            train_losses = []

            print(f"\n[{model_name}] 🌀 Epoch {epoch}/{max_epochs}")
            for xb, yb in train_loader:
                xb = xb.to(DEVICE, non_blocking=True)
                yb = yb.to(DEVICE, non_blocking=True)

                optimizer.zero_grad()
                logits = model(xb)

                if torch.isnan(logits).any() or torch.isinf(logits).any():
                    print(f"[{model_name}] ❌ logits became NaN/Inf — стоп навчання")
                    return model_name, None, None, "❌ logits became NaN/Inf"

                loss = criterion(logits, yb)
                if torch.isnan(loss) or torch.isinf(loss):
                    print(f"[{model_name}] ❌ loss is NaN/Inf — стоп навчання")
                    return model_name, None, None, "❌ loss is NaN/Inf"

                loss.backward()
                optimizer.step()
                train_losses.append(loss.item())

            train_loss = float(np.mean(train_losses)) if train_losses else np.nan
            print(f"[{model_name}]   📉 Train loss: {train_loss:.6f}")

            model.eval()
            val_losses, all_preds, all_true = [], [], []
            with torch.no_grad():
                for xb, yb in val_loader:
                    xb = xb.to(DEVICE, non_blocking=True)
                    yb = yb.to(DEVICE, non_blocking=True)

                    logits = model(xb)
                    loss = criterion(logits, yb)
                    val_losses.append(loss.item())

                    probs = torch.sigmoid(logits)
                    preds = (probs > 0.5).float()
                    all_preds.append(preds.detach().cpu().numpy())
                    all_true.append(yb.detach().cpu().numpy())

            val_loss = float(np.mean(val_losses)) if val_losses else np.nan
            y_true = np.vstack(all_true)
            y_pred = np.vstack(all_preds)
            val_acc = accuracy_score(y_true, y_pred)
            val_f1  = f1_score(y_true, y_pred, zero_division=0)

            print(f"[{model_name}]   📊 Val loss: {val_loss:.6f} | Val acc: {val_acc:.6f} | Val F1: {val_f1:.6f} | Best F1*: {max(best_f1, val_f1):.6f}")

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{epoch},{train_loss:.6f},{val_loss:.6f},{val_acc:.6f},{val_f1:.6f},{max(best_f1,val_f1):.6f}\n")

            improved = (val_f1 > best_f1 + 1e-6) or (abs(val_f1 - best_f1) <= 1e-6 and val_loss < best_val_loss - 1e-6)
            if improved:
                best_f1 = val_f1
                best_val_loss = val_loss
                torch.save(model.state_dict(), save_path)
                counter = 0
                print(f"[{model_name}]   💾 Saved new best (F1={best_f1:.6f}, val_loss={best_val_loss:.6f})")
            else:
                counter += 1
                print(f"[{model_name}]   ⏳ No improve ({counter}/{patience})")
                if counter >= patience:
                    print(f"[{model_name}] ⏹️ Early stop at epoch {epoch} (best_f1={best_f1:.4f})")
                    break

        if os.path.exists(save_path):
            model.load_state_dict(torch.load(save_path, map_location=DEVICE))
        model.eval()
        with torch.no_grad():
            logits = model(X_val_tensor.to(DEVICE))
            probs = torch.sigmoid(logits)
            preds = (probs > 0.5).float().cpu().numpy()

        acc = accuracy_score(y_val_tensor.numpy(), preds)
        f1  = f1_score(y_val_tensor.numpy(), preds, zero_division=0)
        return model_name, acc, f1, "✅ trained"

    except Exception as e:
        return model_name, None, None, f"❌ error: {type(e).__name__}: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description="Train CNN models for crypto classification.")
    parser.add_argument("--symbol", default="ETHUSDT", help="Ticker symbol (default: ETHUSDT).")
    parser.add_argument("--data-root", default="test/data", help="Root folder with symbol subfolder.")
    parser.add_argument("--models-dir", default=None, help="Where to save models. Default: models/CNN_<SYMBOL> (or models/CNN for BTCUSDT).")
    parser.add_argument("--logs-dir", default=None, help="Where to save logs. Default: logs/CNN_<SYMBOL> (or logs/CNN for BTCUSDT).")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    data_dir   = os.path.join(args.data_root, symbol)
    # окремі директорії для ETH, щоб не перетирати BTC
    default_models_dir = "models/CNN" if symbol == "BTCUSDT" else f"models/CNN_{symbol}"
    default_logs_dir   = "logs/CNN"   if symbol == "BTCUSDT" else f"logs/CNN_{symbol}"
    models_dir = args.models_dir or default_models_dir
    logs_dir   = args.logs_dir   or default_logs_dir

    print(f"🧩 Settings: symbol={symbol} | data_dir={data_dir} | models_dir={models_dir} | logs_dir={logs_dir} | device={DEVICE.type}")
    ensure_dirs(models_dir, logs_dir)

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)

    with tqdm(total=total, desc=f"🧠 Training CNN models on {DEVICE.type}") as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name, acc, f1, status = train_model(
                        symbol, data_dir, models_dir, logs_dir, timeframe, target, variant_id, features
                    )
                    results.append({
                        "model": model_name,
                        "accuracy": acc,
                        "f1_score": f1,
                        "status": status
                    })
                    print(f"{model_name}: {status}")
                    pbar.update(1)

    df = pd.DataFrame(results)
    out_csv = os.path.join(models_dir, "cnn_training_results.csv")
    df.to_csv(out_csv, index=False)
    print(f"📊 Results saved to {out_csv}")


if __name__ == "__main__":
    main()
