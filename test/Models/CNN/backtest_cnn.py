# backtest_generic.py
# DQN backtester over indicator-only CSVs (no targets).
# - Різний стартовий SL за таймфреймом:
#   15m: 1.5%, 30m: 2.0%, 1h: 2.5%, 4h/1d: 3.0%
# - Після TP1 стоп у 0% (break-even); далі не підтягуємо.
# - Вхід лише на перетині порога вгору (rising edge), опційно EMA і cooldown.
# - Пише зведення у CSV (і за бажанням — трейди у <out>_trades.csv).

import os
import json
import glob
import argparse
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import numpy as np
import pandas as pd

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler


# =======================
# Model: DQN-style MLP over flattened window
# =======================
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

    def forward(self, x):  # x: (B, T, F)
        b, t, f = x.shape
        x = x.reshape(b, t * f)
        return self.net(x)  # (B, 1)


# =======================
# TP/SL ladder
# =======================
@dataclass
class Ladder:
    tp1: float = 0.005   # +0.5%
    tp2: float = 0.010   # +1.0%
    tp3: float = 0.015   # +1.5%
    tp4: float = 0.020   # +2.0%
    sl0: float = 0.015   # буде перезаписаний по TF
    be1: float = 0.000   # 0% після TP1 (break-even)
    be2: float = 0.000   # тримаємо 0%
    be3: float = 0.000   # тримаємо 0%

TF_INIT_SL = {
    "15m": 0.015,
    "30m": 0.020,
    "1h":  0.025,
    "4h":  0.030,
    "1d":  0.030,
}

def initial_sl_by_timeframe(tf: str) -> float:
    return TF_INIT_SL.get(tf, 0.030)

def simulate_trade_path(
    prices: pd.DataFrame,
    entry_idx: int,
    side: str,
    ladder: Ladder
) -> Tuple[bool, float, int, int]:
    """
    Консервативно: якщо в одному барі досяжні і TP, і SL — спочатку спрацює SL.
    Повертає (win, pnl_pct, exit_idx, tp_count). Вхід — по close entry_idx.
    """
    if entry_idx >= len(prices) - 1:
        return False, 0.0, entry_idx, 0

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
                return (reached > 0), ((sl_level - entry) / entry), i, reached
            if tp_hit:
                reached += 1
                if reached <= 3:
                    cur_sl = sls[reached]  # після TP1 -> 0%, далі тримаємо 0%
                    continue
                else:
                    return True, tps[-1], i, reached
            if sl_hit:
                return (reached > 0), ((sl_level - entry) / entry), i, reached
        else:  # SHORT
            sl_level = entry * (1.0 - cur_sl)  # SL вище входу
            tp_level = entry * (1.0 - (tps[reached] if reached < 4 else 0.0))
            sl_hit = hi >= sl_level
            tp_hit = (reached < 4) and (lo <= tp_level)

            if sl_hit and tp_hit:
                return (reached > 0), ((entry - sl_level) / entry) * -1.0, i, reached
            if tp_hit:
                reached += 1
                if reached <= 3:
                    cur_sl = sls[reached]
                    continue
                else:
                    return True, -tps[-1], i, reached
            if sl_hit:
                return (reached > 0), ((entry - sl_level) / entry) * -1.0, i, reached

    last_close = float(prices.iloc[-1]["close"])
    pnl = (last_close - entry) / entry if side == "long" else -((last_close - entry) / entry)
    return (reached > 0), pnl, len(prices) - 1, reached


# =======================
# Utils
# =======================
def ema(arr: np.ndarray, span: int) -> np.ndarray:
    if span <= 1:
        return arr
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i-1]
    return out

def detect_time_col(df: pd.DataFrame) -> Optional[str]:
    for tc in ["timestamp", "time", "open_time", "date"]:
        if tc in df.columns:
            return tc
    return None

def restore_scaler_from_npz(npz_path: str, n_features: int) -> StandardScaler:
    if os.path.exists(npz_path):
        data = np.load(npz_path)
        scaler = StandardScaler()
        scaler.mean_ = data["mean_"]
        scaler.scale_ = data["scale_"]
        scaler.var_ = data["var_"]
        scaler.n_features_in_ = scaler.mean_.shape[0]
        return scaler
    # identity fallback
    scaler = StandardScaler()
    scaler.mean_ = np.zeros(n_features, dtype=np.float32)
    scaler.scale_ = np.ones(n_features, dtype=np.float32)
    scaler.var_ = np.ones(n_features, dtype=np.float32)
    scaler.n_features_in_ = n_features
    return scaler

