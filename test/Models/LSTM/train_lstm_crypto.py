# test/Models/LSTM/train_lstm_crypto.py
import os
import math
import joblib
import warnings
import argparse
import json
from typing import List, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score

# ---- robust import of configs (workable for -m launch) -----------------------
try:
    from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
except Exception:
    try:
        from test.Models.configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
    except Exception:
        from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

warnings.filterwarnings("ignore", category=FutureWarning)

# =======================
# Paths (will be overridden in main by --symbol)
# =======================
SYMBOL = "BTCUSDT"
MODEL_DIR = "models/LSTM"
LOG_DIR = "logs/LSTM"
DATA_DIR = os.path.join("test", "data", SYMBOL)
RESULTS_CSV = os.path.join(MODEL_DIR, "lstm_training_results.csv")
FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

# =======================
# Reproducibility
# =======================
SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

# =======================
# Device
# =======================
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# =======================
# Dir helpers
# =======================
def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)


def compute_paths(symbol: str,
                  data_root: str = "test/data",
                  models_dir: str | None = None,
                  logs_dir: str | None = None) -> tuple[str, str, str]:
    """
    Returns (data_dir, models_dir, logs_dir) based on symbol.
    - For BTCUSDT keep legacy: models/LSTM, logs/LSTM
    - For others: models/LSTM_<SYMBOL>, logs/LSTM_<SYMBOL>
    """
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)

    default_models = "models/LSTM" if symbol == "BTCUSDT" else f"models/LSTM_{symbol}"
    default_logs   = "logs/LSTM"   if symbol == "BTCUSDT" else f"logs/LSTM_{symbol}"

    models_dir = models_dir or default_models
    logs_dir   = logs_dir or default_logs
    return data_dir, models_dir, logs_dir


# =======================
# Data utils
# =======================
def load_dataframe(timeframe: str, target: str) -> pd.DataFrame:
    csv_path = os.path.join(
        DATA_DIR,
        f"{SYMBOL}_{timeframe}_critical_indicators_with_targets_{target}.csv"
    )
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    df = pd.read_csv(csv_path)
    for col in ["timestamp", "time", "open_time", "date"]:
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    df = df.dropna().reset_index(drop=True)
    return df


def time_split_indices(n: int, val_ratio: float = 0.2) -> Tuple[np.ndarray, np.ndarray]:
    split = int(n * (1 - val_ratio))
    idx = np.arange(n)
    return idx[:split], idx[split:]


def fit_scale_features(train_X: np.ndarray) -> StandardScaler:
    scaler = StandardScaler()
    scaler.fit(train_X)
    return scaler


def transform_features(scaler: StandardScaler, X: np.ndarray) -> np.ndarray:
    Xs = scaler.transform(X).astype(np.float32)
    return Xs


def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> Tuple[np.ndarray, np.ndarray]:
    n = len(X)
    if n < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32), np.empty((0, 1), dtype=np.float32)

    X_seq = []
    y_seq = []
    for t in range(seq_len - 1, n):
        X_seq.append(X[t - seq_len + 1:t + 1])
        y_seq.append([y[t]])
    X_seq = np.array(X_seq, dtype=np.float32)
    y_seq = np.array(y_seq, dtype=np.float32)
    return X_seq, y_seq


class SeqDataset(Dataset):
    def __init__(self, X_seq: np.ndarray, y_seq: np.ndarray):
        self.X = X_seq
        self.y = y_seq

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


# =======================
# Model
# =======================
class LSTMClassifier(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
            bidirectional=False,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):  # x: (B, T, F)
        out, _ = self.lstm(x)      # (B, T, H)
        last = out[:, -1, :]       # (B, H)
        last = self.dropout(last)
        logits = self.fc(last)     # (B, 1)
        return logits


