# test/Models/XGBoost/train_all_xgboost.py
import sys as _sys
from . import configs as _cnn_configs
_sys.modules.setdefault("configs", _cnn_configs)
import os
import sys
import json
import argparse
import warnings
import importlib.util
from typing import List, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn.metrics import accuracy_score, f1_score

import xgboost as xgb

warnings.filterwarnings("ignore", category=FutureWarning)

# =======================
# Robust import of configs (so you can run this file directly)
# =======================
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "././."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TIMEFRAMES = TARGETS = VARIANT_FEATURE_SETS = None
try:
    from .configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
except Exception:
    try:
        from test.Models.configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
    except Exception:
        cfg_path = os.path.join(ROOT, "test", "Models", "configs.py")
        if not os.path.exists(cfg_path):
            raise
        spec = importlib.util.spec_from_file_location("configs", cfg_path)
        cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfg)
        TIMEFRAMES = cfg.TIMEFRAMES
        TARGETS = cfg.TARGETS
        VARIANT_FEATURE_SETS = cfg.VARIANT_FEATURE_SETS

# =======================
# Globals (overridden by --symbol)
# =======================
SYMBOL = "BTCUSDT"
MODEL_DIR = "models/XGBoost"
LOG_DIR = "logs/XGBoost"
DATA_DIR = os.path.join("test", "data", SYMBOL)
RESULTS_CSV = os.path.join(MODEL_DIR, "xgb_training_results.csv")
FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

SEED = 42
np.random.seed(SEED)

def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)

def compute_paths(symbol: str,
                  data_root: str = "test/data",
                  models_dir: str | None = None,
                  logs_dir: str | None = None) -> tuple[str, str, str]:
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)
    default_models = "models/XGBoost" if symbol == "BTCUSDT" else f"models/XGBoost_{symbol}"
    default_logs   = "logs/XGBoost"   if symbol == "BTCUSDT" else f"logs/XGBoost_{symbol}"
    models_dir = models_dir or default_models
    logs_dir   = logs_dir or default_logs
    return data_dir, models_dir, logs_dir

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

def save_feature_spec(base_no_ext: str,
                      model_name: str,
                      timeframe: str,
                      target: str,
                      variant_id: str,
                      features: List[str]):
    """
    Зберігає локальний <model>.features.json і оновлює централізований FEATURES_MANIFEST.
    """
    spec = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": None,
        "features": list(features),
        "n_features": int(len(features)),
        "preprocessing": {"scaler": None, "with_mean": False, "with_std": False},
        "seed": int(SEED)
    }
    with open(base_no_ext + ".features.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    row = {
        "model_name": model_name,
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "target": target,
        "variant_id": str(variant_id),
        "seq_len": "",
        "n_features": int(len(features)),
        "features_csv": "|".join(features)
    }
    if os.path.exists(FEATURES_MANIFEST):
        dfm = pd.read_csv(FEATURES_MANIFEST)
        dfm = pd.concat([dfm, pd.DataFrame([row])], ignore_index=True)
    else:
        dfm = pd.DataFrame([row])
    dfm.to_csv(FEATURES_MANIFEST, index=False)

def _predict_iter(booster: xgb.Booster, dval: xgb.DMatrix, it: int) -> np.ndarray:
    try:
        return booster.predict(dval, iteration_range=(0, it))
    except TypeError:
        return booster.predict(dval, ntree_limit=it)

