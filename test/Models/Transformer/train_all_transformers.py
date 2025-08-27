# test/Models/Transformer/train_all_transformers.py
import sys as _sys
from . import configs as _cnn_configs
_sys.modules.setdefault("configs", _cnn_configs)
import os
import json
import math
import argparse
import warnings
from typing import List, Tuple

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, precision_recall_curve

# ---- robust import of configs (works with -m) --------------------------------
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
# Globals (overridden by --symbol)
# =======================
SYMBOL = "BTCUSDT"
MODEL_DIR = "models/Transformer"
LOG_DIR = "logs/Transformer"
DATA_DIR = os.path.join("test", "data", SYMBOL)
RESULTS_CSV = os.path.join(MODEL_DIR, "transformer_training_results.csv")
FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)

def _norm(p: str | None) -> str:
    return (p or "").replace("\\", "/").rstrip("/").lower()

def compute_paths(symbol: str,
                  data_root: str = "test/data",
                  models_dir: str | None = None,
                  logs_dir: str | None = None) -> tuple[str, str, str]:
    """
    Нова схема за замовчуванням:
      models/<SYMBOL>/Transformer
      logs/<SYMBOL>/Transformer
    Якщо користувач передав свої --models-dir/--logs-dir — використовуємо як є.
    """
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)

    if models_dir is None or _norm(models_dir) == "models/transformer":
        models_dir = os.path.join("models", symbol, "Transformer")
    if logs_dir is None or _norm(logs_dir) == "logs/transformer":
        logs_dir = os.path.join("logs", symbol, "Transformer")

    return data_dir, models_dir, logs_dir

def setup_single_file_logger(log_dir: str) -> logging.Logger:
    ensure_dirs(log_dir)
    logger = logging.getLogger(f"transformer_train_{int(datetime.now().timestamp())}")
    logger.setLevel(logging.INFO)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fh = logging.FileHandler(os.path.join(log_dir, f"train_{ts}.log"), encoding="utf-8")
    ch = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    logger.propagate = False
    return logger

# =======================
# Data utils
# =======================
def load_dataframe(timeframe: str, target: str) -> pd.DataFrame:
    csv_path = os.path.join(DATA_DIR, f"{SYMBOL}_{timeframe}_critical_indicators_with_targets_{target}.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    df = pd.read_csv(csv_path)
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
    sc = StandardScaler(); sc.fit(train_X); return sc

def transform_features(scaler: StandardScaler, X: np.ndarray) -> np.ndarray:
    return scaler.transform(X).astype(np.float32)

def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> Tuple[np.ndarray, np.ndarray]:
    n = len(X)
    if n < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32), np.empty((0, 1), dtype=np.float32)
    X_seq, y_seq = [], []
    for t in range(seq_len - 1, n):
        X_seq.append(X[t - seq_len + 1:t + 1])
        y_seq.append([y[t]])
    return np.asarray(X_seq, dtype=np.float32), np.asarray(y_seq, dtype=np.float32)

# =======================
# Model
# =======================
class TransformerHead(nn.Module):
    def __init__(self, n_features: int, d_model: int=128, nhead: int=8, num_layers: int=3, dim_ff: int=256, dropout: float=0.1):
        super().__init__()
        self.input = nn.Linear(n_features, d_model)
        enc_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, dim_feedforward=dim_ff, dropout=dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
        self.cls = nn.Linear(d_model, 1)

    def forward(self, x):        # x: (B,T,F)
        z = self.input(x)        # (B,T,d)
        z = self.encoder(z)      # (B,T,d)
        z = z[:, -1, :]          # use last token
        logit = self.cls(z)      # (B,1)
        return logit

