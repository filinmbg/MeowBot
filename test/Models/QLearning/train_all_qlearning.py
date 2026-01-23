# test/Models/QLearning/train_all_qlearning.py
import sys as _sys
from . import configs as _cnn_configs
_sys.modules.setdefault("configs", _cnn_configs)
import os, json, argparse, warnings
from typing import List, Tuple, Dict

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score

# ---- robust import of configs (works with -m) -------------------------------
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
MODEL_DIR = "models/QLearning"
LOG_DIR   = "logs/QLearning"
DATA_DIR  = os.path.join("test", "data", SYMBOL)
RESULTS_CSV = os.path.join(MODEL_DIR, "qlearning_training_results.csv")
FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

SEED = 42
rng = np.random.default_rng(SEED)

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
      models/<SYMBOL>/QLearning
      logs/<SYMBOL>/QLearning
    Якщо користувач передав свої --models-dir/--logs-dir — використовуємо як є.
    """
    symbol = symbol.upper()
    data_dir = os.path.join(data_root, symbol)

    if models_dir is None or _norm(models_dir) == "models/qlearning":
        models_dir = os.path.join("models", symbol, "QLearning")
    if logs_dir is None or _norm(logs_dir) == "logs/qlearning":
        logs_dir = os.path.join("logs", symbol, "QLearning")

    return data_dir, models_dir, logs_dir

def setup_single_file_logger(log_dir: str) -> logging.Logger:
    ensure_dirs(log_dir)
    logger = logging.getLogger(f"qlearning_train_{int(datetime.now().timestamp())}")
    logger.setLevel(logging.INFO)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fh = logging.FileHandler(os.path.join(log_dir, f"train_{ts}.log"), encoding="utf-8")
    ch = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    logger.propagate = False
    return logger

def load_dataframe(timeframe: str, target: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{SYMBOL}_{timeframe}_critical_indicators_with_targets_{target}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    for col in ["timestamp","time","open_time","date"]:
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    return df.dropna().reset_index(drop=True)

def time_split_indices(n: int, val_ratio: float=0.2):
    split = int(n*(1-val_ratio))
    idx = np.arange(n)
    return idx[:split], idx[split:]

def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int):
    n = len(X)
    if n < seq_len:
        return np.empty((0,seq_len,X.shape[1]),np.float32), np.empty((0,),np.float32)
    Xs, ys = [], []
    for t in range(seq_len-1, n):
        Xs.append(X[t-seq_len+1:t+1])
        ys.append(y[t])
    return np.asarray(Xs, np.float32), np.asarray(ys, np.float32)

def fit_scale_features(train_X: np.ndarray) -> StandardScaler:
    sc = StandardScaler(); sc.fit(train_X); return sc

def save_feature_spec(base_no_ext: str, model_name: str, timeframe: str, target: str,
                      variant_id: str, features: List[str], seq_len: int, scaler: StandardScaler):
    spec = {
        "model_name": model_name, "symbol": SYMBOL, "timeframe": timeframe, "target": target,
        "variant_id": str(variant_id), "seq_len": int(seq_len),
        "features": list(features), "n_features": int(len(features)),
        "preprocessing": {"scaler":"StandardScaler","with_mean":True,"with_std":True},
        "seed": int(SEED)
    }
    with open(base_no_ext + ".features.json","w",encoding="utf-8") as f:
        json.dump(spec,f,ensure_ascii=False,indent=2)
    mean_ = getattr(scaler,"mean_",None)
    scale_= getattr(scaler,"scale_",None)
    var_  = getattr(scaler,"var_",None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(base_no_ext + ".scaler.npz",
                            mean_=np.asarray(mean_,np.float32),
                            scale_=np.asarray(scale_,np.float32),
                            var_=np.asarray(var_ if var_ is not None else np.square(scale_),np.float32))

    row = {
        "model_name": model_name, "symbol": SYMBOL, "timeframe": timeframe, "target": target,
        "variant_id": str(variant_id), "seq_len": int(seq_len),
        "n_features": int(len(features)), "features_csv": "|".join(features)
    }
    if os.path.exists(FEATURES_MANIFEST):
        dfm = pd.read_csv(FEATURES_MANIFEST)
        dfm = pd.concat([dfm, pd.DataFrame([row])], ignore_index=True)
    else:
        dfm = pd.DataFrame([row])
    dfm.to_csv(FEATURES_MANIFEST, index=False)

# -------- Discretizer --------
class FeatureDiscretizer:
    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self.edges: List[np.ndarray] = []

    def fit(self, X_last: np.ndarray):
        # X_last: (N, F) — беремо останній крок вікна
        self.edges = []
        for i in range(X_last.shape[1]):
            col = X_last[:, i]
            lo, hi = np.nanmin(col), np.nanmax(col)
            if not np.isfinite(lo) or not np.isfinite(hi) or lo == hi:
                hi = lo + 1e-6
            self.edges.append(np.linspace(lo, hi, self.n_bins + 1))

    def transform_one(self, x: np.ndarray) -> Tuple[int,]:
        bins = []
        for i, val in enumerate(x):
            e = self.edges[i]
            b = np.digitize(val, e[1:-1], right=False)
            bins.append(int(b))
        return tuple(bins)

# -------- Q-Table --------
class QTable:
    def __init__(self, n_actions: int=2, alpha: float=0.1, gamma: float=0.0, eps_start: float=0.2, eps_end: float=0.01, eps_decay: float=0.995):
        self.Q: Dict[Tuple[int,], np.ndarray] = {}
        self.n_actions = n_actions
        self.alpha = alpha
        self.gamma = gamma  # 0.0, бо епізод 1-кроковий
        self.eps = eps_start
        self.eps_end = eps_end
        self.eps_decay = eps_decay

    def _ensure(self, s: Tuple[int,]):
        if s not in self.Q:
            self.Q[s] = np.zeros(self.n_actions, dtype=np.float32)

    def act(self, s: Tuple[int,]) -> int:
        self._ensure(s)
        if rng.random() < self.eps:
            return rng.integers(0, self.n_actions)
        return int(np.argmax(self.Q[s]))

    def update(self, s: Tuple[int,], a: int, r: float):
        self._ensure(s)
        td_target = r  # + gamma * max(Q') але епізод 1-кроковий
        self.Q[s][a] += self.alpha * (td_target - self.Q[s][a])

    def decay_eps(self):
        self.eps = max(self.eps_end, self.eps * self.eps_decay)

def train_one(timeframe: str, target: str, variant_id: str, features: List[str],
              seq_len: int=32, epochs: int=100, patience: int=12, n_bins: int=10,
              alpha: float=0.1, gamma: float=0.0) -> dict:
    model_name = f"QL_{timeframe}_{target}_V{variant_id}"
    base_no_ext = os.path.join(MODEL_DIR, model_name)
    log_path = os.path.join(LOG_DIR, f"{model_name}_log.csv")
    qtable_path = base_no_ext + ".qtable.npz"

    try:
        df = load_dataframe(timeframe, target)

        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"model": model_name, "status": f"❌ missing features: {missing}", "val_acc": None, "val_f1": None}

        tgt_col = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if tgt_col not in df.columns:
            return {"model": model_name, "status": f"❌ missing target column: {tgt_col}", "val_acc": None, "val_f1": None}

        X_all = df[features].values.astype(np.float32)
        y_all = df[tgt_col].values.astype(np.float32)

        idx_tr, idx_va = time_split_indices(len(X_all), val_ratio=0.2)
        X_tr_raw, y_tr_raw = X_all[idx_tr], y_all[idx_tr]
        X_va_raw, y_va_raw = X_all[idx_va], y_all[idx_va]

        scaler = fit_scale_features(X_tr_raw)
        X_tr = scaler.transform(X_tr_raw).astype(np.float32)
        X_va = scaler.transform(X_va_raw).astype(np.float32)

        save_feature_spec(base_no_ext, model_name, timeframe, target, variant_id, features, seq_len, scaler)

        Xtr_seq, ytr_seq = build_sequences(X_tr, y_tr_raw, seq_len)
        Xva_seq, yva_seq = build_sequences(X_va, y_va_raw, seq_len)
        if len(Xtr_seq)==0 or len(Xva_seq)==0:
            return {"model": model_name, "status": "❌ not enough data for sequences", "val_acc": None, "val_f1": None}

        disc = FeatureDiscretizer(n_bins=n_bins)
        disc.fit(Xtr_seq[:,-1,:])  # останній крок вікна
        Q = QTable(alpha=alpha, gamma=gamma)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("epoch,train_loss,val_loss,val_acc,val_f1,best_f1\n")

        best_f1 = -1.0
        best_loss = float("inf")
        no_improve = 0

        for epoch in range(1, epochs+1):
            # ---------------- train ----------------
            train_rewards = []
            for i in range(len(Xtr_seq)):
                s_cont = Xtr_seq[i, -1, :]
                s = disc.transform_one(s_cont)
                a = Q.act(s)
                r = 1.0 if a == int(ytr_seq[i]) else 0.0
                Q.update(s, a, r)
                train_rewards.append(r)
            Q.decay_eps()

            train_loss = float(1.0 - (np.mean(train_rewards) if train_rewards else 0.0))

            # ---------------- validate ----------------
            preds = []
            for i in range(len(Xva_seq)):
                s = disc.transform_one(Xva_seq[i, -1, :])
                if s in Q.Q:
                    a = int(np.argmax(Q.Q[s]))
                else:
                    a = 1 if np.mean(Xva_seq[i, -1, :]) > 0 else 0
                preds.append(a)

            y_true = yva_seq.astype(np.int64)
            y_pred = np.asarray(preds, dtype=np.int64)
            val_acc = float(accuracy_score(y_true, y_pred))
            val_f1  = float(f1_score(y_true, y_pred, zero_division=0))
            val_loss = 1.0 - val_f1

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{epoch},{train_loss:.6f},{val_loss:.6f},{val_acc:.6f},{val_f1:.6f},{max(best_f1,val_f1):.6f}\n")

            improved = (val_f1 > best_f1 + 1e-6) or (abs(val_f1 - best_f1) <= 1e-6 and val_loss < best_loss - 1e-6)
            if improved:
                best_f1 = val_f1
                best_loss = val_loss
                ensure_dirs(MODEL_DIR)
                # збережемо Q-таблицю + дискретизатор
                np.savez_compressed(qtable_path, **{str(k): v for k, v in Q.Q.items()})
                with open(base_no_ext + ".disc.json", "w", encoding="utf-8") as f:
                    json.dump({"edges": [e.tolist() for e in disc.edges]}, f)
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    print(f"[{model_name}] ⏹️ early stop at epoch {epoch} (best_f1={best_f1:.4f})")
                    break

        return {"model": model_name, "status": "✅ trained", "val_acc": val_acc, "val_f1": best_f1}

    except Exception as e:
        return {"model": model_name, "status": f"❌ error: {type(e).__name__}: {str(e)}", "val_acc": None, "val_f1": None}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT", help="Ticker symbol, e.g. ETHUSDT. Default: BTCUSDT.")
    ap.add_argument("--data-root", default="test/data")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--logs-dir", default=None)

    ap.add_argument("--seq_len", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=12)
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--gamma", type=float, default=0.0)
    args = ap.parse_args()

    data_dir, models_dir, logs_dir = compute_paths(args.symbol, data_root=args.data_root,
                                                   models_dir=args.models_dir, logs_dir=args.logs_dir)

    global SYMBOL, DATA_DIR, MODEL_DIR, LOG_DIR, RESULTS_CSV, FEATURES_MANIFEST
    SYMBOL = args.symbol.upper()
    DATA_DIR = data_dir
    MODEL_DIR = models_dir
    LOG_DIR = logs_dir
    RESULTS_CSV = os.path.join(MODEL_DIR, "qlearning_training_results.csv")
    FEATURES_MANIFEST = os.path.join(MODEL_DIR, "features_manifest.csv")

    ensure_dirs(MODEL_DIR, LOG_DIR)

    # aggregated run log
    logger = setup_single_file_logger(LOG_DIR)
    logger.info(f"start symbol={SYMBOL} data={DATA_DIR} models={MODEL_DIR} logs={LOG_DIR}")

    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)
    with tqdm(total=total, desc=f"🧠 Training QLearning models") as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name = f"QL_{timeframe}_{target}_V{variant_id}"
                    qtable_path = os.path.join(MODEL_DIR, model_name + ".qtable.npz")

                    if os.path.exists(qtable_path):
                        msg = f"{model_name}: ⏭️ already exists — skipped"
                        print(msg); logger.info(msg)
                        pbar.update(1)
                        continue

                    res = train_one(
                        timeframe, target, variant_id, features,
                        seq_len=args.seq_len, epochs=args.epochs,
                        patience=args.patience, n_bins=args.bins,
                        alpha=args.alpha, gamma=args.gamma
                    )
                    msg = f"{res['model']}: {res['status']} | acc={res['val_acc']}, f1={res['val_f1']}"
                    print(msg); logger.info(msg)
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