# =======================
# Train one
# =======================
def train_one(timeframe: str,
              target: str,
              variant_id: str,
              features: List[str],
              num_boost_round: int = 500,
              patience: int = 40,
              lr: float = 0.05,
              max_depth: int = 6,
              subsample: float = 0.9,
              colsample_bytree: float = 0.9,
              min_child_weight: float = 1.0,
              reg_lambda: float = 1.0,
              gpu: bool = False) -> dict:
    model_name = f"XGB_{timeframe}_{target}_V{variant_id}"
    base_no_ext = os.path.join(MODEL_DIR, model_name)
    model_path = base_no_ext + ".json"
    log_path = os.path.join(LOG_DIR, f"{model_name}_log.csv")

    try:
        df = load_dataframe(timeframe, target)

        # перевірка фіч і таргета
        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"model": model_name, "status": f"❌ missing features: {missing}", "val_acc": None, "val_f1": None}

        target_col = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_col not in df.columns:
            return {"model": model_name, "status": f"❌ missing target column: {target_col}", "val_acc": None, "val_f1": None}

        X_all = df[features].astype(np.float32).values
        y_all = df[target_col].astype(np.float32).values

        # time split
        idx_tr, idx_va = time_split_indices(len(X_all), val_ratio=0.2)
        X_tr, y_tr = X_all[idx_tr], y_all[idx_tr]
        X_va, y_va = X_all[idx_va], y_all[idx_va]

        dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=features)
        dval   = xgb.DMatrix(X_va, label=y_va, feature_names=features)

        save_feature_spec(base_no_ext, model_name, timeframe, target, variant_id, features)

        params = {
            "objective": "binary:logistic",
            "eval_metric": ["logloss"],
            "eta": lr,
            "max_depth": int(max_depth),
            "subsample": float(subsample),
            "colsample_bytree": float(colsample_bytree),
            "min_child_weight": float(min_child_weight),
            "lambda": float(reg_lambda),
            "tree_method": "gpu_hist" if gpu else "hist",
            "seed": SEED,
        }

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("iter,train_logloss,val_logloss,val_acc,val_f1,best_f1\n")

        class LogAndEarlyStop(xgb.callback.TrainingCallback):
            def __init__(self):
                self.best_f1 = -1.0
                self.best_iter = -1
                self.no_improve = 0
                self.best_model_bytes = None

            def after_iteration(self, model, epoch: int, evals_log) -> bool:
                it = epoch + 1
                train_logloss = evals_log.get("train", {}).get("logloss", [np.nan])[-1]
                val_logloss   = evals_log.get("val", {}).get("logloss",   [np.nan])[-1]
                preds = _predict_iter(model, dval, it)
                y_pred = (preds > 0.5).astype(np.int32)
                val_acc = accuracy_score(y_va, y_pred)
                val_f1  = f1_score(y_va, y_pred, zero_division=0)

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"{it},{train_logloss:.6f},{val_logloss:.6f},{val_acc:.6f},{val_f1:.6f},{max(self.best_f1,val_f1):.6f}\n")

                print(f"[{model_name}] iter {it}: train_logloss={train_logloss:.6f}, val_logloss={val_logloss:.6f}, val_acc={val_acc:.6f}, val_f1={val_f1:.6f}")

                if val_f1 > self.best_f1 + 1e-6:
                    self.best_f1 = val_f1
                    self.best_iter = it
                    self.no_improve = 0
                    self.best_model_bytes = model.save_raw()
                else:
                    self.no_improve += 1
                    if self.no_improve >= patience:
                        print(f"[{model_name}] ⏹️ early stop at iter {it} (best_f1={self.best_f1:.4f})")
                        return True  # stop training
                return False  # continue

            def after_training(self, model):
                if self.best_model_bytes is not None:
                    model.load_model(self.best_model_bytes)

        callbacks = [LogAndEarlyStop()]
        watchlist = [(dtrain, "train"), (dval, "val")]
        booster = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist, callbacks=callbacks)

        # Final eval with best booster
        preds = booster.predict(dval)
        y_pred = (preds > 0.5).astype(np.int32)
        final_acc = accuracy_score(y_va, y_pred)
        final_f1  = f1_score(y_va, y_pred, zero_division=0)

        booster.save_model(model_path)
        return {"model": model_name, "status": "✅ trained", "val_acc": final_acc, "val_f1": final_f1}

    except Exception as e:
        return {"model": model_name, "status": f"❌ error: {type(e).__name__}: {str(e)}", "val_acc": None, "val_f1": None}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT", help="Ticker symbol, e.g. ETHUSDT. Default: BTCUSDT.")
    ap.add_argument("--data-root", default="test/data")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--logs-dir", default=None)

    ap.add_argument("--rounds", type=int, default=500)
    ap.add_argument("--patience", type=int, default=40)
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--max_depth", type=int, default=6)
    ap.add_argument("--subsample", type=float, default=0.9)
    ap.add_argument("--colsample_bytree", type=float, default=0.9)
    ap.add_argument("--min_child_weight", type=float, default=1.0)
    ap.add_argument("--reg_lambda", type=float, default=1.0)
    ap.add_argument("--gpu", action="store_true", help="Use GPU tree_method=gpu_hist")
    args = ap.parse_args()

    data_dir, models_dir, logs_dir = compute_paths(args.symbol, data_root=args.data_root,
                                                   models_dir=args.models_dir, logs_dir=args.logs_dir)

    global SYMBOL, DATA_DIR, MODEL_DIR, LOG_DIR, RESULTS_CSV, FEATURES_MANIFEST
    SYMBOL = args.symbol.upper()
    DATA_DIR = data_dir
    MODEL_DIR = models_dir
    LOG_DIR = logs_dir
    RESULTS_CSV = os.path.join(MODEL_DIR, "xgb_training_results.csv")
    FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

    ensure_dirs(MODEL_DIR, LOG_DIR)

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)
    with tqdm(total=total, desc="🧠 Training XGBoost models") as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name = f"XGB_{timeframe}_{target}_V{variant_id}"
                    model_path = os.path.join(MODEL_DIR, model_name + ".json")

                    if os.path.exists(model_path):
                        print(f"{model_name}: ⏭️ already exists — skipped")
                        pbar.update(1)
                        continue

                    res = train_one(
                        timeframe=timeframe,
                        target=target,
                        variant_id=variant_id,
                        features=features,
                        num_boost_round=args.rounds,
                        patience=args.patience,
                        lr=args.lr,
                        max_depth=args.max_depth,
                        subsample=args.subsample,
                        colsample_bytree=args.colsample_bytree,
                        min_child_weight=args.min_child_weight,
                        reg_lambda=args.reg_lambda,
                        gpu=args.gpu,
                    )
                    print(f"{res['model']}: {res['status']} | acc={res['val_acc']}, f1={res['val_f1']}")
                    results.append(res)
                    pbar.update(1)

    pd.DataFrame(results).to_csv(RESULTS_CSV, index=False)
    print(f"📊 Results saved to {RESULTS_CSV}")
    print(f"🧾 Features manifest: {FEATURES_MANIFEST}")

if __name__ == "__main__":
    main()