def save_feature_spec(base_no_ext: str,
                      model_name: str,
                      timeframe: str,
                      target: str,
                      variant_id: str,
                      features: List[str],
                      seq_len: int,
                      scaler: StandardScaler):
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
        "seed": int(SEED)
    }
    with open(base_no_ext + ".features.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    # scaler dump
    mean_ = getattr(scaler, "mean_", None)
    scale_= getattr(scaler, "scale_", None)
    var_  = getattr(scaler, "var_", None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(base_no_ext + ".scaler.npz",
                            mean_=np.asarray(mean_, dtype=np.float32),
                            scale_=np.asarray(scale_, dtype=np.float32),
                            var_=np.asarray(var_ if var_ is not None else np.square(scale_), dtype=np.float32))

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
# Train one
# =======================
def train_one(timeframe: str,
              target: str,
              variant_id: str,
              features: List[str],
              seq_len: int = 32,
              batch_size: int = 256,
              lr: float = 1e-3,
              max_epochs: int = 100,
              patience: int = 12,
              d_model: int = 128,
              nhead: int = 8,
              num_layers: int = 3,
              dim_feedforward: int = 256,
              dropout: float = 0.1,
              use_sampler: bool = True) -> dict:
    model_name = f"Transformer_{timeframe}_{target}_V{variant_id}"
    save_path = os.path.join(MODEL_DIR, f"{model_name}.pt")
    log_path  = os.path.join(LOG_DIR,  f"{model_name}_log.csv")
    base_no_ext = os.path.join(MODEL_DIR, model_name)

    try:
        df = load_dataframe(timeframe, target)

        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"model": model_name, "status": f"❌ missing features: {missing}", "val_acc": None, "val_f1": None}

        target_col = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_col not in df.columns:
            return {"model": model_name, "status": f"❌ missing target column: {target_col}", "val_acc": None, "val_f1": None}

        X_all = df[features].values.astype(np.float32)
        y_all = df[target_col].values.astype(np.float32)

        idx_tr, idx_va = time_split_indices(len(X_all), val_ratio=0.2)
        X_tr_raw, y_tr_raw = X_all[idx_tr], y_all[idx_tr]
        X_va_raw, y_va_raw = X_all[idx_va], y_all[idx_va]

        scaler = fit_scale_features(X_tr_raw)
        save_feature_spec(base_no_ext, model_name, timeframe, target, variant_id, features, seq_len, scaler)

        X_tr = transform_features(scaler, X_tr_raw)
        X_va = transform_features(scaler, X_va_raw)

        Xtr_seq, ytr_seq = build_sequences(X_tr, y_tr_raw, seq_len)
        Xva_seq, yva_seq = build_sequences(X_va, y_va_raw, seq_len)
        if len(Xtr_seq) == 0 or len(Xva_seq) == 0:
            return {"model": model_name, "status": "❌ not enough data for sequences", "val_acc": None, "val_f1": None}

        # Datasets
        class _DS(Dataset):
            def __init__(self, X, y): self.X, self.y = X, y
            def __len__(self): return len(self.X)
            def __getitem__(self, i): return torch.tensor(self.X[i]), torch.tensor(self.y[i])

        ds_tr = _DS(Xtr_seq, ytr_seq)
        ds_va = _DS(Xva_seq, yva_seq)

        # Sampler to mitigate class imbalance
        if use_sampler:
            targets_flat = ytr_seq.reshape(-1)
            class_sample_count = np.bincount(targets_flat.astype(int), minlength=2).astype(np.float32)
            class_sample_count[class_sample_count == 0] = 1.0
            weights = 1.0 / class_sample_count
            sample_weights = weights[targets_flat.astype(int)]
            sampler = WeightedRandomSampler(sample_weights, num_samples=len(sample_weights), replacement=True)
            train_loader = DataLoader(ds_tr, batch_size=batch_size, sampler=sampler, drop_last=False)
        else:
            train_loader = DataLoader(ds_tr, batch_size=batch_size, shuffle=True, drop_last=False)

        val_loader = DataLoader(ds_va, batch_size=batch_size, shuffle=False, drop_last=False)

        model = TransformerHead(n_features=Xtr_seq.shape[2], d_model=d_model, nhead=nhead,
                                num_layers=num_layers, dim_ff=dim_feedforward, dropout=dropout).to(DEVICE)
        crit = nn.BCEWithLogitsLoss()
        opt  = optim.Adam(model.parameters(), lr=lr)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("epoch,train_loss,val_loss,val_acc,val_f1,best_f1\n")

        best_f1 = -1.0
        best_val = float("inf")
        no_imp = 0

        for epoch in range(1, max_epochs + 1):
            model.train()
            tr_losses = []
            for xb, yb in train_loader:
                xb = xb.to(DEVICE); yb = yb.to(DEVICE)
                opt.zero_grad()
                logits = model(xb)
                loss = crit(logits, yb)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                tr_losses.append(loss.item())
            train_loss = float(np.mean(tr_losses)) if tr_losses else np.nan

            model.eval()
            va_losses, y_true, y_pred = [], [], []
            with torch.no_grad():
                for xb, yb in val_loader:
                    xb = xb.to(DEVICE); yb = yb.to(DEVICE)
                    logits = model(xb)
                    loss = crit(logits, yb)
                    va_losses.append(loss.item())
                    probs = torch.sigmoid(logits)
                    preds = (probs > 0.5).float()
                    y_true.append(yb.cpu().numpy()); y_pred.append(preds.cpu().numpy())
            val_loss = float(np.mean(va_losses)) if va_losses else np.nan
            y_true = np.vstack(y_true); y_pred = np.vstack(y_pred)
            val_acc = accuracy_score(y_true, y_pred)
            val_f1  = f1_score(y_true, y_pred, zero_division=0)

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{epoch},{train_loss:.6f},{val_loss:.6f},{val_acc:.6f},{val_f1:.6f},{max(best_f1,val_f1):.6f}\n")

            improved = (val_f1 > best_f1 + 1e-6) or (abs(val_f1 - best_f1) <= 1e-6 and val_loss < best_val - 1e-6)
            if improved:
                best_f1, best_val = val_f1, val_loss
                ensure_dirs(MODEL_DIR)
                torch.save(model.state_dict(), save_path)
                no_imp = 0
            else:
                no_imp += 1
                if no_imp >= patience:
                    print(f"[{model_name}] ⏹️ early stop at epoch {epoch} (best_f1={best_f1:.4f})")
                    break

        # final eval
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
    parser.add_argument("--batch_size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--lr", type=float, default=1e-3)

    parser.add_argument("--d_model", type=int, default=128)
    parser.add_argument("--nhead", type=int, default=8)
    parser.add_argument("--num_layers", type=int, default=3)
    parser.add_argument("--dim_ff", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.1)

    parser.add_argument("--no_sampler", action="store_true", help="Disable WeightedRandomSampler")

    args = parser.parse_args()

    data_dir, models_dir, logs_dir = compute_paths(args.symbol, data_root=args.data_root,
                                                   models_dir=args.models_dir, logs_dir=args.logs_dir)

    global SYMBOL, DATA_DIR, MODEL_DIR, LOG_DIR, RESULTS_CSV, FEATURES_MANIFEST, DEVICE
    SYMBOL = args.symbol.upper()
    DATA_DIR = data_dir
    MODEL_DIR = models_dir
    LOG_DIR = logs_dir
    RESULTS_CSV = os.path.join(MODEL_DIR, "transformer_training_results.csv")
    FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")
    if args.device:
        DEVICE = torch.device(args.device)

    ensure_dirs(MODEL_DIR, LOG_DIR)
    logger = setup_single_file_logger(LOG_DIR)
    logger.info(f"start symbol={SYMBOL} data={DATA_DIR} models={MODEL_DIR} logs={LOG_DIR} device={DEVICE.type}")

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)
    desc = f"🧠 Training Transformer models on {DEVICE.type}"

    with tqdm(total=total, desc=desc) as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name = f"Transformer_{timeframe}_{target}_V{variant_id}"
                    save_path = os.path.join(MODEL_DIR, f"{model_name}.pt")

                    if os.path.exists(save_path):
                        msg = f"{model_name}: ⏭️ already exists — skipped"
                        print(msg); logger.info(msg)
                        pbar.update(1)
                        continue

                    res = train_one(
                        timeframe=timeframe,
                        target=target,
                        variant_id=variant_id,
                        features=features,
                        seq_len=args.seq_len,
                        batch_size=args.batch_size,
                        lr=args.lr,
                        max_epochs=args.epochs,
                        patience=args.patience,
                        d_model=args.d_model,
                        nhead=args.nhead,
                        num_layers=args.num_layers,
                        dim_feedforward=args.dim_ff,
                        dropout=args.dropout,
                        use_sampler=not args.no_sampler
                    )
                    msg = f"{res['model']}: {res['status']} | acc={res['val_acc']}, f1={res['val_f1']}"
                    print(msg); logger.info(msg)
                    results.append(res)
                    pbar.update(1)

    pd.DataFrame(results).to_csv(RESULTS_CSV, index=False)
    print(f"📊 Results saved to {RESULTS_CSV}")
    logger.info(f"results {RESULTS_CSV}")
    print(f"🧾 Features manifest: {FEATURES_MANIFEST}")
    logger.info(f"manifest {FEATURES_MANIFEST}")

if __name__ == "__main__":
    main()
