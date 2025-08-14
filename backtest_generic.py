# backtest_generic.py
# Backtester for DQN & CNN with 1m exits, TF-specific SL, CNN state_dict compatibility,
# and automatic channel adaptation (F -> expected_in_ch).
# - TF SL: 15m:1.5%, 30m:2.0%, 1h:2.5%, 4h/1d:3.0% ; після TP1 -> SL=0%
# - Entries: rising-edge above prob_threshold; EMA smoothing; cooldown
# - 1m exits (--use_m1_exit) з робастним мапінгом OHLC (або явні --m1_* прапорці)
# - CNN compat: conv1/bn1/conv2/bn2/fc1/fc2 keys
# - Channel adapter: зводить фічі до очікуваних in_ch (mean/first/trim/pad)

import os, json, glob, argparse, importlib.util
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import numpy as np
import pandas as pd

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler

# ========= Architectures =========
class DQNClassifier(nn.Module):
    def __init__(self, input_size: int, hidden: int = 256, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, 1)
        )
    def forward(self, x):  # x: (B,T,F)
        b,t,f = x.shape
        return self.net(x.reshape(b, t*f))

class CNN1DClassifier(nn.Module):
    """Generic CNN: (B,T,F) -> (B,1)"""
    def __init__(self, in_ch: int, channels=(64,128), kernel_size=3, dropout: float=0.1):
        super().__init__()
        k = kernel_size; pad = k//2
        c1, c2 = channels
        self.body = nn.Sequential(
            nn.Conv1d(in_ch, c1, k, padding=pad), nn.ReLU(),
            nn.Conv1d(c1, c1, k, padding=pad), nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(c1, c2, k, padding=pad), nn.ReLU(),
            nn.Conv1d(c2, c2, k, padding=pad), nn.ReLU(),
        )
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.head = nn.Linear(c2, 1)
    def forward(self, x):  # (B,T,F)
        x = x.permute(0,2,1)  # (B,F,T)
        x = self.body(x)
        x = self.pool(x).squeeze(-1)
        return self.head(x)