def build_sequences(X: np.ndarray, seq_len: int) -> np.ndarray:
    n, f = X.shape
    if n < seq_len:
        return np.empty((0, seq_len, f), dtype=np.float32)
    out = np.zeros((n - seq_len + 1, seq_len, f), dtype=np.float32)
    for i in range(seq_len - 1, n):
        out[i - seq_len + 1] = X[i - seq_len + 1:i + 1]
    return out


# =======================
# Backtest one model
# =======================
def backtest_one_dqn(base_path: str, df: pd.DataFrame, spec: Dict, args) -> Tuple[Dict, List[Dict]]:
    timeframe = spec.get("timeframe", "1d")
    target = spec.get("target", "long")
    features: List[str] = list(spec["features"])
    seq_len: int = int(spec["seq_len"])

    needed = set(["open", "high", "low", "close"] + features)
    if not needed.issubset(df.columns):
        missing = list(needed - set(df.columns))
        return ({
            "model": os.path.basename(base_path) + ".pt",
            "timeframe": timeframe,
            "target": target,
            "variant": spec.get("variant_id", ""),
            "features": "|".join(features),
            "seq_len": seq_len,
            "prob_threshold": args.prob_threshold,
            "trades": 0, "wins": 0, "losses": 0,
            "winrate_%": 0.0, "final_deposit": args.start_deposit,
            "status": f"❌ missing columns: {missing}"
        }, [])

    tc = detect_time_col(df)
    if tc is not None:
        df = df.sort_values(tc).reset_index(drop=True)
    df = df.dropna().reset_index(drop=True)

    scaler = restore_scaler_from_npz(base_path + ".scaler.npz", len(features))
    X_raw = df[features].to_numpy(dtype=np.float32)
    X = (X_raw - scaler.mean_) / scaler.scale_

    X_seq = build_sequences(X, seq_len)
    if len(X_seq) == 0:
        return ({
            "model": os.path.basename(base_path) + ".pt",
            "timeframe": timeframe,
            "target": target,
            "variant": spec.get("variant_id", ""),
            "features": "|".join(features),
            "seq_len": seq_len,
            "prob_threshold": args.prob_threshold,
            "trades": 0, "wins": 0, "losses": 0,
            "winrate_%": 0.0, "final_deposit": args.start_deposit,
            "status": "❌ not enough data for sequences"
        }, [])

    prices = df.iloc[seq_len - 1:].reset_index(drop=True)

    input_size = X_seq.shape[1] * X_seq.shape[2]
    model = DQNClassifier(input_size=input_size).to(args.device)
    try:
        state = torch.load(base_path + ".pt", map_location=args.device, weights_only=True)
    except TypeError:
        state = torch.load(base_path + ".pt", map_location=args.device)
    model.load_state_dict(state)
    model.eval()

    probs = []
    with torch.no_grad():
        bs = 4096
        for start in range(0, len(X_seq), bs):
            xb = torch.from_numpy(X_seq[start:start+bs]).to(args.device)
            logits = model(xb)
            p = torch.sigmoid(logits).squeeze(1).detach().cpu().numpy()
            probs.append(p)
    probs = np.concatenate(probs, axis=0)

    if args.signal_ema and args.signal_ema > 1:
        probs = ema(probs, span=args.signal_ema)

    above = probs >= args.prob_threshold
    prev = np.concatenate([[False], above[:-1]])
    entries = np.logical_and(above, np.logical_not(prev)).astype(np.int32)

    deposit = float(args.start_deposit)
    trades = wins = losses = 0
    i = 0
    trades_rows: List[Dict] = []

    tcol = detect_time_col(prices)
    def _time_of(idx: int) -> Optional[str]:
        if tcol is None: return None
        return str(prices.loc[idx, tcol])

    ladder = Ladder(sl0=initial_sl_by_timeframe(timeframe), be1=0.0, be2=0.0, be3=0.0)

    while i < len(prices) - 1:
        if entries[i] == 1:
            side = target
            win, pnl_pct, exit_idx, tp_count = simulate_trade_path(prices, i, side, ladder)
            stake = deposit * args.stake_frac
            deposit += stake * (pnl_pct * args.leverage)
            trades += 1
            wins += int(win)
            losses += int(not win)

            if args.save_trades:
                trades_rows.append({
                    "model": os.path.basename(base_path) + ".pt",
                    "timeframe": timeframe,
                    "target": target,
                    "variant": spec.get("variant_id", ""),
                    "entry_index": i, "exit_index": exit_idx,
                    "entry_time": _time_of(i), "exit_time": _time_of(exit_idx),
                    "tp_hits": tp_count,
                    "pnl_pct_raw": round(pnl_pct, 6),
                    "pnl_pct_leveraged": round(pnl_pct * args.leverage, 6),
                    "stake_used": round(stake, 2),
                    "deposit_after": round(deposit, 2)
                })

            i = exit_idx + 1 + int(args.cooldown)  # cooldown
        else:
            i += 1

    winrate = (wins / trades) * 100.0 if trades > 0 else 0.0
    summary_row = {
        "model": os.path.basename(base_path) + ".pt",
        "timeframe": timeframe,
        "target": target,
        "variant": spec.get("variant_id", ""),
        "features": "|".join(features),
        "seq_len": seq_len,
        "prob_threshold": args.prob_threshold,
        "trades": trades, "wins": wins, "losses": losses,
        "winrate_%": round(winrate, 2),
        "final_deposit": round(deposit, 2),
        "status": "✅ ok"
    }
    return summary_row, trades_rows


