
import os
import json
import glob
import argparse
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import numpy as np
import pandas as pd

# Optional imports guarded with try/except
try:
    import torch
    import torch.nn as nn
except Exception:
    torch = None
    nn = None

try:
    from sklearn.preprocessing import StandardScaler
except Exception:
    StandardScaler = None

# XGBoost can be either core Booster or sklearn wrapper
try:
    import xgboost as xgb
except Exception:
    xgb = None

try:
    import joblib
except Exception:
    joblib = None

try:
    from stable_baselines3 import PPO as SB3_PPO
except Exception:
    SB3_PPO = None


# --------- Utility: conservative TP/SL ladder sim ----------
@dataclass
class Ladder:
    tp1: float = 0.005  # +0.5%
    tp2: float = 0.010  # +1.0%
    tp3: float = 0.015  # +1.5%
    tp4: float = 0.020  # +2.0%
    sl0: float = 0.005  # -0.5% initial
    be1: float = 0.000  # 0% after TP1
    be2: float = 0.002  # +0.2% after TP2
    be3: float = 0.004  # +0.4% after TP3


def simulate_trade_path(prices: pd.DataFrame, entry_idx: int, side: str, ladder: Ladder) -> Tuple[bool, float, int]:
    if entry_idx >= len(prices) - 1:
        return False, 0.0, entry_idx

    entry = float(prices.loc[entry_idx, "close"])
    tps = [ladder.tp1, ladder.tp2, ladder.tp3, ladder.tp4]
    sls = [ladder.sl0, ladder.be1, ladder.be2, ladder.be3]
    reached = 0
    cur_sl = -sls[0] if side == "long" else sls[0]

    for i in range(entry_idx + 1, len(prices)):
        hi = float(prices.loc[i, "high"])
        lo = float(prices.loc[i, "low"])

        if side == "long":
            sl_level = entry * (1.0 + cur_sl)
            tp_level = entry * (1.0 + (tps[reached] if reached < 4 else 0.0))
            sl_hit = lo <= sl_level
            tp_hit = (reached < 4) and (hi >= tp_level)

            if sl_hit and tp_hit:
                return (reached > 0), ((sl_level - entry) / entry), i
            if tp_hit:
                reached += 1
                if reached <= 3:
                    cur_sl = sls[reached]
                else:
                    return True, tps[-1], i
                continue
            if sl_hit:
                return (reached > 0), ((sl_level - entry) / entry), i
        else:
            sl_level = entry * (1.0 - cur_sl)
            tp_level = entry * (1.0 - (tps[reached] if reached < 4 else 0.0))
            sl_hit = hi >= sl_level
            tp_hit = (reached < 4) and (lo <= tp_level)

            if sl_hit and tp_hit:
                return (reached > 0), ((entry - sl_level) / entry) * -1.0, i
            if tp_hit:
                reached += 1
                if reached <= 3:
                    cur_sl = sls[reached]
                else:
                    return True, -tps[-1], i
                continue
            if sl_hit:
                return (reached > 0), ((entry - sl_level) / entry) * -1.0, i

    last_close = float(prices.iloc[-1]["close"])
    pnl = (last_close - entry) / entry if side == "long" else -((last_close - entry) / entry)
    return (reached > 0), pnl, len(prices) - 1


# --------- Feature & scaler helpers ----------
def restore_scaler_identity(n_features: int):
    if StandardScaler is None:
        # minimal identity-like stub
        class _S: pass
        s = _S()
        s.mean_ = np.zeros(n_features, dtype=np.float32)
        s.scale_ = np.ones(n_features, dtype=np.float32)
        s.var_ = np.ones(n_features, dtype=np.float32)
        return s
    s = StandardScaler()
    s.mean_ = np.zeros(n_features, dtype=np.float32)
    s.scale_ = np.ones(n_features, dtype=np.float32)
    s.var_ = np.ones(n_features, dtype=np.float32)
    s.n_features_in_ = n_features
    return s


def restore_scaler_from_npz(npz_path: str, n_features: int):
    if not os.path.exists(npz_path):
        return restore_scaler_identity(n_features)
    data = np.load(npz_path)
    s = restore_scaler_identity(n_features)
    s.mean_ = data["mean_"]
    s.scale_ = data["scale_"]
    s.var_ = data["var_"]
    return s


def build_sequences(X: np.ndarray, seq_len: int) -> np.ndarray:
    n, f = X.shape
    if n < seq_len:
        return np.empty((0, seq_len, f), dtype=np.float32)
    out = np.zeros((n - seq_len + 1, seq_len, f), dtype=np.float32)
    for i in range(seq_len - 1, n):
        out[i - seq_len + 1] = X[i - seq_len + 1:i + 1]
    return out


