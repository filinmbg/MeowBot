# test/Models/PPO/ppo_train.py
import sys as _sys
from . import configs as _cnn_configs
_sys.modules.setdefault("configs", _cnn_configs)
import os
import json
import warnings
import argparse
import shutil
from typing import List, Tuple, Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

# --- gymnasium first, fallback to gym ---
try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    import gym
    from gym import spaces

import torch
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor

# ---- robust import of configs (works with -m) --------------------------------
try:
    from .configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
except Exception:
    try:
        from test.Models.PPO.configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
    except Exception:
        from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

warnings.filterwarnings("ignore", category=FutureWarning)

# =======================
# Globals (will be overridden in main by --symbol)
# =======================
SYMBOL = "BTCUSDT"
DATA_DIR = os.path.join("test", "data", SYMBOL)
MODEL_DIR = "models/PPO"
LOG_DIR = "logs/PPO"
TB_DIR = os.path.join(LOG_DIR, "tb")
RESULTS_CSV = os.path.join(MODEL_DIR, "ppo_training_results.csv")
FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

# =======================
# Reproducibility
# =======================
SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)

# =======================
# Path helpers
# =======================
def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)

def compute_paths(symbol: str,
                  data_root: str = "test/data",
                  models_dir: Optional[str] = None,
                  logs_dir: Optional[str] = None,
                  tb_subdir: Optional[str] = None) -> Tuple[str, str, str, str, str, str]:
    """
    Returns (data_dir, model_dir, log_dir, tb_dir, results_csv, features_manifest)
    - For BTCUSDT keep legacy dirs: models/PPO, logs/PPO
    - For others use suffixed dirs: models/PPO_<SYMBOL>, logs/PPO_<SYMBOL>
    """
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)

    default_models = "models/PPO" if symbol == "BTCUSDT" else f"models/PPO_{symbol}"
    default_logs   = "logs/PPO"   if symbol == "BTCUSDT" else f"logs/PPO_{symbol}"

    model_dir = models_dir or default_models
    log_dir   = logs_dir   or default_logs
    tb_dir    = os.path.join(log_dir, tb_subdir or "tb")

    results_csv = os.path.join(model_dir, "ppo_training_results.csv")
    features_manifest = os.path.join(model_dir, "features_manifest.csv")
    return data_dir, model_dir, log_dir, tb_dir, results_csv, features_manifest

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
    return scaler.transform(X).astype(np.float32)

def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> Tuple[np.ndarray, np.ndarray]:
    n = len(X)
    if n < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32), np.empty((0,), dtype=np.float32)
    X_seq, y_seq = [], []
    for t in range(seq_len - 1, n):
        X_seq.append(X[t - seq_len + 1:t + 1])
        y_seq.append(y[t])
    return np.asarray(X_seq, dtype=np.float32), np.asarray(y_seq, dtype=np.float32)

# =======================
# Simple sequence env (1 sample = 1 step/episode)
# =======================
class SeqBinaryEnv(gym.Env):
    """
    Кожен епізод — один вектор (T,F), flatten до (T*F).
    Дія: 0 або 1. Нагорода: 1.0 якщо дія == y, інакше 0.0.
    """
    metadata = {"render_modes": []}  # gymnasium-compatible

    def __init__(self, X_seq: np.ndarray, y_seq: np.ndarray):
        super().__init__()
        assert X_seq.ndim == 3, "X_seq shape must be (N, T, F)"
        assert len(X_seq) == len(y_seq)
        self.X_seq = X_seq
        self.y_seq = y_seq.astype(np.int64)
        self.n, self.T, self.F = X_seq.shape
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.T * self.F,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(2)
        self._idx = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if self._idx >= self.n:
            self._idx = 0
        obs = self.X_seq[self._idx].reshape(-1).astype(np.float32)
        return obs, {}

    def step(self, action):
        y = int(self.y_seq[self._idx])
        a = int(np.asarray(action).reshape(-1)[0])
        reward = 1.0 if a == y else 0.0

        self._idx += 1
        terminated = True
        truncated = False
        if self._idx >= self.n:
            self._idx = 0
        next_obs = self.X_seq[self._idx].reshape(-1).astype(np.float32)
        info = {}
        return next_obs, reward, terminated, truncated, info

