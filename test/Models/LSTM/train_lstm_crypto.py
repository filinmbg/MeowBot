# test/Models/LSTM/train_lstm_crypto.py
import os
import math
import joblib
import warnings
import argparse
import json
from typing import List, Tuple, Optional, Dict

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torch.nn.utils import clip_grad_norm_

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score

import importlib.util, pathlib

_THIS = pathlib.Path(__file__).resolve()
_CFG  = _THIS.parent / "configs.py"

spec = importlib.util.spec_from_file_location("model_configs", _CFG)
_cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_cfg)

TIMEFRAMES = _cfg.TIMEFRAMES
TARGETS = _cfg.TARGETS
VARIANT_FEATURE_SETS = _cfg.VARIANT_FEATURE_SETS
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
# Repro / device
# =======================
SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# =======================
# FS helpers
# =======================
def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)


def compute_paths(symbol: str,
                  data_root: str = "test/data",
                  models_dir: Optional[str] = None,
                  logs_dir: Optional[str] = None) -> tuple[str, str, str]:
    """
    Повертає (data_dir, models_dir, logs_dir) у форматі:
      - models/<SYMBOL>/LSTM
      - logs/<SYMBOL>/LSTM
    Якщо передано власні --models-dir / --logs-dir — використовуємо їх як є.
    """
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)

    def _norm(p: Optional[str]) -> str:
        return (p or "").replace("\\", "/").rstrip("/").lower()

    if models_dir is None or _norm(models_dir) == "models/lstm":
        models_dir = os.path.join("models", symbol, "LSTM")
    if logs_dir is None or _norm(logs_dir) == "logs/lstm":
        logs_dir = os.path.join("logs", symbol, "LSTM")

    return data_dir, models_dir, logs_dir


def setup_single_file_logger(log_dir: str) -> logging.Logger:
    ensure_dirs(log_dir)
    logger = logging.getLogger(f"lstm_train_{int(datetime.now().timestamp())}")
    logger.setLevel(logging.INFO)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fh = logging.FileHandler(os.path.join(log_dir, f"train_{ts}.log"), encoding="utf-8")
    ch = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    return logger