# --------- Adapters for models ----------
class BaseAdapter:
    def name(self) -> str:
        raise NotImplementedError

    def supports(self, base_path: str) -> bool:
        """Return True if adapter can load this model path (without extension)."""
        raise NotImplementedError

    def load(self, base_path: str, input_shape: Tuple[int, int]) -> object:
        """Return a callable predictor f(X_seq)->prob or f(X_flat)->prob or action."""
        raise NotImplementedError

    def predict_signals(self, predictor, X_seq: np.ndarray, prob_threshold: float) -> np.ndarray:
        """Return binary signals array (len = X_seq)."""
        raise NotImplementedError


# ---- DQN (PyTorch MLP over flattened windows) ----
class DQNAdapter(BaseAdapter):
    def name(self): return "DQN"

    def supports(self, base_path: str) -> bool:
        return os.path.exists(base_path + ".pt") and os.path.exists(base_path + ".features.json")

    def load(self, base_path: str, input_shape: Tuple[int, int]):
        if torch is None or nn is None:
            raise RuntimeError("PyTorch not available")

        T, F = input_shape
        input_size = T * F

        class DQNClassifier(nn.Module):
            def __init__(self, input_size: int, hidden: int = 256, dropout: float = 0.2):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(input_size, hidden),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden, hidden),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden, 1)
                )
            def forward(self, x):
                b, t, f = x.shape
                x = x.reshape(b, t * f)
                return self.net(x)

        model = DQNClassifier(input_size)
        state = torch.load(base_path + ".pt", map_location="cpu")
        try:
            model.load_state_dict(state)
        except Exception:
            # if full model was saved instead of state_dict
            model = state
        model.eval()

        def predictor(X_seq: np.ndarray) -> np.ndarray:
            probs = []
            with torch.no_grad():
                for i in range(0, len(X_seq), 4096):
                    xb = torch.from_numpy(X_seq[i:i+4096])
                    logits = model(xb)
                    p = torch.sigmoid(logits).squeeze(1).cpu().numpy()
                    probs.append(p)
            return np.concatenate(probs, axis=0)

        return predictor

    def predict_signals(self, predictor, X_seq: np.ndarray, prob_threshold: float) -> np.ndarray:
        probs = predictor(X_seq)
        return (probs >= prob_threshold).astype(np.int32)


# ---- XGBoost (either Booster or sklearn wrapper) ----
class XGBAdapter(BaseAdapter):
    def name(self): return "XGBoost"

    def supports(self, base_path: str) -> bool:
        # Allow .json/.ubj (Booster) or .pkl/.joblib (sklearn)
        return any(os.path.exists(base_path + ext) for ext in [".json", ".ubj", ".pkl", ".joblib"]) and \
               os.path.exists(base_path + ".features.json")

    def load(self, base_path: str, input_shape: Tuple[int, int]):
        if xgb is None:
            raise RuntimeError("xgboost not available")

        # Prefer Booster
        booster = None
        if os.path.exists(base_path + ".json") or os.path.exists(base_path + ".ubj"):
            booster = xgb.Booster()
            if os.path.exists(base_path + ".json"):
                booster.load_model(base_path + ".json")
            else:
                booster.load_model(base_path + ".ubj")
            def predictor(X_seq: np.ndarray) -> np.ndarray:
                # Flatten windows to per-row features by using last timestep
                X_flat = X_seq[:, -1, :]
                dmat = xgb.DMatrix(X_flat)
                proba = booster.predict(dmat)
                if proba.ndim == 2 and proba.shape[1] > 1:
                    proba = proba[:, 1]
                return proba
            return predictor

        # sklearn wrapper
        if joblib is None:
            raise RuntimeError("joblib not available to load sklearn xgboost model")
        model = joblib.load(base_path + (".pkl" if os.path.exists(base_path + ".pkl") else ".joblib"))
        def predictor(X_seq: np.ndarray) -> np.ndarray:
            X_flat = X_seq[:, -1, :]
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X_flat)
                if isinstance(proba, np.ndarray) and proba.ndim == 2:
                    return proba[:, 1]
                return proba
            # fallback to decision_function or predict
            if hasattr(model, "decision_function"):
                z = model.decision_function(X_flat)
                # sigmoid
                return 1 / (1 + np.exp(-z))
            y = model.predict(X_flat)
            return y.astype(np.float32)
        return predictor

    def predict_signals(self, predictor, X_seq: np.ndarray, prob_threshold: float) -> np.ndarray:
        probs = predictor(X_seq)
        return (probs >= prob_threshold).astype(np.int32)