# =======================
# Feature spec + scaler save
# =======================
def save_feature_spec(base_path_no_ext: str,
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
    with open(base_path_no_ext + ".features.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    mean_ = getattr(scaler, "mean_", None)
    scale_ = getattr(scaler, "scale_", None)
    var_ = getattr(scaler, "var_", None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(
            base_path_no_ext + ".scaler.npz",
            mean_=np.asarray(mean_, dtype=np.float32),
            scale_=np.asarray(scale_, dtype=np.float32),
            var_=np.asarray(var_ if var_ is not None else np.square(scale_), dtype=np.float32)
        )

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
# Callback: per-epoch logging + early stop (with min_epochs)
# =======================
class PrintAndCSVCallback(BaseCallback):
    def __init__(self, model_name: str, log_path: str,
                 X_val: np.ndarray, y_val: np.ndarray,
                 patience: int = 12, verbose: int = 0,
                 min_epochs: int = 30):
        super().__init__(verbose)
        self.model_name = model_name
        self.log_path = log_path
        self.X_val = X_val.astype(np.float32)  # (N, T*F)
        self.y_val = y_val.astype(np.int64)
        self.epoch = 0
        self.best_f1 = -1.0
        self.best_val_loss = float("inf")  # surrogate: 1 - F1
        self.no_improve = 0
        self.patience = int(patience)
        self.min_epochs = int(min_epochs)

        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("epoch,train_loss,val_loss,val_acc,eval_mean_reward,val_f1,best_f1\n")

    def _on_step(self) -> bool:
        return True

    def _on_rollout_end(self) -> bool:
        train_loss = -1.0  # policy/value/entropy — див. у TensorBoard

        preds = []
        for i in range(len(self.X_val)):
            obs = self.X_val[i].reshape(1, -1)
            action, _ = self.model.predict(obs, deterministic=True)
            action = int(np.asarray(action).reshape(-1)[0])
            preds.append(action)

        y_true = self.y_val
        y_pred = np.asarray(preds, dtype=np.int64)
        val_acc = float(accuracy_score(y_true, y_pred))
        val_f1  = float(f1_score(y_true, y_pred, zero_division=0))
        val_loss = 1.0 - val_f1
        eval_mean_reward = val_acc  # у цьому env це корелює з acc

        self.epoch += 1

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{self.epoch},{train_loss:.6f},{val_loss:.6f},{val_acc:.6f},{eval_mean_reward:.6f},{val_f1:.6f},{max(self.best_f1,val_f1):.6f}\n")

        print(f"[{self.model_name}] epoch {self.epoch}: val_loss={val_loss:.6f}, val_acc={val_acc:.6f}, val_f1={val_f1:.6f}")

        improved = (val_f1 > self.best_f1 + 1e-6) or (abs(val_f1 - self.best_f1) <= 1e-6 and val_loss < self.best_val_loss - 1e-6)
        if improved:
            self.best_f1 = val_f1
            self.best_val_loss = val_loss
            self.model.save(self.best_path())
            self.no_improve = 0
        else:
            self.no_improve += 1
            # Early stop дозволений лише після досягнення min_epochs
            if (self.epoch >= self.min_epochs) and (self.no_improve >= self.patience):
                print(f"[{self.model_name}] ⏹️ early stop at epoch {self.epoch} (best_f1={self.best_f1:.4f})")
                return False

        return True

    def best_path(self) -> str:
        base = os.path.join(MODEL_DIR, f"{self.model_name}")
        return base + ".zip"

# =======================
# Helper: locate or materialize an existing checkpoint to canonical path
# =======================
def find_or_materialize_best(model_name: str) -> Optional[str]:
    """
    Returns a path to an existing checkpoint.
    Prefers canonical models/.../<model>.zip.
    If only eval-callback 'tmp/<model>/best_model.zip' exists, copies it to canonical and returns the canonical path.
    """
    main_zip = os.path.join(MODEL_DIR, model_name + ".zip")
    if os.path.exists(main_zip):
        return main_zip

    eval_best = os.path.join(MODEL_DIR, "tmp", model_name, "best_model.zip")
    if os.path.exists(eval_best):
        try:
            shutil.copy2(eval_best, main_zip)
            if os.path.exists(main_zip):
                return main_zip
        except Exception:
            # if copy fails, at least signal that something exists
            return eval_best
    return None

# =======================
# Train one model (PPO)
# =======================
def train_one(timeframe: str,
              target: str,
              variant_id: str,
              features: List[str],
              seq_len: int = 32,
              total_epochs: int = 100,
              patience: int = 12,
              min_epochs: int = 30,
              lr: float = 1e-4,
              n_steps: int = 4096,
              batch_size: int = 256,
              device: str = "cpu") -> dict:
    """
    total_epochs — кількість rollout-циклів (аналог "епох").
    Кожен цикл = on_rollout_end → логування/валідація/early-stop.
    """
    model_name = f"PPO_{timeframe}_{target}_V{variant_id}"
    base_no_ext = os.path.join(MODEL_DIR, model_name)
    log_path = os.path.join(LOG_DIR, f"{model_name}_log.csv")

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

        idx_train, idx_val = time_split_indices(len(X_all), val_ratio=0.2)
        X_train_raw, y_train_raw = X_all[idx_train], y_all[idx_train]
        X_val_raw,   y_val_raw   = X_all[idx_val],   y_all[idx_val]

        scaler = fit_scale_features(X_train_raw)
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

        Xtr_seq, ytr_seq = build_sequences(X_train, y_train_raw, seq_len=seq_len)
        Xva_seq, yva_seq = build_sequences(X_val,   y_val_raw,   seq_len=seq_len)

        if len(Xtr_seq) == 0 or len(Xva_seq) == 0:
            return {"model": model_name, "status": "❌ not enough data for sequences", "val_acc": None, "val_f1": None}

        train_env = Monitor(SeqBinaryEnv(Xtr_seq, ytr_seq))

        model = PPO(
            policy="MlpPolicy",
            env=train_env,
            verbose=0,
            learning_rate=lr,
            n_steps=n_steps,
            batch_size=min(batch_size, n_steps),
            clip_range=0.15,
            gae_lambda=0.95,
            ent_coef=0.01,
            device=device,
            seed=SEED,
            tensorboard_log=TB_DIR
        )

        cb = PrintAndCSVCallback(
            model_name=model_name,
            log_path=log_path,
            X_val=Xva_seq.reshape(len(Xva_seq), -1),
            y_val=yva_seq,
            patience=patience,
            min_epochs=min_epochs
        )

        eval_env = Monitor(SeqBinaryEnv(Xva_seq, yva_seq))
        eval_cb = EvalCallback(
            eval_env=eval_env,
            best_model_save_path=os.path.join(MODEL_DIR, "tmp", model_name),
            log_path=os.path.join(LOG_DIR, "eval", model_name),
            eval_freq=n_steps,
            deterministic=True,
            render=False
        )

        for _ in range(total_epochs):
            model.learn(
                total_timesteps=n_steps,
                callback=[cb, eval_cb],
                reset_num_timesteps=False,
                progress_bar=False
            )
            if cb.no_improve >= patience and cb.epoch >= min_epochs:
                break

        best_zip = cb.best_path()
        # fallback: if our callback didn't save for some reason, try to pick eval's best
        if not os.path.exists(best_zip):
            existing = find_or_materialize_best(model_name)
            if existing is not None and os.path.exists(existing):
                best_zip = existing

        if os.path.exists(best_zip):
            model = PPO.load(best_zip, device=device)

        preds = []
        for i in range(len(Xva_seq)):
            obs = Xva_seq[i].reshape(1, -1).astype(np.float32)
            action, _ = model.predict(obs, deterministic=True)
            action = int(np.asarray(action).reshape(-1)[0])
            preds.append(action)

        y_true = yva_seq.astype(np.int64)
        y_pred = np.asarray(preds, dtype=np.int64)
        final_acc = float(accuracy_score(y_true, y_pred))
        final_f1  = float(f1_score(y_true, y_pred, zero_division=0))

        return {"model": model_name, "status": "✅ trained", "val_acc": final_acc, "val_f1": final_f1}

    except Exception as e:
        return {"model": model_name, "status": f"❌ error: {type(e).__name__}: {str(e)}", "val_acc": None, "val_f1": None}

# =======================
# Orchestrator
# =======================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT", help="Ticker symbol, e.g. ETHUSDT. Default: BTCUSDT.")
    parser.add_argument("--data-root", default="test/data", help="Root folder with per-symbol subfolders.")
    parser.add_argument("--models-dir", default=None, help="Override models dir (default by symbol).")
    parser.add_argument("--logs-dir", default=None, help="Override logs dir (default by symbol).")
    parser.add_argument("--tb-subdir", default="tb", help="TensorBoard subdir under logs dir (default: tb).")

    parser.add_argument("--seq_len", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=100)     # кількість rollout-епох
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--min_epochs", type=int, default=30)  # мінімум епох до дозволу early-stop
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--n_steps", type=int, default=4096)
    parser.add_argument("--batch_size", type=int, default=256)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()

    # compute dirs for symbol
    data_dir, model_dir, log_dir, tb_dir, results_csv, features_manifest = compute_paths(
        symbol=args.symbol, data_root=args.data_root,
        models_dir=args.models_dir, logs_dir=args.logs_dir, tb_subdir=args.tb_subdir
    )

    # set globals used elsewhere
    global SYMBOL, DATA_DIR, MODEL_DIR, LOG_DIR, TB_DIR, RESULTS_CSV, FEATURES_MANIFEST
    SYMBOL = args.symbol.upper()
    DATA_DIR = data_dir
    MODEL_DIR = model_dir
    LOG_DIR = log_dir
    TB_DIR = tb_dir
    RESULTS_CSV = results_csv
    FEATURES_MANIFEST = features_manifest

    ensure_dirs(MODEL_DIR, LOG_DIR, TB_DIR)

    print(f"🧩 Settings: symbol={SYMBOL} | data_dir={DATA_DIR} | models_dir={MODEL_DIR} | logs_dir={LOG_DIR} | tb_dir={TB_DIR} | device={args.device}")

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)
    desc = f"🧠 Training PPO models on {args.device}"

    with tqdm(total=total, desc=desc) as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name = f"PPO_{timeframe}_{target}_V{variant_id}"

                    # --- robust skip: canonical zip or eval best (materialized) ---
                    existing = find_or_materialize_best(model_name)
                    if existing is not None and os.path.exists(existing):
                        print(f"{model_name}: ⏭️ already exists — skipped ({os.path.relpath(existing)})")
                        pbar.update(1)
                        continue

                    res = train_one(
                        timeframe=timeframe,
                        target=target,
                        variant_id=variant_id,
                        features=features,
                        seq_len=args.seq_len,
                        total_epochs=args.epochs,
                        patience=args.patience,
                        min_epochs=args.min_epochs,
                        lr=args.lr,
                        n_steps=args.n_steps,
                        batch_size=args.batch_size,
                        device=args.device
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
