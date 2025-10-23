# backtest/start_backtest.py
# Запуск (приклади):
#   python backtest/start_backtest.py --symbols BTCUSDT --timeframes 1d
#   python backtest/start_backtest.py --symbols BTCUSDT,ETHUSDT --timeframes 15m,30m,1h,4h,1d
# Порада: почни з тих TF, де у тебе реально є моделі (у прикладі ти показав 1d).

import os
import sys
import json
import argparse
import pickle
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from glob import glob

import numpy as np
import pandas as pd

# ==== локальні імпорти варіантів ====
try:
    from backtest.variants import VARIANTS, ENSEMBLE_MODE, ENSEMBLE_PARAMS
except Exception:
    VARIANTS = [f"V{i}" for i in range(1, 16)]
    ENSEMBLE_MODE = "single"
    ENSEMBLE_PARAMS = {"k": 2, "families": None}

# ==== константи бектесту ====
BT_END_DATE = datetime(2025, 9, 30)  # 30.09.2025
INIT_DEPOSIT = 1000.0
RISK_PCT = 0.01  # 1%
TP_LEVELS = [0.01, 0.02, 0.03]  # 1%, 2%, 3%
TP_PARTS  = [0.50, 0.70, 1.00]  # 50% від повної, 70% від залишку, 100% від залишку
SL_LEVEL  = -0.02  # -2%
AFTER_TP1_SL = 0.0
AFTER_TP2_SL = 0.0

TIMEFRAMES_DEFAULT = ["15m","30m","1h","4h","1d"]
FAMILIES = ["CNN","LSTM","DQN","Transformer","XGBoost","QLearning","PPO"]
SIDES = ["long","short"]

# ==== шляхи ====
def ensure_dir(p: str): os.makedirs(p, exist_ok=True)

def legacy_data_dir(symbol: str) -> str:
    return os.path.join("test", "data", symbol.upper())

def backtest_data_dir(symbol: str) -> str:
    return os.path.join("backtest", "data", symbol.upper())

def models_dir_of(symbol: str, family: str) -> str:
    return os.path.join("models", symbol.upper(), family)

def results_dir_of(family: str, tf: str) -> str:
    return os.path.join("backtest","results",family,tf)

def read_symbols_from_file(path: str) -> List[str]:
    with open(path,"r",encoding="utf-8") as f:
        syms = [s.strip().upper() for s in f if s.strip()]
    return syms

# ==== пошук єдиного файлу індикаторів для TF ====
def _candidate_paths_for_tf(symbol: str, tf: str) -> List[str]:
    base1 = backtest_data_dir(symbol)
    base2 = legacy_data_dir(symbol)
    fname = f"{symbol}_{tf}_critical_indicators"
    return [
        os.path.join(base1, fname + ".parquet"),
        os.path.join(base2, fname + ".parquet"),
        os.path.join(base1, fname + ".csv"),
        os.path.join(base2, fname + ".csv"),
    ]

def has_data_for(symbol: str, tfs: List[str]) -> Tuple[bool, List[str]]:
    missing = []
    for tf in tfs:
        candidates = _candidate_paths_for_tf(symbol, tf)
        if not any(os.path.exists(p) for p in candidates):
            missing.append(" | ".join(candidates))
    return (len(missing)==0, missing)

def _read_any_df(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path)