# ---- PPO via Stable-Baselines3 ----
class PPOAdapter(BaseAdapter):
    def name(self): return "PPO"

    def supports(self, base_path: str) -> bool:
        return os.path.exists(base_path + ".zip") and os.path.exists(base_path + ".features.json")

    def load(self, base_path: str, input_shape: Tuple[int, int]):
        if SB3_PPO is None:
            raise RuntimeError("stable_baselines3 not available")
        model = SB3_PPO.load(base_path + ".zip", device="cpu")

        def predictor(X_seq: np.ndarray) -> np.ndarray:
            # Use last timestep as observation (flatten)
            X_flat = X_seq[:, -1, :]
            # SB3 predict returns action; we map action==1 -> 1. No prob; threshold ignored.
            actions, _ = model.predict(X_flat, deterministic=False)
            return actions.astype(np.int32)
        return predictor

    def predict_signals(self, predictor, X_seq: np.ndarray, prob_threshold: float) -> np.ndarray:
        acts = predictor(X_seq)
        # already discrete 0/1
        return acts.astype(np.int32)


# ---- Generic PyTorch (CNN/LSTM/Transformer) via state_dict or full module ----
class TorchGenericAdapter(BaseAdapter):
    """Attempts to load any .pt with .features.json. Expects the .pt either to be a full nn.Module
    (pickled) or a state_dict for a Module defined inside the checkpoint under key 'class_name'/'arch'.
    You can also provide a Python file 'base_path.modeldef.py' exporting 'build_model(input_shape)->nn.Module'.
    """
    def name(self): return "TorchGeneric"

    def supports(self, base_path: str) -> bool:
        return os.path.exists(base_path + ".pt") and os.path.exists(base_path + ".features.json")

    def load(self, base_path: str, input_shape: Tuple[int, int]):
        if torch is None or nn is None:
            raise RuntimeError("PyTorch not available")
        state = torch.load(base_path + ".pt", map_location="cpu")
        if isinstance(state, nn.Module):
            model = state
        else:
            # Try a sidecar builder file
            builder_path = base_path + ".modeldef.py"
            if os.path.exists(builder_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("modeldef_mod", builder_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore
                if not hasattr(mod, "build_model"):
                    raise RuntimeError(f"builder missing build_model(): {builder_path}")
                T, F = input_shape
                model = mod.build_model((T, F))
                model.load_state_dict(state)
            else:
                raise RuntimeError("Cannot reconstruct model class. Provide *.modeldef.py with build_model().")
        model.eval()

        def predictor(X_seq: np.ndarray) -> np.ndarray:
            probs = []
            with torch.no_grad():
                for i in range(0, len(X_seq), 2048):
                    xb = torch.from_numpy(X_seq[i:i+2048])
                    logits = model(xb)
                    if logits.ndim == 2 and logits.shape[1] == 1:
                        p = torch.sigmoid(logits).squeeze(1).cpu().numpy()
                    else:
                        # If model outputs logits for 2 classes
                        p = torch.softmax(logits, dim=-1)[:, -1].cpu().numpy()
                    probs.append(p)
            return np.concatenate(probs, axis=0)

        return predictor

    def predict_signals(self, predictor, X_seq: np.ndarray, prob_threshold: float) -> np.ndarray:
        probs = predictor(X_seq)
        return (probs >= prob_threshold).astype(np.int32)


# --------- Registry of adapters ----------
ADAPTERS = [
    DQNAdapter(),         # exact match for our DQN
    XGBAdapter(),         # xgboost (booster/sklearn)
    PPOAdapter(),         # stable-baselines3
    TorchGenericAdapter() # fallback for CNN/LSTM/Transformer with builder
]


# --------- Core backtest flow ----------
def run_backtest_for_model(base_path: str,
                           data_dir: str,
                           prob_threshold: float,
                           ladder: Ladder,
                           leverage: float,
                           stake_frac: float,
                           start_deposit: float) -> Optional[Dict]:

    spec_path = base_path + ".features.json"
    if not os.path.exists(spec_path):
        return None

    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    timeframe = spec.get("timeframe", "1d")
    target = spec.get("target", "long")
    features: List[str] = spec.get("features", [])
    seq_len = int(spec.get("seq_len", 32))

    csv_path = os.path.join(data_dir, f"BTCUSDT_{timeframe}_critical_indicators.csv")
    if not os.path.exists(csv_path):
        return {"model": os.path.basename(base_path), "status": f"⚠️ data not found: {csv_path}", "trades": 0}

    df = pd.read_csv(csv_path)
    # time sort if column exists
    for tc in ["timestamp","time","open_time","date"]:
        if tc in df.columns:
            df = df.sort_values(tc).reset_index(drop=True)
            break
    df = df.dropna().reset_index(drop=True)

    # Validate columns
    needed = set(["open","high","low","close"] + features)
    if not needed.issubset(df.columns):
        missing = list(needed - set(df.columns))
        return {"model": os.path.basename(base_path), "status": f"❌ missing columns: {missing}", "trades": 0}

    # Scaler
    scaler_npz = base_path + ".scaler.npz"
    scaler = restore_scaler_from_npz(scaler_npz, len(features))

    X_raw = df[features].to_numpy(dtype=np.float32)
    X = (X_raw - scaler.mean_) / scaler.scale_

    X_seq = build_sequences(X, seq_len)
    if len(X_seq) == 0:
        return {"model": os.path.basename(base_path), "status": "❌ not enough data for sequences", "trades": 0}

    prices = df.iloc[seq_len - 1:].reset_index(drop=True)

    # Pick adapter
    adapter = None
    for a in ADAPTERS:
        if a.supports(base_path):
            adapter = a
            break
    if adapter is None:
        return {"model": os.path.basename(base_path), "status": "❌ no adapter supports this model", "trades": 0}

    try:
        predictor = adapter.load(base_path, (X_seq.shape[1], X_seq.shape[2]))
    except Exception as e:
        return {"model": os.path.basename(base_path), "status": f"❌ load error: {type(e).__name__}: {e}", "trades": 0}

    try:
        signals = adapter.predict_signals(predictor, X_seq, prob_threshold)
    except Exception as e:
        return {"model": os.path.basename(base_path), "status": f"❌ inference error: {type(e).__name__}: {e}", "trades": 0}

    # Walk forward
    deposit = start_deposit
    trades = wins = losses = 0
    i = 0
    while i < len(prices) - 1:
        if signals[i] == 1:
            side = target  # "long" or "short"
            win, pnl_pct, exit_idx = simulate_trade_path(prices, i, side, ladder)
            stake = deposit * stake_frac
            deposit += stake * (pnl_pct * leverage)
            trades += 1
            wins += int(win)
            losses += int(not win)
            i = exit_idx + 1
        else:
            i += 1

    winrate = (wins / trades) * 100.0 if trades > 0 else 0.0

    return {
        "model": os.path.basename(base_path),
        "adapter": adapter.name(),
        "timeframe": timeframe,
        "target": target,
        "variant": spec.get("variant_id",""),
        "features": "|".join(features),
        "seq_len": seq_len,
        "prob_threshold": prob_threshold,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate_%": round(winrate, 2),
        "final_deposit": round(deposit, 2),
        "status": "✅ ok"
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="test/data/BTCUSDT")
    ap.add_argument("--models_root", default="models", help="Root with subfolders: DQN, CNN, LSTM, Transformer, XGBoost, PPO")
    ap.add_argument("--out_csv", default="backtest_results_all.csv")
    ap.add_argument("--prob_threshold", type=float, default=0.5)
    ap.add_argument("--start_deposit", type=float, default=100.0)
    ap.add_argument("--stake_frac", type=float, default=0.01)
    ap.add_argument("--leverage", type=float, default=20.0)
    args = ap.parse_args()

    subdirs = ["DQN", "CNN", "LSTM", "Transformer", "XGBoost", "PPO"]
    rows = []

    for sd in subdirs:
        mdir = os.path.join(args.models_root, sd)
        if not os.path.isdir(mdir):
            continue
        specs = glob.glob(os.path.join(mdir, "*.features.json"))
        for spec_path in specs:
            base = spec_path[:-len(".features.json")]
            res = run_backtest_for_model(
                base_path=base,
                data_dir=args.data_dir,
                prob_threshold=args.prob_threshold,
                ladder=Ladder(),
                leverage=args.leverage,
                stake_frac=args.stake_frac,
                start_deposit=args.start_deposit
            )
            if res is None:
                continue
            print(f"{res.get('model','?')}: {res.get('status')} ({res.get('adapter','?')})"
                  f" | trades={res.get('trades',0)} winrate={res.get('winrate_%',0)}% final={res.get('final_deposit','?')}")
            rows.append(res)

    if rows:
        out_df = pd.DataFrame(rows)
        out_df.to_csv(args.out_csv, index=False)
        print("📊 Saved results to", args.out_csv)
    else:
        print("No models were backtested. Ensure *.features.json exist for each model.")

if __name__ == "__main__":
    main()