# =======================
# Feature saving helpers
# =======================
def save_feature_spec(base_path_no_ext: str,
                      model_name: str,
                      timeframe: str,
                      target: str,
                      variant_id: str,
                      features: List[str],
                      seq_len: int,
                      scaler: StandardScaler):
    """
    Зберігає:
    - JSON із метаданими фіч та препроцесингу
    - NPZ із статистикою скейлера (mean, scale, var)
    - Рядок у загальний manifest CSV
    """
    # JSON
    spec = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": int(seq_len),
        "features": list(features),
        "n_features": int(len(features)),
        "preprocessing": {
            "scaler": "StandardScaler",
            "with_mean": True,
            "with_std": True
        },
        "seed": int(SEED)
    }
    with open(base_path_no_ext + ".features.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    # NPZ
    mean_ = getattr(scaler, "mean_", None)
    scale_ = getattr(scaler, "scale_", None)
    var_ = getattr(scaler, "var_", None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(
            base_path_no_ext + ".scaler.npz",
            mean_=mean_.astype(np.float32),
            scale_=scale_.astype(np.float32),
            var_=(var_.astype(np.float32) if var_ is not None else (scale_ ** 2).astype(np.float32))
        )

    # Manifest append
    row = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": int(seq_len),
        "n_features": int(len(features)),
        "features_csv": "|".join(features)
    }
    if os.path.exists(FEATURES_MANIFEST):
        dfm = pd.read_csv(FEATURES_MANIFEST)
        dfm = pd.concat([dfm, pd.DataFrame([row])], ignore_index=True)
    else:
        dfm = pd.DataFrame([row])
    dfm.to_csv(FEATURES_MANIFEST, index=False)


# =======================
# Train one model
# =======================
def train_one(timeframe: str,
              target: str,
              variant_id: str,
              features: List[str],
              seq_len: int = 32,
              batch_size: int = 256,
              lr: float = 1e-3,
              max_epochs: int = 100,
              patience: int = 12) -> dict:
    model_name = f"LSTM_{timeframe}_{target}_V{variant_id}"
    save_path = os.path.join(MODEL_DIR, f"{model_name}.pt")
    scaler_path = os.path.join(MODEL_DIR, f"{model_name}.scaler.pkl")
    log_path = os.path.join(LOG_DIR, f"{model_name}_log.csv")
    base_no_ext = os.path.join(MODEL_DIR, model_name)

    try:
        df = load_dataframe(timeframe, target)

        # Перевірка фіч і таргета
        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"model": model_name, "status": f"❌ missing features: {missing}", "val_acc": None, "val_f1": None}

        target_col = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_col not in df.columns:
            return {"model": model_name, "status": f"❌ missing target column: {target_col}", "val_acc": None, "val_f1": None}

        X_all = df[features].values.astype(np.float32)
        y_all = df[target_col].values.astype(np.float32)

        # Time split
        idx_train, idx_val = time_split_indices(len(X_all), val_ratio=0.2)
        X_train_raw, y_train_raw = X_all[idx_train], y_all[idx_train]
        X_val_raw,   y_val_raw   = X_all[idx_val],   y_all[idx_val]

        # Scale: fit тільки на train
        scaler = fit_scale_features(X_train_raw)
        # одразу зберігаємо специфікацію фіч/скейлера (і згодом оновимо joblib під час best-ckpt)
        save_feature_spec(
            base_path_no_ext=base_no_ext,
            model_name=model_name,
            timeframe=timeframe,
            target=target,
            variant_id=variant_id,
            features=features,
            seq_len=seq_len,
            scaler=scaler
        )

        X_train = transform_features(scaler, X_train_raw)
        X_val   = transform_features(scaler, X_val_raw)

        # Послідовності
        Xtr_seq, ytr_seq = build_sequences(X_train, y_train_raw, seq_len=seq_len)
        Xva_seq, yva_seq = build_sequences(X_val, y_val_raw, seq_len=seq_len)

        if len(Xtr_seq) == 0 or len(Xva_seq) == 0:
            return {"model": model_name, "status": "❌ not enough data for sequences", "val_acc": None, "val_f1": None}

        # Datasets / Loaders
        train_ds = SeqDataset(Xtr_seq, ytr_seq)
        val_ds   = SeqDataset(Xva_seq, yva_seq)
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=False)
        val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, drop_last=False)

        # Model / loss / opt
        model = LSTMClassifier(input_size=X_train.shape[1], hidden_size=64, num_layers=2, dropout=0.2).to(DEVICE)
        criterion = nn.BCEWithLogitsLoss()
        optimizer = optim.Adam(model.parameters(), lr=lr)

        # Логи у файл
        ensure_dirs(LOG_DIR)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("epoch,train_loss,val_loss,val_acc,val_f1,best_f1\n")

        best_f1 = -1.0
        best_val_loss = float("inf")
        no_improve = 0

        for epoch in range(1, max_epochs + 1):
            # ---- train ----
            model.train()
            train_losses = []
            for xb, yb in train_loader:
                xb = xb.to(DEVICE, non_blocking=True)   # (B, T, F)
                yb = yb.to(DEVICE, non_blocking=True)   # (B, 1)

                optimizer.zero_grad()
                logits = model(xb)
                loss = criterion(logits, yb)
                if torch.isnan(loss) or torch.isinf(loss):
                    return {"model": model_name, "status": "❌ loss NaN/Inf", "val_acc": None, "val_f1": None}

                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                train_losses.append(loss.item())

            train_loss = float(np.mean(train_losses)) if train_losses else np.nan

            # ---- validate ----
            model.eval()
            val_losses = []
            all_true, all_pred = [], []
            with torch.no_grad():
                for xb, yb in val_loader:
                    xb = xb.to(DEVICE, non_blocking=True)
                    yb = yb.to(DEVICE, non_blocking=True)
                    logits = model(xb)
                    loss = criterion(logits, yb)
                    val_losses.append(loss.item())
                    probs = torch.sigmoid(logits)
                    preds = (probs > 0.5).float()
                    all_true.append(yb.detach().cpu().numpy())
                    all_pred.append(preds.detach().cpu().numpy())

            val_loss = float(np.mean(val_losses)) if val_losses else np.nan
            y_true = np.vstack(all_true)
            y_pred = np.vstack(all_pred)
            val_acc = accuracy_score(y_true, y_pred)
            val_f1  = f1_score(y_true, y_pred, zero_division=0)

            # ---- запис у файл
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{epoch},{train_loss:.6f},{val_loss:.6f},{val_acc:.6f},{val_f1:.6f},{max(best_f1,val_f1):.6f}\n")

            # ---- ПРИНТ у консоль
            print(f"[{model_name}] epoch {epoch}: "
                  f"train_loss={train_loss:.6f}, val_loss={val_loss:.6f}, "
                  f"val_acc={val_acc:.6f}, val_f1={val_f1:.6f}")

            # ---- early stopping by best F1 (tie-break: lower val_loss)
            improved = (val_f1 > best_f1 + 1e-6) or (abs(val_f1 - best_f1) <= 1e-6 and val_loss < best_val_loss - 1e-6)
            if improved:
                best_f1 = val_f1
                best_val_loss = val_loss
                ensure_dirs(MODEL_DIR)
                torch.save(model.state_dict(), save_path)
                joblib.dump(scaler, scaler_path)
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    print(f"[{model_name}] ⏹️ early stop at epoch {epoch} (best_f1={best_f1:.4f})")
                    break

        # ---- final eval on val with best ----
        if os.path.exists(save_path):
            model.load_state_dict(torch.load(save_path, map_location=DEVICE))
        model.eval()
        with torch.no_grad():
            logits_all, y_all = [], []
            for xb, yb in val_loader:
                xb = xb.to(DEVICE)
                logits_all.append(model(xb).cpu())
                y_all.append(yb)
            logits_all = torch.cat(logits_all, dim=0)
            y_all = torch.cat(y_all, dim=0)
            probs = torch.sigmoid(logits_all)
            preds = (probs > 0.5).float().numpy()
            final_acc = accuracy_score(y_all.numpy(), preds)
            final_f1  = f1_score(y_all.numpy(), preds, zero_division=0)

        return {"model": model_name, "status": "✅ trained", "val_acc": final_acc, "val_f1": final_f1}

    except Exception as e:
        return {"model": model_name, "status": f"❌ error: {type(e).__name__}: {str(e)}", "val_acc": None, "val_f1": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT", help="Ticker symbol, e.g. ETHUSDT. Default: BTCUSDT.")
    parser.add_argument("--data-root", default="test/data", help="Root folder with per-symbol subfolders.")
    parser.add_argument("--models-dir", default=None, help="Override models dir (default by symbol).")
    parser.add_argument("--logs-dir", default=None, help="Override logs dir (default by symbol).")
    parser.add_argument("--device", default=None, choices=["cpu", "cuda"], help="Force device (optional).")

    parser.add_argument("--seq_len", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--batch_size", type=int, default=256)
    args = parser.parse_args()

    # compute dirs for symbol
    data_dir, models_dir, logs_dir = compute_paths(args.symbol, data_root=args.data_root,
                                                   models_dir=args.models_dir, logs_dir=args.logs_dir)

    # set globals used by helpers/trainers
    global SYMBOL, DATA_DIR, MODEL_DIR, LOG_DIR, RESULTS_CSV, FEATURES_MANIFEST, DEVICE
    SYMBOL = args.symbol.upper()
    DATA_DIR = data_dir
    MODEL_DIR = models_dir
    LOG_DIR = logs_dir
    RESULTS_CSV = os.path.join(MODEL_DIR, "lstm_training_results.csv")
    FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")
    if args.device:
        DEVICE = torch.device(args.device)

    ensure_dirs(MODEL_DIR, LOG_DIR)

    print(f"🧩 Settings: symbol={SYMBOL} | data_dir={DATA_DIR} | models_dir={MODEL_DIR} | logs_dir={LOG_DIR} | device={DEVICE.type}")

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)
    desc = f"🧠 Training LSTM models on {DEVICE.type}"

    with tqdm(total=total, desc=desc) as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name = f"LSTM_{timeframe}_{target}_V{variant_id}"
                    best_ckpt = os.path.join(MODEL_DIR, model_name + ".pt")

                    # Пропуск готових моделей
                    if os.path.exists(best_ckpt):
                        print(f"{model_name}: ⏭️ already exists — skipped")
                        pbar.update(1)
                        continue

                    res = train_one(
                        timeframe=timeframe,
                        target=target,
                        variant_id=str(variant_id),
                        features=features,
                        seq_len=args.seq_len,
                        batch_size=args.batch_size,
                        lr=args.lr,
                        max_epochs=args.epochs,
                        patience=args.patience,
                    )
                    print(f"{res['model']}: {res['status']} | acc={res['val_acc']}, f1={res['val_f1']}")
                    results.append(res)
                    pbar.update(1)

    if results:
        pd.DataFrame(results).to_csv(RESULTS_CSV, index=False)
        print(f"📊 Results saved to {RESULTS_CSV}")
    else:
        print("✅ All models already trained — nothing to do.")
    print(f"🧾 Features manifest: {FEATURES_MANIFEST}")


if __name__ == "__main__":
    main()