# =======================
# Main
# =======================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="test/data/BTCUSDT",
                    help="Folder with BTCUSDT_<tf>_critical_indicators.csv")
    ap.add_argument("--models_dir", default="models/DQN",
                    help="Folder with DQN models (*.pt) and specs (*.features.json, *.scaler.npz)")
    ap.add_argument("--out_csv", default="backtest_results_dqn.csv")
    ap.add_argument("--prob_threshold", type=float, default=0.6)
    ap.add_argument("--start_deposit", type=float, default=100.0)
    ap.add_argument("--stake_frac", type=float, default=0.01, help="1% of deposit per trade")
    ap.add_argument("--leverage", type=float, default=20.0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--signal_ema", type=int, default=0, help="EMA span for probability smoothing (0=off)")
    ap.add_argument("--cooldown", type=int, default=0, help="Bars to wait after closing a trade before next entry")
    ap.add_argument("--save_trades", action="store_true", help="Save detailed trades to <out>_trades.csv")
    args = ap.parse_args()

    json_specs = glob.glob(os.path.join(args.models_dir, "*.features.json"))
    if not json_specs:
        print("No feature specs found in", args.models_dir)
        return

    summaries: List[Dict] = []
    all_trades: List[Dict] = []

    for js in json_specs:
        base = js[:-len(".features.json")]
        model_pt = base + ".pt"
        if not os.path.exists(model_pt):
            print(f"⚠️  weights missing for spec: {os.path.basename(js)} -> skipping")
            continue

        with open(js, "r", encoding="utf-8") as f:
            spec = json.load(f)
        timeframe = spec.get("timeframe", "1d")

        csv_path = os.path.join(args.data_dir, f"BTCUSDT_{timeframe}_critical_indicators.csv")
        if not os.path.exists(csv_path):
            print(f"⚠️  data not found for timeframe={timeframe}: {csv_path}")
            continue

        df = pd.read_csv(csv_path)

        summary_row, trades_rows = backtest_one_dqn(base, df, spec, args)
        print(f"{summary_row['model']}: {summary_row['status']} | trades={summary_row['trades']} "
              f"wins={summary_row['wins']} losses={summary_row['losses']} "
              f"winrate={summary_row['winrate_%']}% final={summary_row['final_deposit']}")
        summaries.append(summary_row)
        if args.save_trades:
            all_trades.extend(trades_rows)

    if summaries:
        out_df = pd.DataFrame(summaries)
        out_df.to_csv(args.out_csv, index=False)
        print("📊 Saved results to", args.out_csv)
        if args.save_trades and all_trades:
            trades_path = os.path.splitext(args.out_csv)[0] + "_trades.csv"
            pd.DataFrame(all_trades).to_csv(trades_path, index=False)
            print("🧾 Saved trades to", trades_path)
    else:
        print("No models were backtested.")

if __name__ == "__main__":
    main()