def load_df(symbol: str, tf: str, end_dt: datetime) -> pd.DataFrame:
    candidates = _candidate_paths_for_tf(symbol, tf)
    chosen = None
    for p in candidates:
        if os.path.exists(p):
            chosen = p
            break
    if chosen is None:
        return pd.DataFrame()

    df = _read_any_df(chosen)

    # обмеження за датою (шукаємо найтиповіші назви)
    time_col = None
    for c in ["timestamp","time","open_time","date","close_time"]:
        if c in df.columns:
            time_col = c; break

    if time_col is None:
        return df.reset_index(drop=True)

    s = df[time_col]
    if np.issubdtype(s.dtype, np.number):
        vals = s.astype("int64")
        if vals.max() > 10_000_000_000:
            dt = pd.to_datetime(vals // 1000, unit="s")
        else:
            dt = pd.to_datetime(vals, unit="s")
    else:
        dt = pd.to_datetime(s, errors="coerce")

    df = df.loc[dt <= pd.Timestamp(end_dt)].reset_index(drop=True)
    return df

# ==== утиліти інференсу ====
def _load_scaler_npz(path: str) -> Optional[Dict[str,np.ndarray]]:
    if not os.path.exists(path): return None
    z = np.load(path)
    return {k: z[k] for k in z.files}

def _apply_scaler(arr: np.ndarray, sc: Optional[Dict[str,np.ndarray]]) -> np.ndarray:
    if not sc: return arr.astype(np.float32)
    mean = sc.get("mean_", None); scale = sc.get("scale_", None)
    if mean is None or scale is None: return arr.astype(np.float32)
    mean = mean.astype(np.float32); scale = scale.astype(np.float32)
    scale = np.where(scale==0.0, 1.0, scale)
    return ((arr - mean) / scale).astype(np.float32)

def _seq_windows(X: np.ndarray, seq_len: int=32) -> np.ndarray:
    if len(X) < seq_len: return np.empty((0, seq_len, X.shape[1]), np.float32)
    out = np.stack([X[i-seq_len+1:i+1] for i in range(seq_len-1, len(X))], axis=0)
    return out.astype(np.float32)

def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _load_vecnorm_stats(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            obj = pickle.load(f)
        stats = {}
        if isinstance(obj, dict):
            obs_rms = obj.get("obs_rms", None)
            stats["clip_obs"] = float(obj.get("clip_obs", 10.0))
            stats["epsilon"] = float(obj.get("epsilon", 1e-8))
            if obs_rms is not None:
                stats["mean"] = np.array(getattr(obs_rms, "mean", None), dtype=np.float32)
                stats["var"]  = np.array(getattr(obs_rms, "var", None), dtype=np.float32)
                return stats
        mean = np.array(getattr(obj, "mean", None), dtype=np.float32) if hasattr(obj, "mean") else None
        var  = np.array(getattr(obj, "var", None), dtype=np.float32) if hasattr(obj, "var") else None
        if mean is not None and var is not None:
            stats["mean"] = mean; stats["var"] = var
            stats["clip_obs"] = 10.0; stats["epsilon"] = 1e-8
            return stats
    except Exception:
        return None
    return None

def _apply_vecnorm(obs: np.ndarray, stats: Optional[dict]) -> np.ndarray:
    if stats is None:
        return obs.astype(np.float32)
    mean = stats.get("mean", None)
    var  = stats.get("var", None)
    eps  = float(stats.get("epsilon", 1e-8))
    clip = float(stats.get("clip_obs", 10.0))
    if mean is None or var is None:
        return obs.astype(np.float32)
    x = (obs - mean) / np.sqrt(var + eps)
    x = np.clip(x, -clip, clip)
    return x.astype(np.float32)

# ==== ДИСКАВЕРІ МОДЕЛЕЙ (під твої імена файлів) ====
def _read_manifest_if_any(dir_path: str) -> Optional[pd.DataFrame]:
    # читаємо features_manifest.csv, якщо є. Гнучко ставимось до колонок.
    paths = glob(os.path.join(dir_path, "features_manifest.csv"))
    if not paths:
        return None
    try:
        df = pd.read_csv(paths[0])
        return df
    except Exception:
        return None

def resolve_model_paths(family: str, symbol: str, tf: str, side: str, variant: str) -> Dict[str, Optional[str]]:
    """
    Повертає словник із шляхами до артефактів для конкретної комбінації.
    Якщо не знайдено — повертає всі None.
    """
    mdir = models_dir_of(symbol, family)
    res = {"base": None, "pt": None, "booster": None, "features": None, "spec": None,
           "scaler": None, "disc": None, "qtable": None, "zip": None, "vecnorm": None}

    # Спроба через маніфест (для XGBoost/QLearning найімовірніше)
    manifest = _read_manifest_if_any(mdir)
    if manifest is not None:
        # нормалізуємо назви колонок до нижнього
        mcols = {c.lower(): c for c in manifest.columns}
        def _get(col):
            for k,v in mcols.items():
                if k == col: return v
            return None
        col_tf = _get("timeframe") or _get("tf")
        col_side = _get("side")
        col_var = _get("variant") or _get("v") or _get("version")
        if col_tf and col_side and col_var:
            sub = manifest[(manifest[col_tf].astype(str)==tf) &
                           (manifest[col_side].astype(str)==side) &
                           (manifest[col_var].astype(str)==variant)]
            if len(sub) >= 1:
                row = sub.iloc[0]
                # спробуємо поля із шляхами, незалежно від назв
                for key in ["model","booster","booster_path","qtable","disc","scaler","features","spec","vecnorm","zip","checkpoint"]:
                    col = _get(key)
                    if col and isinstance(row[col], str) and os.path.exists(row[col]):
                        # в залежності від розширення розкладаємо
                        p = row[col]
                        if p.endswith(".json"): res["booster"] = p
                        elif p.endswith(".pt"): res["pt"] = p
                        elif p.endswith(".npz"):
                            if "q" in os.path.basename(p).lower(): res["qtable"] = p
                            else: res["scaler"] = p
                        elif p.endswith(".zip"): res["zip"] = p
                        elif p.endswith(".pkl"): res["vecnorm"] = p
                        elif p.endswith(".features.json"): res["features"] = p
                        elif p.endswith(".spec.json"): res["spec"] = p
                # якщо щось знайшли — повертаємо (без glob)
                if any(res[k] for k in res):
                    return res

    # Якщо маніфест не допоміг — йдемо через шаблони імен
    if family == "XGBoost":
        # підтримуємо XGB_{tf}_{side}_{V}.json і .features.json/.spec.json
        base_glob = os.path.join(mdir, f"*{tf}_{side}_{variant}*")
        cand_json = sorted([p for p in glob(base_glob + ".json") if "features" not in p and "spec" not in p])
        if cand_json:
            res["booster"] = cand_json[0]
        cand_feat = sorted(glob(base_glob + ".features.json")) + sorted(glob(base_glob + ".spec.json"))
        if cand_feat:
            # надаємо пріоритет features.json, але spec.json теж ок
            res["features"] = next((p for p in cand_feat if p.endswith(".features.json")), cand_feat[0])
            if res["features"].endswith(".spec.json"):
                res["spec"] = res["features"]
                res["features"] = None
        return res

    if family in ("CNN","DQN","Transformer","LSTM"):
        # .pt обов'язковий (назви можуть бути довільні, головне щоб містили tf/side/V)
        base_glob = os.path.join(mdir, f"*{tf}_{side}_{variant}*")
        cand_pt = sorted(glob(base_glob + ".pt"))
        if cand_pt: res["pt"] = cand_pt[0]
        res["scaler"] = next(iter(sorted(glob(base_glob + ".scaler.npz"))), None)
        # features/spec як завгодно
        cand_feat = sorted(glob(base_glob + ".features.json")) + sorted(glob(base_glob + ".spec.json"))
        if cand_feat:
            # будь-який з них
            pick = cand_feat[0]
            if pick.endswith(".features.json"): res["features"] = pick
            else: res["spec"] = pick
        return res

    if family == "QLearning":
        base_glob = os.path.join(mdir, f"*{tf}_{side}_{variant}*")
        res["qtable"] = next(iter(sorted(glob(base_glob + ".qtable.npz"))), None)
        res["disc"]   = next(iter(sorted(glob(base_glob + ".disc.json"))), None)
        res["scaler"] = next(iter(sorted(glob(base_glob + ".scaler.npz"))), None)
        # features/spec необов'язково
        cand_feat = sorted(glob(base_glob + ".features.json")) + sorted(glob(base_glob + ".spec.json"))
        if cand_feat:
            pick = cand_feat[0]
            if pick.endswith(".features.json"): res["features"] = pick
            else: res["spec"] = pick
        return res

    if family == "PPO":
        # дозволяємо суфікс символа: PPO_{tf}_{side}_{V}_{SYMBOL}.zip
        base_glob = os.path.join(mdir, f"*{tf}_{side}_{variant}*")
        cand_zip  = sorted(glob(base_glob + ".best.zip")) or sorted(glob(base_glob + ".zip"))
        res["zip"] = cand_zip[0] if cand_zip else None
        res["spec"] = next(iter(sorted(glob(base_glob + ".spec.json"))), None)
        res["vecnorm"] = next(iter(sorted(glob(base_glob + ".vecnorm.pkl"))), None)
        return res

    return res

# ==== інференс по родинах ====
def infer_probs_torch(model_pt: str, X_seq: np.ndarray, head: str="flat", device: str="cpu") -> np.ndarray:
    import torch
    sd = torch.load(model_pt, map_location=device)
    if isinstance(sd, dict) and "state_dict" in sd:
        state = sd["state_dict"]
    else:
        state = sd

    B, T, F = X_seq.shape
    x = torch.from_numpy(X_seq)
    if head == "flat":
        in_dim = T*F
        net = torch.nn.Sequential(
            torch.nn.Linear(in_dim, 256), torch.nn.ReLU(),
            torch.nn.Linear(256, 1)
        )
        try:
            net.load_state_dict(state, strict=False)
        except Exception:
            pass
        with torch.no_grad():
            p = torch.sigmoid(net(x.reshape(B, -1).float())).cpu().numpy().reshape(-1)
        return p
    else:
        lstm = torch.nn.LSTM(input_size=F, hidden_size=64, num_layers=2, batch_first=True, dropout=0.2)
        head = torch.nn.Linear(64,1)
        try:
            lstm.load_state_dict({k.replace("module.",""):v for k,v in state.items() if "lstm" in k}, strict=False)
            head.load_state_dict({k.replace("module.",""):v for k,v in state.items() if "fc" in k or "cls" in k}, strict=False)
        except Exception:
            pass
        with torch.no_grad():
            out,_ = lstm(x.float())
            last = out[:,-1,:]
            p = torch.sigmoid(head(last)).cpu().numpy().reshape(-1)
        return p

def infer_family_probs(family: str, symbol: str, tf: str, side: str, variant: str,
                       df: pd.DataFrame) -> Tuple[np.ndarray, float]:
    """
    Повертає (probabilities [N], threshold) для класифікації 1/0 (enter/hold)
    """
    paths = resolve_model_paths(family, symbol, tf, side, variant)
    feats = None
    seq_len = 32
    thr = 0.5

    # спробуємо spec/features, щоб дістати features/seq_len/best_thr/open_action
    spec_obj = None
    for jpath in [paths.get("spec"), paths.get("features")]:
        if jpath and os.path.exists(jpath):
            spec_obj = _load_json(jpath)
            if spec_obj: break
    spec = spec_obj or {}
    if "features" in spec: feats = [str(c) for c in spec["features"]]
    if "seq_len" in spec and spec["seq_len"]:
        try: seq_len = int(spec["seq_len"])
        except Exception: pass
    if "best_thr" in spec:
        try: thr = float(spec["best_thr"])
        except Exception: pass

    # підготуємо X
    if feats is None:
        excl = {"open_time","close_time","target_long","target_short"}
        base_df = df[[c for c in df.columns if c not in excl]]
        X_all = base_df.select_dtypes(include=[np.number]).values.astype(np.float32)
    else:
        miss = [f for f in feats if f not in df.columns]
        if miss:
            X_all = df.select_dtypes(include=[np.number]).values.astype(np.float32)
        else:
            X_all = df[feats].values.astype(np.float32)

    # масштабування
    sc = paths.get("scaler")
    sc_dict = _load_scaler_npz(sc) if sc else None
    X_all = _apply_scaler(X_all, sc_dict)

    # Torch-родини
    if family in ("CNN","LSTM","DQN","Transformer"):
        if not paths.get("pt") or not os.path.exists(paths["pt"]):
            return np.zeros((0,),np.float32), thr
        X_seq = _seq_windows(X_all, seq_len=seq_len)
        if len(X_seq)==0:
            return np.zeros((0,),np.float32), thr
        head = "seq" if family in ("LSTM","Transformer") else "flat"
        probs = infer_probs_torch(paths["pt"], X_seq, head=head, device="cpu")
        return probs, thr

    if family == "XGBoost":
        if not paths.get("booster") or not os.path.exists(paths["booster"]):
            return np.zeros((0,),np.float32), thr
        import xgboost as xgb
        dval = xgb.DMatrix(X_all, feature_names=[f"f{i}" for i in range(X_all.shape[1])])
        booster = xgb.Booster()
        booster.load_model(paths["booster"])
        p = booster.predict(dval)
        return p.reshape(-1), thr

    if family == "QLearning":
        if not paths.get("qtable") or not os.path.exists(paths["qtable"]) or not paths.get("disc") or not os.path.exists(paths["disc"]):
            return np.zeros((0,),np.float32), thr
        z = np.load(paths["qtable"])
        with open(paths["disc"],"r",encoding="utf-8") as f:
            disc = json.load(f).get("edges") or json.load(f)
        edges = [np.asarray(e) for e in disc]
        def digitize_row(row):
            bins = []
            for i,val in enumerate(row[:len(edges)]):
                e = edges[i]
                b = np.digitize(val, e[1:-1], right=False)
                bins.append(int(b))
            return tuple(bins)
        probs = []
        for i in range(len(X_all)):
            s = digitize_row(X_all[i])
            key = str(s)
            if key in z.files:
                q = z[key]
                q0 = float(q[0]) if len(q)>0 else 0.0
                q1 = float(q[1]) if len(q)>1 else 0.0
                p = 1.0/(1.0+np.exp(-(q1 - q0)))
            else:
                p = 0.5
            probs.append(p)
        return np.asarray(probs,np.float32), thr

    if family == "PPO":
        model_zip = paths.get("zip")
        if not model_zip or not os.path.exists(model_zip):
            return np.zeros((0,), np.float32), thr

        vec_stats = _load_vecnorm_stats(paths.get("vecnorm")) if paths.get("vecnorm") else None

        seq_mode = int(spec.get("seq_len", 1) or 1) > 1 or bool(spec.get("obs_mode_seq_flat", False))
        if seq_mode:
            X_seq = _seq_windows(X_all, seq_len=seq_len)
            if len(X_seq) == 0:
                return np.zeros((0,), np.float32), thr
            X_obs = X_seq.reshape((X_seq.shape[0], -1)).astype(np.float32)
        else:
            X_obs = X_all.astype(np.float32)

        if vec_stats is not None:
            X_obs = _apply_vecnorm(X_obs, vec_stats)

        try:
            import torch
            from stable_baselines3 import PPO as SB3_PPO
            model = SB3_PPO.load(model_zip, device="cpu")
            obs_tensor = torch.as_tensor(X_obs, dtype=torch.float32)
            with torch.no_grad():
                dist = model.policy.get_distribution(obs_tensor)
                distribution = getattr(dist, "distribution", None)
                probs_tensor = getattr(distribution, "probs", None) if distribution is not None else getattr(dist, "probs", None)
                if probs_tensor is None:
                    raise RuntimeError("No probs in distribution")
                probs_np = probs_tensor.cpu().numpy()
                open_action = int(spec.get("open_action", 1))
                if probs_np.ndim == 1:
                    probs_np = probs_np.reshape(-1, 2)
                p_open = probs_np[:, open_action] if open_action < probs_np.shape[1] else 1.0 - probs_np[:, 0]
                return p_open.astype(np.float32), thr
        except Exception:
            try:
                from stable_baselines3 import PPO as SB3_PPO
                model = SB3_PPO.load(model_zip, device="cpu")
                actions = []
                for i in range(X_obs.shape[0]):
                    a, _ = model.predict(X_obs[i], deterministic=True)
                    actions.append(int(a))
                open_action = int(spec.get("open_action", 1))
                probs = np.array([1.0 if a == open_action else 0.0 for a in actions], dtype=np.float32)
                return probs, thr
            except Exception:
                return np.zeros((0,), np.float32), thr

    return np.zeros((0,),np.float32), thr

# ==== TP/SL ====
@dataclass
class TradeStats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    pnl_abs: float = 0.0

def simulate_trade_path(side: str, entry: float, high: float, low: float, stop_level: float,
                        tp_idx_done: int) -> Tuple[int, float, bool]:
    is_long = (side == "long")
    tp_levels = [TP_LEVELS[i] for i in range(tp_idx_done, len(TP_LEVELS))]
    hits = 0
    sl_hit = False
    stop_px = entry * (1.0 + stop_level if is_long else 1.0 - stop_level)

    if is_long and low <= stop_px:
        return tp_idx_done + hits, stop_level, True
    if (not is_long) and high >= stop_px:
        return tp_idx_done + hits, stop_level, True

    for tl in tp_levels:
        tp_px = entry * (1.0 + tl if is_long else 1.0 - tl)
        if (is_long and high >= tp_px) or ((not is_long) and low <= tp_px):
            hits += 1
            if tp_idx_done + hits == 1:
                stop_level = AFTER_TP1_SL
            elif tp_idx_done + hits == 2:
                stop_level = AFTER_TP2_SL
        else:
            break

    return tp_idx_done + hits, stop_level, False

def trade_pnl(side: str, entry: float, tp_hits: int, sl_triggered: bool) -> float:
    if tp_hits == 0 and sl_triggered:
        return SL_LEVEL  # -2%

    parts = []
    remain = 1.0
    for i in range(tp_hits):
        part = TP_PARTS[i] * remain
        parts.append(part)
        remain -= part

    pnl = 0.0
    for i, part in enumerate(parts):
        lvl = TP_LEVELS[i]
        pnl += part * lvl  # side симетрично

    return pnl  # решта в 0%

# ==== бектест однієї комбінації ====
def backtest_series(symbol: str, tf: str, side: str, family: str, variant: str) -> Dict[str, float]:
    df = load_df(symbol, tf, BT_END_DATE)
    if df.empty:
        return {"trades":0,"wins":0,"losses":0,"winrate":0.0,"final_deposit":INIT_DEPOSIT}

    probs, thr = infer_family_probs(family, symbol, tf, side, variant, df)
    if probs.size == 0:
        return {"trades":0,"wins":0,"losses":0,"winrate":0.0,"final_deposit":INIT_DEPOSIT}

    price_col = "close" if "close" in df.columns else None
    high_col  = "high"  if "high"  in df.columns else None
    low_col   = "low"   if "low"   in df.columns else None

    # Вирівнюємо довжини (для секвенсів PPO/LSTM/...)
    offset = max(0, len(df) - len(probs))
    sub = df.iloc[offset:].reset_index(drop=True)
    preds = (probs >= thr).astype(int)

    # Якщо немає OHLC — рахуємо тільки кількість входів (без PnL)
    if price_col is None or high_col is None or low_col is None:
        n = int(preds.sum())
        return {"trades": n, "wins": 0, "losses": n, "winrate": 0.0, "final_deposit": INIT_DEPOSIT}

    dep = INIT_DEPOSIT
    trades = wins = losses = 0

    i = 0
    while i < len(sub)-1:
        if preds[i] != 1:
            i += 1
            continue

        entry = float(sub.loc[i, price_col])
        size_usd = dep * RISK_PCT
        tp_idx_done = 0
        stop_level = SL_LEVEL
        closed = False
        j = i + 1
        while j < len(sub):
            high = float(sub.loc[j, high_col])
            low  = float(sub.loc[j, low_col])
            tp_idx_done, stop_level, sl_now = simulate_trade_path(side, entry, high, low, stop_level, tp_idx_done)
            if sl_now or tp_idx_done == 3:
                pnl_pct = trade_pnl(side, entry, tp_hits=tp_idx_done, sl_triggered=sl_now)
                dep += size_usd * pnl_pct
                trades += 1
                wins += 1 if tp_idx_done >= 1 else 0
                losses += 1 if tp_idx_done == 0 else 0
                closed = True
                i = j
                break
            j += 1
        if not closed:
            pnl_pct = 0.0 if tp_idx_done > 0 else 0.0
            dep += size_usd * pnl_pct
            trades += 1
            wins += 1 if tp_idx_done > 0 else 0
            losses += 1 if tp_idx_done == 0 else 0
            i = j

    winrate = (wins / trades)*100.0 if trades>0 else 0.0
    return {"trades":trades,"wins":wins,"losses":losses,"winrate":winrate,"final_deposit":dep}

def save_stats_table(rows: List[Dict], out_csv: str):
    ensure_dir(os.path.dirname(out_csv))
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8")

# ==== головна ====
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", default=None, help="файл зі списком монет (по одній у рядку)")
    ap.add_argument("--symbols", default=None, help="кома-сепаратед перелік, напр. BTCUSDT,ETHUSDT,...")
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES_DEFAULT))
    ap.add_argument("--strict-models", action="store_true",
                    help="Якщо вмикнути — зупинятись при відсутності моделей для будь-якої комбінації")
    args = ap.parse_args()

    if args.symbols_file:
        symbols = read_symbols_from_file(args.symbols_file)
    else:
        sy = args.symbols.split(",") if args.symbols else []
        symbols = [s.strip().upper() for s in sy if s.strip()]
    if not symbols:
        print("⚠️ Не задані символи. Використайте --symbols-file або --symbols.")
        sys.exit(1)

    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    # 1) Перевірка даних (лише індикатори, без таргетів)
    any_missing = False
    for s in symbols:
        ok, miss = has_data_for(s, tfs)
        if not ok:
            any_missing = True
            print(f"❌ Дані відсутні для {s}:")
            for m in miss[:10]:
                print("   -", m)
            if len(miss)>10:
                print(f"   ... ще {len(miss)-10} файлів")
    if any_missing:
        print("Зупинка: спершу дозавантаж індикатори (без таргетів).")
        sys.exit(2)

    # 2) НЕ зупиняємось при відсутності моделей (за замовчуванням). Лише ворнінги.
    #    Якщо --strict-models, тоді перевіримо і зупинимо.
    missing_summary: Dict[str, List[str]] = {}
    if args.strict_models:
        for s in symbols:
            for fam in FAMILIES:
                for tf in tfs:
                    for side in SIDES:
                        for v in VARIANTS:
                            paths = resolve_model_paths(fam, s, tf, side, v)
                            needed = []
                            if fam in ("CNN","DQN","Transformer","LSTM"):
                                if not paths.get("pt"): needed.append("*.pt")
                            elif fam == "XGBoost":
                                if not paths.get("booster"): needed.append("*.json(booster)")
                                if not (paths.get("features") or paths.get("spec")):
                                    needed.append("*.features.json|*.spec.json")
                            elif fam == "QLearning":
                                if not paths.get("qtable"): needed.append("*.qtable.npz")
                                if not paths.get("disc"):   needed.append("*.disc.json")
                            elif fam == "PPO":
                                if not paths.get("zip"):    needed.append("*.zip|*.best.zip")
                            if needed:
                                key = f"{s} [{fam}] {tf} {side} {v}"
                                missing_summary.setdefault(key, []).extend(needed)
        if missing_summary:
            print("❌ Моделі відсутні (strict mode):")
            shown = 0
            for k, lst in missing_summary.items():
                print("   ", k, " → ", ", ".join(sorted(set(lst))))
                shown += 1
                if shown > 25:
                    print("   ... ще є відсутні, але список обрізано")
                    break
            sys.exit(3)

    # 3) Бектест
    all_rows = []
    skipped = 0
    for fam in FAMILIES:
        for tf in tfs:
            family_rows = []
            for s in symbols:
                for side in SIDES:
                    for v in VARIANTS:
                        paths = resolve_model_paths(fam, s, tf, side, v)
                        # якщо ключовий артефакт відсутній — пропускаємо (м'який режим)
                        need_ok = True
                        if fam in ("CNN","DQN","Transformer","LSTM"):
                            need_ok = bool(paths.get("pt"))
                        elif fam == "XGBoost":
                            need_ok = bool(paths.get("booster"))
                        elif fam == "QLearning":
                            need_ok = bool(paths.get("qtable") and paths.get("disc"))
                        elif fam == "PPO":
                            need_ok = bool(paths.get("zip"))
                        if not need_ok:
                            skipped += 1
                            continue

                        stats = backtest_series(symbol=s, tf=tf, side=side, family=fam, variant=v)
                        row = {
                            "family": fam, "timeframe": tf, "symbol": s, "side": side, "variant": v,
                            **stats
                        }
                        family_rows.append(row)
                        all_rows.append(row)
            if family_rows:
                out_csv = os.path.join(results_dir_of(fam, tf), f"bt_{fam}_{tf}_single.csv")
                save_stats_table(family_rows, out_csv)
                print(f"💾 Saved: {out_csv}")

    # 4) Загальна агрегована статистика
    if all_rows:
        out_all = os.path.join("backtest","results","bt__ALL__summary.csv")
        save_stats_table(all_rows, out_all)
        print(f"📊 Загальна таблиця: {out_all}")
    else:
        print("⚠️ Не знайдено жодної придатної моделі для обраних параметрів. "
              "Спробуй інший TF/варіанти або перевір імена файлів у models/.")

if __name__ == "__main__":
    main()