# =======================
# Data
# =======================
def load_dataframe(timeframe: str, target: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{SYMBOL}_{timeframe}_critical_indicators_with_targets_{target}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    # відсортуємо, якщо є часові колонки
    for col in ["timestamp", "time", "open_time", "date"]:
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    return df.dropna().reset_index(drop=True)


def time_split_indices(n: int, val_ratio: float = 0.2) -> Tuple[np.ndarray, np.ndarray]:
    split = int(n * (1 - val_ratio))
    idx = np.arange(n)
    return idx[:split], idx[split:]


def fit_scale_features(train_X: np.ndarray) -> StandardScaler:
    sc = StandardScaler()
    sc.fit(train_X)
    return sc


def transform_features(scaler: StandardScaler, X: np.ndarray) -> np.ndarray:
    return scaler.transform(X).astype(np.float32)


def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Повертає:
      X_seq: (N, T, F)
      y_seq: (N, 1)
    """
    n = len(X)
    if n < seq_len:
        return np.empty((0, seq_len, X.shape[1]), np.float32), np.empty((0, 1), np.float32)
    Xs, ys = [], []
    for t in range(seq_len - 1, n):
        Xs.append(X[t - seq_len + 1:t + 1])
        ys.append([y[t]])
    return np.asarray(Xs, np.float32), np.asarray(ys, np.float32)


# =======================
# Dataset / Model
# =======================
class SeqDataset(Dataset):
    def __init__(self, X, y):
        self.X, self.y = X, y

    def __len__(self):
        return len(self.X)

    def __getitem__(self, i):
        return self.X[i], self.y[i]


class LSTMClassifier(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_size, 1)
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        last = self.dropout(out[:, -1, :])
        return self.fc(last).squeeze(-1)  # logits [B]


# =======================
# Save feature spec / scaler manifest
# =======================
def save_feature_spec(base_no_ext: str, model_name: str, timeframe: str, target: str,
                      variant_id: str, features: List[str], seq_len: int, scaler: StandardScaler):
    spec = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": int(seq_len),
        "features": list(features),
        "n_features": int(len(features)),
        "preprocessing": {"scaler": "StandardScaler", "with_mean": True, "with_std": True},
        "seed": int(SEED),
    }
    # 👇 вирівнюємо з CNN: .spec.json
    with open(base_no_ext + ".spec.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    mean_, scale_, var_ = getattr(scaler, "mean_", None), getattr(scaler, "scale_", None), getattr(scaler, "var_", None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(
            base_no_ext + ".scaler.npz",
            mean_=mean_.astype(np.float32),
            scale_=scale_.astype(np.float32),
            var_=(var_.astype(np.float32) if var_ is not None else (scale_**2).astype(np.float32)),
        )

    # Маніфест фіч (для зручної інспекції)
    row = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": int(seq_len),
        "n_features": int(len(features)),
        "features_csv": "|".join(features),
    }
    if os.path.exists(FEATURES_MANIFEST):
        dfm = pd.read_csv(FEATURES_MANIFEST)
        dfm = pd.concat([dfm, pd.DataFrame([row])], ignore_index=True)
    else:
        dfm = pd.DataFrame([row])
    dfm.to_csv(FEATURES_MANIFEST, index=False)


# =======================
# Metrics helpers
# =======================
def class_balance_info(y: np.ndarray) -> Dict[int, float]:
    vals, cnt = np.unique(y.astype(int), return_counts=True)
    total = cnt.sum()
    return {int(v): float(c / total) for v, c in zip(vals, cnt)}

def make_weighted_sampler(y_train: np.ndarray) -> WeightedRandomSampler:
    vals, cnt = np.unique(y_train.astype(int), return_counts=True)
    freq = {int(v): c for v, c in zip(vals, cnt)}
    w = np.array([1.0 / (freq.get(int(t), 1) + 1e-8) for t in y_train.astype(int)], dtype=np.float32)
    return WeightedRandomSampler(w, num_samples=len(w), replacement=True)

def best_threshold(y_true: np.ndarray, y_prob: np.ndarray, grid=None):
    if grid is None:
        grid = np.linspace(0.2, 0.8, 25)
    best_f1, best_t = -1.0, 0.5
    for t in grid:
        f1 = f1_score(y_true, (y_prob >= t).astype(int), average="macro", zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t, best_f1


# =======================
# Train one
# =======================
def train_one(timeframe: str, target: str, variant_id: str, features: List[str],
              seq_len: int = 32, batch_size: int = 256, lr: float = 3e-4,
              max_epochs: int = 100, patience: int = 12,
              logger: Optional[logging.Logger] = None) -> dict:
    model_name = f"LSTM_{timeframe}_{target}_V{variant_id}"
    base_no_ext = os.path.join(MODEL_DIR, model_name)
    csv_log = os.path.join(LOG_DIR, f"{model_name}.csv")

    try:
        df = load_dataframe(timeframe, target)
        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"model": model_name, "status": f"❌ missing features: {missing}", "val_acc": None, "val_f1": None}

        tgt = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if tgt not in df.columns:
            return {"model": model_name, "status": f"❌ missing target column: {tgt}", "val_acc": None, "val_f1": None}

        # raw
        X_all = df[features].values.astype(np.float32)
        y_all = df[tgt].values.astype(np.float32)

        # перевірка константного таргета на train/val
        idx_tr, idx_va = time_split_indices(len(X_all), 0.2)
        Xtr_raw, ytr_raw = X_all[idx_tr], y_all[idx_tr]
        Xva_raw, yva_raw = X_all[idx_va], y_all[idx_va]
        if np.unique(ytr_raw).size < 2 or np.unique(yva_raw).size < 2:
            return {"model": model_name, "status": "❌ target is constant on train or val", "val_acc": None, "val_f1": None}

        # scale & save spec/scaler
        scaler = fit_scale_features(Xtr_raw)
        save_feature_spec(base_no_ext, model_name, timeframe, target, variant_id, features, seq_len, scaler)

        Xtr, Xva = transform_features(scaler, Xtr_raw), transform_features(scaler, Xva_raw)
        Xtr_seq, ytr_seq = build_sequences(Xtr, ytr_raw, seq_len)
        Xva_seq, yva_seq = build_sequences(Xva, yva_raw, seq_len)

        if len(Xtr_seq) == 0 or len(Xva_seq) == 0:
            return {"model": model_name, "status": "❌ not enough data for sequences", "val_acc": None, "val_f1": None}

        ds_tr, ds_va = SeqDataset(Xtr_seq, ytr_seq), SeqDataset(Xva_seq, yva_seq)

        # Баланс класів: Weighted sampler + pos_weight
        sampler = make_weighted_sampler(ytr_seq.reshape(-1))
        dl_tr = DataLoader(ds_tr, batch_size=batch_size, sampler=sampler, drop_last=False, num_workers=2, pin_memory=True)
        dl_va = DataLoader(ds_va, batch_size=batch_size, shuffle=False, num_workers=2, pin_memory=True)

        # Модель / лосс / оптимайзер / шедулер
        model = LSTMClassifier(Xtr_seq.shape[2]).to(DEVICE)
        frac = class_balance_info(ytr_seq.reshape(-1))
        p = frac.get(1, 0.5)
        pos_weight = torch.tensor([(1 - p) / (p + 1e-8)], dtype=torch.float32, device=DEVICE)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        opt = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
        sched = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(opt, T_0=5, T_mult=2)

        # CSV лог
        os.makedirs(LOG_DIR, exist_ok=True)
        if not os.path.exists(csv_log):
            with open(csv_log, "w", encoding="utf-8") as f:
                f.write("timestamp,model,epoch,phase,loss,acc,f1,lr,thr\n")

        def log_row(epoch, phase, loss, acc, f1, thr):
            with open(csv_log, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')},{model_name},{epoch},{phase},{loss:.6f},{acc:.6f},{f1:.6f},{opt.param_groups[0]['lr']:.6e},{thr:.4f}\n")

        best_f1, best_state, no_improve, best_thr = -1.0, None, 0, 0.5
        last_val_acc = 0.0

        if logger:
            logger.info(f"[{model_name}] start seq_len={seq_len} batch={batch_size} lr={lr} device={DEVICE.type} class_frac={frac}")

        for ep in range(1, max_epochs + 1):
            # TRAIN
            model.train()
            tr_losses, tr_pred, tr_true = [], [], []
            for xb, yb in dl_tr:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE).view(-1)
                opt.zero_grad(set_to_none=True)
                logits = model(xb)  # [B]
                loss = criterion(logits, yb)
                loss.backward()
                clip_grad_norm_(model.parameters(), max_norm=1.0)
                opt.step()
                tr_losses.append(float(loss.item()))
                probs = torch.sigmoid(logits).detach().cpu().numpy()
                tr_pred.append((probs >= 0.5).astype(int))
                tr_true.append(yb.detach().cpu().numpy().astype(int))
            sched.step(ep)

            if tr_true:
                tr_pred = np.concatenate(tr_pred); tr_true = np.concatenate(tr_true)
                tr_acc = accuracy_score(tr_true, tr_pred)
                tr_f1  = f1_score(tr_true, tr_pred, average="macro", zero_division=0)
            else:
                tr_acc = tr_f1 = 0.0
            tr_loss = float(np.mean(tr_losses)) if tr_losses else float("nan")
            log_row(ep, "train", tr_loss, tr_acc, tr_f1, 0.5)
            if logger:
                logger.info(f"[{model_name}] epoch {ep:02d} train loss={tr_loss:.4f} acc={tr_acc:.4f} f1={tr_f1:.4f}")

            # VAL
            model.eval()
            va_losses, va_probs, va_true = [], [], []
            with torch.no_grad():
                for xb, yb in dl_va:
                    xb, yb = xb.to(DEVICE), yb.to(DEVICE).view(-1)
                    logits = model(xb)
                    va_losses.append(float(criterion(logits, yb).item()))
                    va_probs.append(torch.sigmoid(logits).detach().cpu().numpy())
                    va_true.append(yb.detach().cpu().numpy().astype(int))
            va_loss = float(np.mean(va_losses)) if va_losses else float("nan")
            if va_true:
                va_probs = np.concatenate(va_probs); va_true = np.concatenate(va_true)
                thr, va_f1 = best_threshold(va_true, va_probs)
                va_pred = (va_probs >= thr).astype(int)
                va_acc = accuracy_score(va_true, va_pred)
            else:
                thr, va_f1, va_acc = 0.5, 0.0, 0.0

            log_row(ep, "val", va_loss, va_acc, va_f1, thr)
            if logger:
                logger.info(f"[{model_name}] epoch {ep:02d} val   loss={va_loss:.4f} acc={va_acc:.4f} f1={va_f1:.4f} thr={thr:.3f}")

            # Early stopping по macro-F1
            if va_f1 > best_f1 + 1e-4:
                best_f1, best_thr, best_state, no_improve = va_f1, thr, model.state_dict(), 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    if logger:
                        logger.info(f"[{model_name}] early stop at {ep} (best_f1={best_f1:.4f} thr={best_thr:.3f})")
                    break

            last_val_acc = va_acc

        if best_state is None:
            best_state = model.state_dict()

        # Save best
        torch.save({"state_dict": best_state, "best_val_f1": best_f1, "best_thr": best_thr}, base_no_ext + ".pt")
        if logger:
            logger.info(f"[{model_name}] saved {base_no_ext+'.pt'}")
        return {"model": model_name, "status": "✅ trained", "val_acc": last_val_acc, "val_f1": best_f1}

    except Exception as e:
        if logger:
            logger.exception(f"[{model_name}] crash: {e}")
        return {"model": model_name, "status": f"❌ error: {type(e).__name__}: {e}", "val_acc": None, "val_f1": None}


# =======================
# Main
# =======================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--data-root", default="test/data")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--logs-dir", default=None)
    ap.add_argument("--device", default=None, choices=["cpu", "cuda"])
    ap.add_argument("--seq_len", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=12)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--batch_size", type=int, default=256)
    args = ap.parse_args()

    # dirs
    data_dir, models_dir, logs_dir = compute_paths(
        args.symbol, data_root=args.data_root, models_dir=args.models_dir, logs_dir=args.logs_dir
    )

    # globals
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
    logger = setup_single_file_logger(LOG_DIR)

    print(f"🧩 Settings: symbol={SYMBOL} | data_dir={DATA_DIR} | models_dir={MODEL_DIR} | logs_dir={LOG_DIR} | device={DEVICE.type}")
    logger.info(f"start symbol={SYMBOL} data={DATA_DIR} models={MODEL_DIR} logs={LOG_DIR} device={DEVICE.type}")

    results = []
    # Коректний total для прогресбару
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)

    with tqdm(total=total, desc=f"🧠 Training LSTM models on {DEVICE.type}") as pbar:
        seen = set()  # підстрахуємося від випадкових дублів у конфігах
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    key = (timeframe, target, str(variant_id))
                    if key in seen:
                        msg = f"LSTM_{timeframe}_{target}_V{variant_id}: ⏭️ duplicate combo — skipped"
                        print(msg); logger.info(msg)
                        pbar.update(1)
                        continue
                    seen.add(key)

                    model_name = f"LSTM_{timeframe}_{target}_V{variant_id}"
                    base_no_ext = os.path.join(MODEL_DIR, model_name)
                    pt = base_no_ext + ".pt"
                    sc = base_no_ext + ".scaler.npz"
                    sp = base_no_ext + ".spec.json"

                    # Пропуск уже навчених (усі артефакти мають існувати)
                    if os.path.exists(pt) and os.path.exists(sc) and os.path.exists(sp):
                        msg = f"{model_name}: ⏭️ already exists — skipped"
                        print(msg)
                        logger.info(msg)
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
                        logger=logger,
                    )
                    msg = f"{res['model']}: {res['status']} | acc={res['val_acc']}, f1={res['val_f1']}"
                    print(msg)
                    logger.info(msg)
                    results.append(res)
                    pbar.update(1)

    if results:
        pd.DataFrame(results).to_csv(RESULTS_CSV, index=False)
        print(f"📊 Results saved to {RESULTS_CSV}")
        logger.info(f"results {RESULTS_CSV}")
    else:
        print("✅ All models already trained — nothing to do.")
        logger.info("all models already trained — nothing to do.")

    print(f"🧾 Features manifest: {FEATURES_MANIFEST}")
    logger.info(f"manifest {FEATURES_MANIFEST}")


if __name__ == "__main__":
    main()