class CNNCompat(nn.Module):
    """
    Сумісна CNN під state_dict з ключами:
    conv1, bn1, conv2, bn2, fc1, fc2
    in_ch/канали/ядра зчитуються зі shapes у state_dict.
    """
    def __init__(self, state: Dict[str, torch.Tensor]):
        super().__init__()
        w1 = state["conv1.weight"]           # (c1, in_ch, k1)
        c1, in_ch, k1 = w1.shape
        w2 = state["conv2.weight"]           # (c2, c1, k2)
        c2, c1_w, k2 = w2.shape
        assert c1_w == c1, "conv2 in_ch != conv1 out_ch"
        fc1_out, fc1_in = state["fc1.weight"].shape
        fc2_out, fc2_in = state["fc2.weight"].shape
        assert fc2_out == 1, "fc2 must output 1 logit"

        self.conv1 = nn.Conv1d(in_ch, c1, k1, padding=k1//2)
        self.bn1   = nn.BatchNorm1d(c1)
        self.conv2 = nn.Conv1d(c1, c2, k2, padding=k2//2)
        self.bn2   = nn.BatchNorm1d(c2)
        self.pool  = nn.AdaptiveAvgPool1d(1)
        self.fc1   = nn.Linear(c2, fc1_out)
        self.fc2   = nn.Linear(fc1_out, 1)
        self.relu  = nn.ReLU()

    def forward(self, x):  # (B,T,F_expected)
        x = x.permute(0,2,1)        # (B,F,T)
        x = self.relu(self.bn1(self.conv1(x)))
        x = self.relu(self.bn2(self.conv2(x)))
        x = self.pool(x).squeeze(-1)  # (B,C)
        x = self.relu(self.fc1(x))
        x = self.fc2(x)
        return x

# ========= TP/SL =========
@dataclass
class Ladder:
    tp1: float = 0.005
    tp2: float = 0.010
    tp3: float = 0.015
    tp4: float = 0.020
    sl0: float = 0.015   # override per TF
    be1: float = 0.000   # 0% після TP1
    be2: float = 0.000
    be3: float = 0.000

TF_INIT_SL = {"15m":0.015,"30m":0.020,"1h":0.025,"4h":0.030,"1d":0.030}
def initial_sl_by_timeframe(tf: str) -> float: return TF_INIT_SL.get(tf, 0.030)
def tf_to_timedelta(tf: str) -> pd.Timedelta:
    return pd.Timedelta(minutes={"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}.get(tf,1440))

# ========= Time & OHLC =========
def _norm(s: str) -> str:
    return str(s).replace("\ufeff","").strip().lower().replace(" ","_").replace("-","_")

def build_colmap(df: pd.DataFrame) -> Dict[str,str]:
    return {_norm(c): c for c in df.columns}

def get_first(df: pd.DataFrame, keys: List[str]) -> Optional[str]:
    cmap = build_colmap(df)
    for k in keys:
        if k in cmap: return cmap[k]
    return None

def detect_time_col(df: pd.DataFrame, override: Optional[str]=None) -> Optional[str]:
    if override:
        return get_first(df, [_norm(override)])
    keys = ["timestamp","time","open_time","date","datetime","close_time","kline_close_time","t","end_time"]
    return get_first(df, keys)

def to_utc_datetime(series: pd.Series) -> pd.Series:
    x = series.iloc[0]
    if pd.api.types.is_numeric_dtype(series):
        unit = "ms" if float(x) > 1e12 else "s"
        return pd.to_datetime(series, unit=unit, utc=True)
    return pd.to_datetime(series, utc=True, errors="coerce")

def compute_close_times(df: pd.DataFrame, timeframe: Optional[str], time_override: Optional[str]=None) -> pd.Series:
    tcol = detect_time_col(df, time_override)
    if tcol is None:
        return pd.to_datetime(pd.RangeIndex(len(df)), unit="s", utc=True)
    t = to_utc_datetime(df[tcol])
    if _norm(tcol) == "open_time" and timeframe is not None:
        t = t + tf_to_timedelta(timeframe)
    return t

def ensure_ohlc(df: pd.DataFrame,
                o_override: Optional[str]=None,
                h_override: Optional[str]=None,
                l_override: Optional[str]=None,
                c_override: Optional[str]=None) -> Tuple[bool, List[str]]:
    # Автоперейменування, якщо немає хедера (0..N):
    if all(isinstance(c, (int, np.integer)) for c in df.columns) and len(df.columns) >= 5:
        cols = list(df.columns)
        rename = {cols[0]:"open_time", cols[1]:"open", cols[2]:"high", cols[3]:"low", cols[4]:"close"}
        df.rename(columns=rename, inplace=True)

    syn = {
        "open":  ["open","o","open_price","price_open","openprice"],
        "high":  ["high","h","high_price","price_high","highprice","max"],
        "low":   ["low","l","low_price","price_low","lowprice","min"],
        "close": ["close","c","close_price","price_close","closeprice","last"]
    }
    want = {
        "open": _norm(o_override) if o_override else None,
        "high": _norm(h_override) if h_override else None,
        "low":  _norm(l_override) if l_override else None,
        "close":_norm(c_override) if c_override else None,
    }
    cmap = build_colmap(df); mapping: Dict[str,str] = {}; missing: List[str] = []
    for std in ["open","high","low","close"]:
        if want[std] and want[std] in cmap:
            mapping[std] = cmap[want[std]]; continue
        col = get_first(df, syn[std]) or get_first(df, [std]) or get_first(df, [std.replace("_"," ")])
        if col is None: missing.append(std)
        else: mapping[std] = col
    if missing: return False, missing
    for std, orig in mapping.items():
        if std not in df.columns: df[std] = df[orig]
    return True, []

# ========= Simulations =========
def simulate_trade_path(prices: pd.DataFrame, entry_idx: int, side: str, ladder: Ladder) -> Tuple[bool,float,int,int]:
    if entry_idx >= len(prices) - 1:
        return False, 0.0, entry_idx, 0
    entry = float(prices.loc[entry_idx,"close"])
    tps = [ladder.tp1, ladder.tp2, ladder.tp3, ladder.tp4]
    sls = [ladder.sl0, ladder.be1, ladder.be2, ladder.be3]
    reached, cur_sl = 0, (-sls[0] if side=="long" else sls[0])
    for i in range(entry_idx+1, len(prices)):
        hi, lo = float(prices.loc[i,"high"]), float(prices.loc[i,"low"])
        if side=="long":
            sl = entry*(1.0+cur_sl); tp = entry*(1.0+(tps[reached] if reached<4 else 0.0))
            sl_hit, tp_hit = (lo<=sl), ((reached<4) and (hi>=tp))
            if sl_hit and tp_hit: return (reached>0), ((sl-entry)/entry), i, reached
            if tp_hit:
                reached += 1
                if reached<=3: cur_sl = sls[reached]; continue
                else: return True, tps[-1], i, reached
            if sl_hit: return (reached>0), ((sl-entry)/entry), i, reached
        else:
            sl = entry*(1.0-cur_sl); tp = entry*(1.0-(tps[reached] if reached<4 else 0.0))
            sl_hit, tp_hit = (hi>=sl), ((reached<4) and (lo<=tp))
            if sl_hit and tp_hit: return (reached>0), ((entry-sl)/entry*-1.0), i, reached
            if tp_hit:
                reached += 1
                if reached<=3: cur_sl = sls[reached]; continue
                else: return True, -tps[-1], i, reached
            if sl_hit: return (reached>0), ((entry-sl)/entry*-1.0), i, reached
    last_close = float(prices.iloc[-1]["close"])
    pnl = (last_close-entry)/entry if side=="long" else -((last_close-entry)/entry)
    return (reached>0), pnl, len(prices)-1, reached

def simulate_trade_path_m1(entry_price: float, entry_close_time: pd.Timestamp, side: str,
                           ladder: Ladder, m1_df: pd.DataFrame, time_override: Optional[str]=None) -> Tuple[bool,float,pd.Timestamp,int]:
    ok,_ = ensure_ohlc(m1_df)
    tcol = detect_time_col(m1_df, time_override)
    if not ok or tcol is None:
        last_close = float(m1_df.iloc[-1][get_first(m1_df,["close"])]) if get_first(m1_df,["close"]) else entry_price
        pnl = (last_close - entry_price)/entry_price if side=="long" else -((last_close - entry_price)/entry_price)
        return False, pnl, entry_close_time, 0
    t = to_utc_datetime(m1_df[tcol])
    if _norm(tcol) == "open_time": t = t + pd.Timedelta(minutes=1)
    mask = t > entry_close_time
    if not mask.any(): return False, 0.0, entry_close_time, 0
    dfm, t = m1_df.loc[mask].reset_index(drop=True), t.loc[mask].reset_index(drop=True)

    tps = [ladder.tp1, ladder.tp2, ladder.tp3, ladder.tp4]
    sls = [ladder.sl0, ladder.be1, ladder.be2, ladder.be3]
    reached, cur_sl = 0, (-sls[0] if side=="long" else sls[0])

    for i in range(len(dfm)):
        hi, lo = float(dfm.loc[i,"high"]), float(dfm.loc[i,"low"])
        if side=="long":
            sl = entry_price*(1.0+cur_sl); tp = entry_price*(1.0+(tps[reached] if reached<4 else 0.0))
            sl_hit, tp_hit = (lo<=sl), ((reached<4) and (hi>=tp))
            if sl_hit and tp_hit: return (reached>0), ((sl-entry_price)/entry_price), t[i], reached
            if tp_hit:
                reached += 1
                if reached<=3: cur_sl = sls[reached]; continue
                else: return True, tps[-1], t[i], reached
            if sl_hit: return (reached>0), ((sl-entry_price)/entry_price), t[i], reached
        else:
            sl = entry_price*(1.0-cur_sl); tp = entry_price*(1.0-(tps[reached] if reached<4 else 0.0))
            sl_hit, tp_hit = (hi>=sl), ((reached<4) and (lo<=tp))
            if sl_hit and tp_hit: return (reached>0), ((entry_price-sl)/entry_price*-1.0), t[i], reached
            if tp_hit:
                reached += 1
                if reached<=3: cur_sl = sls[reached]; continue
                else: return True, -tps[-1], t[i], reached
            if sl_hit: return (reached>0), ((entry_price-sl)/entry_price*-1.0), t[i], reached
    last_close = float(dfm.iloc[-1]["close"])
    pnl = (last_close-entry_price)/entry_price if side=="long" else -((last_close-entry_price)/entry_price)
    return (reached>0), pnl, t.iloc[-1], reached

# ========= Helpers =========
def ema(arr: np.ndarray, span: int) -> np.ndarray:
    if span<=1: return arr
    alpha = 2.0/(span+1.0)
    out = np.empty_like(arr); out[0]=arr[0]
    for i in range(1,len(arr)): out[i] = alpha*arr[i] + (1-alpha)*out[i-1]
    return out

def restore_scaler_from_npz(npz: str, n: int) -> StandardScaler:
    if os.path.exists(npz):
        d = np.load(npz); s = StandardScaler()
        s.mean_, s.scale_, s.var_, s.n_features_in_ = d["mean_"], d["scale_"], d["var_"], d["mean_"].shape[0]
        return s
    s = StandardScaler()
    s.mean_ = np.zeros(n, np.float32); s.scale_ = np.ones(n, np.float32); s.var_ = np.ones(n, np.float32); s.n_features_in_=n
    return s

def build_sequences(X: np.ndarray, T: int) -> np.ndarray:
    n,f = X.shape
    if n<T: return np.empty((0,T,f), np.float32)
    out = np.zeros((n-T+1, T, f), np.float32)
    for i in range(T-1,n): out[i-T+1] = X[i-T+1:i+1]
    return out

def load_custom_model(modeldef_path: str, input_shape: Tuple[int,int]) -> nn.Module:
    spec = importlib.util.spec_from_file_location("user_modeldef", modeldef_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    if not hasattr(mod, "build_model"):
        raise RuntimeError(f"{modeldef_path} must define build_model(input_shape=(T,F))")
    return mod.build_model(input_shape)

def choose_arch(base_path: str, spec_dict: Dict, models_dir: str) -> str:
    s = (spec_dict.get("arch") or spec_dict.get("model_type") or spec_dict.get("type") or "").lower()
    if "cnn" in s: return "cnn"
    if "cnn" in base_path.lower() or "cnn" in models_dir.lower(): return "cnn"
    return "dqn"

def resolve_seq_len(spec: Dict, state: Dict, features_len: int, arch: str, default_seq_len: int) -> int:
    for k in ["seq_len","lookback","window","n_steps","time_steps","context","seq","steps","T"]:
        if k in spec:
            try: return int(spec[k])
            except: pass
    if arch == "dqn" and isinstance(state, dict):
        for ck in ["net.0.weight","fc1.weight","linear1.weight"]:
            if ck in state:
                in_features = state[ck].shape[1]
                if features_len>0 and in_features % features_len == 0:
                    T = in_features // features_len
                    if T >= 2: return int(T)
    return int(default_seq_len)

def expected_in_ch_from_state(state: Dict[str, torch.Tensor], fallback: int) -> int:
    if "conv1.weight" in state: return int(state["conv1.weight"].shape[1])
    if "body.0.weight" in state: return int(state["body.0.weight"].shape[1])
    return int(fallback)

def adapt_in_channels(Xseq: np.ndarray, expected_in_ch: int, mode: str="first", idx: int=0) -> np.ndarray:
    """
    Адаптер каналів: приводить Xseq.shape[2] -> expected_in_ch.
    - Якщо expected_in_ch == 1 і F>1: 'first' (за індексом) або 'mean'
    - Якщо expected_in_ch < F: беремо перші expected_in_ch
    - Якщо expected_in_ch > F: доповнюємо нулями
    """
    B,T,F = Xseq.shape
    if F == expected_in_ch:
        return Xseq
    if expected_in_ch == 1 and F > 1:
        if mode == "mean":
            v = Xseq.mean(axis=2, keepdims=True)
        else:
            i = max(0, min(idx, F-1))
            v = Xseq[:,:,i:i+1]
        return v.astype(np.float32)
    if expected_in_ch < F:
        return Xseq[:,:,:expected_in_ch].astype(np.float32)
    # expected_in_ch > F -> pad zeros
    pad = np.zeros((B,T,expected_in_ch - F), dtype=Xseq.dtype)
    return np.concatenate([Xseq, pad], axis=2).astype(np.float32)

def try_build_cnn_compat(state: Dict[str, torch.Tensor]) -> Optional[nn.Module]:
    keys = set(state.keys())
    needed = {"conv1.weight","conv1.bias","bn1.weight","bn1.bias","bn1.running_mean","bn1.running_var",
              "conv2.weight","conv2.bias","bn2.weight","bn2.bias","bn2.running_mean","bn2.running_var",
              "fc1.weight","fc1.bias","fc2.weight","fc2.bias"}
    if not needed.issubset(keys):
        return None
    return CNNCompat(state)

# ========= One model =========
def backtest_one_model(base: str, df_tf: pd.DataFrame, spec: Dict, args, m1_df: Optional[pd.DataFrame]) -> Tuple[Dict,List[Dict]]:
    timeframe = spec.get("timeframe","1d")
    target = spec.get("target","long")
    features: List[str] = list(spec["features"]) if "features" in spec else []
    if not features:
        return ({"model": os.path.basename(base)+".pt","timeframe":timeframe,"target":target,
                 "arch": choose_arch(base, spec, args.models_dir), "features":"", "seq_len": None,
                 "prob_threshold":args.prob_threshold, "trades":0,"wins":0,"losses":0,"winrate_%":0.0,
                 "final_deposit":args.start_deposit, "status":"❌ spec has no 'features'"},
                [])

    ensure_ohlc(df_tf)
    needed = set(["open","high","low","close"]+features)
    if not needed.issubset(df_tf.columns):
        miss = list(needed - set(df_tf.columns))
        return ({"model": os.path.basename(base)+".pt","timeframe":timeframe,"target":target,
                 "arch": choose_arch(base, spec, args.models_dir),
                 "features":"|".join(features), "seq_len": None, "prob_threshold":args.prob_threshold,
                 "trades":0,"wins":0,"losses":0,"winrate_%":0.0,"final_deposit":args.start_deposit,
                 "status": f"❌ missing columns: {miss}"},
                [])

    # sort by time
    t_tf_close_full = compute_close_times(df_tf, timeframe)
    order = np.argsort(t_tf_close_full.values)
    if not np.all(order == np.arange(len(df_tf))):
        df_tf = df_tf.iloc[order].reset_index(drop=True)
        t_tf_close_full = t_tf_close_full.iloc[order].reset_index(drop=True)
    df_tf = df_tf.dropna().reset_index(drop=True)
    t_tf_close_full = t_tf_close_full.loc[df_tf.index].reset_index(drop=True)

    # scaler & features
    scaler = restore_scaler_from_npz(base+".scaler.npz", len(features))
    X_raw = df_tf[features].to_numpy(np.float32)
    X = (X_raw - scaler.mean_) / scaler.scale_

    arch = choose_arch(base, spec, args.models_dir)
    # load weights
    try:
        raw_state = torch.load(base+".pt", map_location=args.device, weights_only=True)
    except TypeError:
        raw_state = torch.load(base+".pt", map_location=args.device)

    # resolve seq_len
    state_for_infer = raw_state if isinstance(raw_state, dict) else (raw_state.state_dict() if hasattr(raw_state,"state_dict") else {})
    seq_len = resolve_seq_len(spec, state_for_infer, len(features), arch, args.default_seq_len)

    # sequences
    X_seq = build_sequences(X, seq_len)
    if len(X_seq)==0:
        return ({"model": os.path.basename(base)+".pt","timeframe":timeframe,"target":target,"arch":arch,
                 "features":"|".join(features),"seq_len":seq_len,"prob_threshold":args.prob_threshold,
                 "trades":0,"wins":0,"losses":0,"winrate_%":0.0,"final_deposit":args.start_deposit,
                 "status":"❌ not enough data for sequences"},
                [])

    prices = df_tf.iloc[seq_len-1:].reset_index(drop=True)
    t_tf_close = t_tf_close_full.iloc[seq_len-1:].reset_index(drop=True)
    input_T, input_F = X_seq.shape[1], X_seq.shape[2]

    # expected in_ch (for CNN)
    exp_in_ch = input_F
    if isinstance(state_for_infer, dict):
        exp_in_ch = expected_in_ch_from_state(state_for_infer, fallback=input_F)

    # adapt channels if CNN
    X_seq_feed = X_seq if arch!="cnn" else adapt_in_channels(X_seq, exp_in_ch, args.cnn_reduce, args.cnn_reduce_idx)

    # build model
    modeldef_path = base + ".modeldef.py"
    if os.path.exists(modeldef_path):
        model = load_custom_model(modeldef_path, (input_T, X_seq_feed.shape[2])).to(args.device)
    else:
        if arch == "cnn":
            # стандартний CNN з очікуваним числом каналів
            model = CNN1DClassifier(in_ch=X_seq_feed.shape[2]).to(args.device)
        else:
            model = DQNClassifier(input_size=input_T*input_F).to(args.device)

    # try to load weights
    if isinstance(raw_state, nn.Module):
        model = raw_state.to(args.device)
    else:
        try:
            model.load_state_dict(raw_state, strict=True)
        except Exception:
            # спроба через CNNCompat
            compat = try_build_cnn_compat(raw_state)
            if compat is None:
                raise
            model = compat.to(args.device)
            model.load_state_dict(raw_state, strict=True)

    model.eval()

    # predict
    probs = []
    with torch.no_grad():
        bs=4096
        for s in range(0,len(X_seq_feed),bs):
            xb = torch.from_numpy(X_seq_feed[s:s+bs]).to(args.device)
            p = torch.sigmoid(model(xb)).squeeze(1).detach().cpu().numpy()
            probs.append(p)
    probs = np.concatenate(probs)
    if args.signal_ema and args.signal_ema>1: probs = ema(probs, args.signal_ema)

    above = probs >= args.prob_threshold
    prev = np.concatenate([[False], above[:-1]])
    entries = (above & ~prev).astype(np.int32)

    deposit = float(args.start_deposit); trades=wins=losses=0; i=0; trades_rows=[]
    use_m1 = (args.use_m1_exit and (m1_df is not None))
    ladder = Ladder(sl0=initial_sl_by_timeframe(timeframe), be1=0.0, be2=0.0, be3=0.0)
    tf_times = t_tf_close.to_numpy(dtype="datetime64[ns]")

    while i < len(prices)-1:
        if entries[i]==1:
            side = target
            entry_price = float(prices.loc[i,"close"]); entry_close_time = t_tf_close.iloc[i]
            if use_m1:
                win,pnl,exit_time,tp_cnt = simulate_trade_path_m1(entry_price, entry_close_time, side, ladder, m1_df,
                                                                  time_override=args.m1_time)
                j = int(np.searchsorted(tf_times, np.datetime64(exit_time.to_datetime64()), side="left"))
                j = min(max(j, i), len(prices)-1); exit_idx=j
            else:
                win,pnl,exit_idx,tp_cnt = simulate_trade_path(prices, i, side, ladder)
            stake = deposit * args.stake_frac
            deposit += stake * (pnl * args.leverage)
            trades += 1; wins += int(win); losses += int(not win)
            if args.save_trades:
                trades_rows.append({
                    "model": os.path.basename(base)+".pt","timeframe":timeframe,"target":target,
                    "arch": arch, "seq_len_used": seq_len, "in_ch_used": X_seq_feed.shape[2],
                    "entry_index":i,"exit_index":exit_idx,"entry_time":str(entry_close_time),"exit_time":str(t_tf_close.iloc[exit_idx]),
                    "tp_hits":tp_cnt,"pnl_pct_raw":round(pnl,6),"pnl_pct_leveraged":round(pnl*args.leverage,6),
                    "stake_used":round(stake,2),"deposit_after":round(deposit,2),"used_m1":bool(use_m1)
                })
            i = exit_idx + 1 + int(args.cooldown)
        else:
            i += 1

    winrate = (wins/trades)*100.0 if trades>0 else 0.0
    return ({
        "model": os.path.basename(base)+".pt","timeframe":timeframe,"target":target,
        "arch": arch,"features":"|".join(features),"seq_len":seq_len,
        "prob_threshold":args.prob_threshold,"trades":trades,"wins":wins,"losses":losses,
        "winrate_%":round(winrate,2),"final_deposit":round(deposit,2),"status":"✅ ok"
    }, trades_rows)

# ========= Main =========
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="test/data/BTCUSDT")
    ap.add_argument("--models_dir", default="models/DQN")
    ap.add_argument("--m1_path", default="test/data/BTCUSDT/BTCUSDT_1m.csv.gz")
    ap.add_argument("--use_m1_exit", action="store_true")
    # explicit 1m column overrides
    ap.add_argument("--m1_time", default=None)
    ap.add_argument("--m1_open", default=None)
    ap.add_argument("--m1_high", default=None)
    ap.add_argument("--m1_low",  default=None)
    ap.add_argument("--m1_close",default=None)
    # CNN channel adapter
    ap.add_argument("--cnn_reduce", choices=["first","mean"], default="first",
                    help="Якщо модель очікує менше каналів, ніж фіч: 'first' або 'mean'")
    ap.add_argument("--cnn_reduce_idx", type=int, default=0,
                    help="Яку фічу брати при cnn_reduce=first (default: 0)")
    ap.add_argument("--out_csv", default="backtest_results.csv")
    ap.add_argument("--prob_threshold", type=float, default=0.6)
    ap.add_argument("--start_deposit", type=float, default=100.0)
    ap.add_argument("--stake_frac", type=float, default=0.01)
    ap.add_argument("--leverage", type=float, default=20.0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--signal_ema", type=int, default=0)
    ap.add_argument("--cooldown", type=int, default=0)
    ap.add_argument("--save_trades", action="store_true")
    ap.add_argument("--default_seq_len", type=int, default=64)
    args = ap.parse_args()

    # load 1m (optional)
    m1_df = None
    if args.use_m1_exit:
        if os.path.exists(args.m1_path):
            m1_df = pd.read_csv(args.m1_path)
            ok, miss = ensure_ohlc(m1_df, args.m1_open, args.m1_high, args.m1_low, args.m1_close)
            if not ok:
                print(f"⚠️  1m file missing OHLC columns {miss}; using TF-only exits.")
                m1_df = None
        else:
            print(f"⚠️  1m file not found: {args.m1_path}; using TF-only exits.")

    specs = glob.glob(os.path.join(args.models_dir, "*.features.json"))
    if not specs:
        print("No feature specs found in", args.models_dir); return

    summaries, all_trades = [], []
    for js in specs:
        base = js[:-len(".features.json")]
        if not os.path.exists(base+".pt"):
            print(f"⚠️  weights missing for spec: {os.path.basename(js)} -> skipping"); continue
        with open(js,"r",encoding="utf-8") as f: spec = json.load(f)
        tf = spec.get("timeframe","1d")
        csv_path = os.path.join(args.data_dir, f"BTCUSDT_{tf}_critical_indicators.csv")
        if not os.path.exists(csv_path):
            print(f"⚠️  data not found for timeframe={tf}: {csv_path}"); continue
        df_tf = pd.read_csv(csv_path)
        ensure_ohlc(df_tf)

        summary, trades = backtest_one_model(base, df_tf, spec, args, m1_df)
        print(f"{summary['model']} ({summary['arch']}): {summary['status']} | trades={summary['trades']} "
              f"wins={summary['wins']} losses={summary['losses']} winrate={summary['winrate_%']}% "
              f"final={summary['final_deposit']} (seq_len={summary['seq_len']})")
        summaries.append(summary)
        if args.save_trades: all_trades += trades

    if summaries:
        pd.DataFrame(summaries).to_csv(args.out_csv, index=False)
        print("📊 Saved results to", args.out_csv)
        if args.save_trades and all_trades:
            trades_path = os.path.splitext(args.out_csv)[0] + "_trades.csv"
            pd.DataFrame(all_trades).to_csv(trades_path, index=False)
            print("🧾 Saved trades to", trades_path)
    else:
        print("No models were backtested.")

if __name__ == "__main__":
    main()
