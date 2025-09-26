import re
import gzip
import json
import glob
import argparse
from datetime import datetime

import numpy as np
import pandas as pd
from tqdm import tqdm
import torch

try:
    import joblib
    HAS_JOBLIB = True
except Exception:
    HAS_JOBLIB = False

# -----------------------------
# TP/SL по таймфреймах
# -----------------------------
TP_SL_BY_TF = {
    "15m": {"tp": 0.005, "sl": 0.02},   # +0.5% / -2.0%
    "30m": {"tp": 0.007, "sl": 0.02},   # +0.7% / -2.0%
    "1h":  {"tp": 0.010, "sl": 0.025},  # +1.0% / -2.5%
    "4h":  {"tp": 0.020, "sl": 0.030},  # +2.0% / -3.0%
    "1d":  {"tp": 0.030, "sl": 0.040},  # +3.0% / -4.0%
}

NON_FEATURE_COLS = {
    "timestamp","date","open","high","low","close","volume",
    "target","target_long","target_short","symbol","time","datetime","index"
}

# -----------------------------
# CLI
# -----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="CNN backtest with 1m exits (2024 range by default)")
    p.add_argument("--models_dir", type=str, default="Models/CNN")
    p.add_argument("--data_dir", type=str, default="test/data/BTCUSDT")
    p.add_argument("--one_min_file", type=str, default="BTCUSDT_1m.csv.gz")
    # за замовчуванням тільки 2024 рік
    p.add_argument("--start", type=str, default="2024-01-01")
    p.add_argument("--end", type=str, default="2024-12-31")
    p.add_argument("--out_dir", type=str, default="results/backtests")
    p.add_argument("--device", type=str, default="cpu")
    p.add_argument("--prob_threshold", type=float, default=0.5)
    # управління капіталом
    p.add_argument("--start_deposit", type=float, default=100.0, help="Початковий депозит ($)")
    p.add_argument("--risk_pct", type=float, default=0.01, help="Частка депозиту на угоду (0.01 = 1%)")
    return p.parse_args()

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def to_datetime(series):
    try:
        if np.issubdtype(series.dtype, np.number):
            if series.max() > 10_000_000_000:
                return pd.to_datetime(series, unit="ms", utc=True)
            else:
                return pd.to_datetime(series, unit="s", utc=True)
        else:
            return pd.to_datetime(series, utc=True)
    except Exception:
        return pd.to_datetime(series, utc=True, errors="coerce")

def infer_timeframe_from_filename(path: str) -> str:
    name = os.path.basename(path).lower()
    for tf in TP_SL_BY_TF.keys():
        if f"_{tf}_" in name or name.endswith(f"_{tf}.pth") or name.endswith(f"_{tf}.pt"):
            return tf
    m = re.search(r"(15m|30m|1h|4h|1d)", name)
    return m.group(1) if m else None

def infer_side_from_filename(path: str) -> str:
    name = os.path.basename(path).lower()
    if "long" in name: return "long"
    if "short" in name: return "short"
    return None

def list_cnn_models(models_dir: str):
    paths = sorted(list(glob.glob(os.path.join(models_dir, "*.pt"))) +
                   list(glob.glob(os.path.join(models_dir, "*.pth"))))
    items = []
    for p in paths:
        tf = infer_timeframe_from_filename(p)
        side = infer_side_from_filename(p)
        if tf in TP_SL_BY_TF and side in ("long","short"):
            items.append((p, tf, side))
    return items

def load_one_minute_df(data_dir: str, filename: str) -> pd.DataFrame:
    path = os.path.join(data_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"1m data not found: {path}")
    if path.endswith(".gz"):
        df = pd.read_csv(path, compression="gzip")
    else:
        df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns:
        ts_col = "time" if "time" in df.columns else ("date" if "date" in df.columns else None)
        if ts_col is None:
            raise ValueError("1m file must contain 'timestamp'/'time'/'date' column")
        df["timestamp"] = df[ts_col]
    df["timestamp"] = to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df

def get_feature_frame(ind_path: str, start=None, end=None) -> pd.DataFrame:
    df = pd.read_csv(ind_path)
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" not in df.columns:
        ts_col = "time" if "time" in df.columns else ("date" if "date" in df.columns else None)
        if ts_col is None:
            raise ValueError(f"{ind_path} must contain 'timestamp'/'time'/'date' column")
        df["timestamp"] = df[ts_col]
    df["timestamp"] = to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    if start: df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:   df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)

def build_X(df: pd.DataFrame, feature_list=None) -> pd.DataFrame:
    if feature_list:
        missing = [f for f in feature_list if f not in df.columns]
        if missing:
            raise ValueError(f"Missing features in data: {missing}")
        return df[feature_list].copy()
    numeric_cols = [c for c in df.columns
                    if c not in NON_FEATURE_COLS and pd.api.types.is_numeric_dtype(df[c])]
    return df[numeric_cols].copy()

def load_scaler_if_any(model_path: str):
    base = os.path.splitext(model_path)[0]
    for cand in [base + "_scaler.pkl", base + ".scaler.pkl", base + "_scaler.joblib"]:
        if os.path.exists(cand) and HAS_JOBLIB:
            try:
                return joblib.load(cand)
            except Exception:
                pass
    return None

def model_predict_on_bar(model, x_row: np.ndarray, device="cpu", prob_threshold=0.5):
    model.eval()
    with torch.no_grad():
        t = torch.tensor(x_row, dtype=torch.float32, device=device)
        if t.ndim == 1:
            t = t.unsqueeze(0)
        out = model(t)
        out_cpu = out.detach().cpu().numpy()
        if out_cpu.ndim == 2 and out_cpu.shape[1] == 1:
            proba = 1.0 / (1.0 + np.exp(-out_cpu[:,0]))
            return int(proba[0] >= prob_threshold)
        elif out_cpu.ndim == 2 and out_cpu.shape[1] == 2:
            pred = np.argmax(out_cpu, axis=1)
            return int(pred[0] == 1)
        else:
            val = out_cpu.ravel()[0]
            return int(val >= prob_threshold)

def simulate_exit_on_1m(entry_ts: pd.Timestamp,
                        entry_price: float,
                        side: str,
                        tp_pct: float,
                        sl_pct: float,
                        df_1m: pd.DataFrame) -> dict:
    if side not in ("long","short"):
        raise ValueError("side must be 'long' or 'short'")
    if side == "long":
        tp_price = entry_price * (1.0 + tp_pct)
        sl_price = entry_price * (1.0 - sl_pct)
    else:
        tp_price = entry_price * (1.0 - tp_pct)
        sl_price = entry_price * (1.0 + sl_pct)

    mask = df_1m["timestamp"] > entry_ts
    stream = df_1m.loc[mask, ["timestamp","open","high","low","close"]].itertuples(index=False)

    minutes = 0
    for t, o, h, l, c in stream:
        minutes += 1
        if side == "long":
            hit_tp = h >= tp_price
            hit_sl = l <= sl_price
            if hit_tp and hit_sl:
                exit_price, reason = sl_price, "SL_both_hit"
            elif hit_tp:
                exit_price, reason = tp_price, "TP"
            elif hit_sl:
                exit_price, reason = sl_price, "SL"
            else:
                continue
            pnl_pct = (exit_price / entry_price - 1.0) * 100.0
        else:
            hit_tp = l <= tp_price
            hit_sl = h >= sl_price
            if hit_tp and hit_sl:
                exit_price, reason = sl_price, "SL_both_hit"
            elif hit_tp:
                exit_price, reason = tp_price, "TP"
            elif hit_sl:
                exit_price, reason = sl_price, "SL"
            else:
                continue
            pnl_pct = (entry_price / exit_price - 1.0) * 100.0

        return dict(exit_ts=t, exit_price=exit_price, exit_reason=reason,
                    pnl_pct=pnl_pct, minutes_held=minutes)

    # форс-закриття останнім доступним close після входу (якщо нічого не вдарило)
    last = df_1m[df_1m["timestamp"] > entry_ts].tail(1)
    if last.empty:
        return dict(exit_ts=None, exit_price=entry_price, exit_reason="NO_DATA",
                    pnl_pct=0.0, minutes_held=0)
    last_close = float(last["close"].values[-1])
    pnl_pct = ((last_close / entry_price - 1.0) if side == "long"
               else (entry_price / last_close - 1.0)) * 100.0
    return dict(exit_ts=last["timestamp"].values[-1], exit_price=last_close,
                exit_reason="FORCED_CLOSE", pnl_pct=pnl_pct, minutes_held=minutes)

def equity_after_trades(trades_df: pd.DataFrame,
                        start_deposit: float,
                        leverage: float,
                        risk_pct: float) -> float:
    """
    Модель компаунду:
      зміна депозиту на кожній угоді = deposit * risk_pct * leverage * (pnl_pct/100)
      deposit += change
    """
    deposit = float(start_deposit)
    if trades_df.empty:
        return deposit
    # гарантуємо хронологію
    trades_df = trades_df.sort_values("entry_ts").reset_index(drop=True)
    for _, row in trades_df.iterrows():
        pnl_frac = float(row["pnl_pct"]) / 100.0
        delta = deposit * risk_pct * leverage * pnl_frac
        deposit = max(0.0, deposit + delta)
    return round(deposit, 2)

def backtest_model(model_path: str,
                   timeframe: str,
                   side: str,
                   data_dir: str,
                   one_min_file: str,
                   start: str,
                   end: str,
                   device: str,
                   prob_threshold: float) -> tuple[pd.DataFrame, dict]:
    ind_file = os.path.join(data_dir, f"BTCUSDT_{timeframe}_critical_indicators.csv")
    if not os.path.exists(ind_file):
        raise FileNotFoundError(f"Indicators not found for {timeframe}: {ind_file}")
    df_tf = get_feature_frame(ind_file, start, end)
    df_1m = load_one_minute_df(data_dir, one_min_file)

    device_t = torch.device(device)
    model = torch.load(model_path, map_location=device_t)
    model.to(device_t).eval()

    scaler = load_scaler_if_any(model_path)
    feature_list = None
    meta_path = os.path.splitext(model_path)[0] + "_meta.json"
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, "r", encoding="utf-8"))
            if isinstance(meta, dict) and "feature_names" in meta:
                feature_list = [f.lower() for f in meta["feature_names"]]
        except Exception:
            pass

    X = build_X(df_tf, feature_list=feature_list)
    if scaler is not None:
        X_scaled = scaler.transform(X.values)
    else:
        mu = X.mean(axis=0).values
        sigma = X.std(axis=0).replace(0, 1e-8).values
        X_scaled = (X.values - mu) / sigma

    if "close" not in df_tf.columns:
        raise ValueError(f"{ind_file} must contain 'close' column")
    closes = df_tf["close"].astype(float).values
    tss = df_tf["timestamp"].values

    tp = TP_SL_BY_TF[timeframe]["tp"]
    sl = TP_SL_BY_TF[timeframe]["sl"]

    trades = []
    in_position = False

    for i in tqdm(range(len(df_tf)), desc=f"Backtest {os.path.basename(model_path)}"):
        if in_position:
            continue
        x_row = X_scaled[i]
        signal = model_predict_on_bar(model, x_row, device=device, prob_threshold=prob_threshold)
        if signal != 1:
            continue

        entry_ts = (pd.Timestamp(tss[i]).tz_convert("UTC")
                    if pd.api.types.is_datetime64tz_dtype(tss)
                    else pd.to_datetime(tss[i], utc=True))
        entry_price = float(closes[i])

        res = simulate_exit_on_1m(entry_ts, entry_price, side, tp, sl, df_1m)
        trades.append({
            "model": os.path.basename(model_path),
            "timeframe": timeframe,
            "side": side,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "exit_ts": res["exit_ts"],
            "exit_price": res["exit_price"],
            "exit_reason": res["exit_reason"],
            "minutes_held": res["minutes_held"],
            "pnl_pct": res["pnl_pct"]
        })
        in_position = False

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        stats = {
            "model": os.path.basename(model_path),
            "timeframe": timeframe,
            "side": side,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate_pct": 0.0,
            "avg_pnl_pct": 0.0,
            "sum_pnl_pct": 0.0
        }
        return trades_df, stats

    wins = int((trades_df["pnl_pct"] > 0).sum())
    losses = int((trades_df["pnl_pct"] <= 0).sum())
    stats = {
        "model": os.path.basename(model_path),
        "timeframe": timeframe,
        "side": side,
        "trades": int(len(trades_df)),
        "wins": wins,
        "losses": losses,
        "winrate_pct": round(100.0 * wins / max(len(trades_df),1), 2),
        "avg_pnl_pct": round(float(trades_df["pnl_pct"].mean()), 4),
        "sum_pnl_pct": round(float(trades_df["pnl_pct"].sum()), 4)
    }
    return trades_df, stats

# -----------------------------
# main
# -----------------------------
def main():
    args = parse_args()
    ensure_dir(args.out_dir)

    models = list_cnn_models(args.models_dir)
    if not models:
        print(f"Моделі не знайдені у {args.models_dir}. Очікуються *.pt/*.pth з _<tf>_ та _long|short_ у назві.")
        return

    all_trades = []
    all_stats = []

    for model_path, tf, side in models:
        try:
            trades_df, stats = backtest_model(
                model_path=model_path,
                timeframe=tf,
                side=side,
                data_dir=args.data_dir,
                one_min_file=args.one_min_file,
                start=args.start,
                end=args.end,
                device=args.device,
                prob_threshold=args.prob_threshold
            )
            # підрахунок фінал. еквіті (по кожній моделі окремо)
            if not trades_df.empty:
                eq20 = equity_after_trades(trades_df, args.start_deposit, leverage=20.0, risk_pct=args.risk_pct)
                eq50 = equity_after_trades(trades_df, args.start_deposit, leverage=50.0, risk_pct=args.risk_pct)
                stats["final_equity_20x"] = eq20
                stats["final_equity_50x"] = eq50
                all_trades.append(trades_df)
            else:
                stats["final_equity_20x"] = args.start_deposit
                stats["final_equity_50x"] = args.start_deposit

            all_stats.append(stats)
        except Exception as e:
            all_stats.append({
                "model": os.path.basename(model_path),
                "timeframe": tf,
                "side": side,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "winrate_pct": 0.0,
                "avg_pnl_pct": 0.0,
                "sum_pnl_pct": 0.0,
                "final_equity_20x": args.start_deposit,
                "final_equity_50x": args.start_deposit,
                "error": str(e)
            })

    ts_tag = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stats_df = pd.DataFrame(all_stats)
    out_stats = os.path.join(args.out_dir, f"CNN_backtest_stats_{ts_tag}.csv")
    stats_df.to_csv(out_stats, index=False)

    if all_trades:
        trades_df = pd.concat(all_trades, ignore_index=True)
        out_trades = os.path.join(args.out_dir, f"CNN_backtest_trades_{ts_tag}.csv")
        trades_df.to_csv(out_trades, index=False)
        print(f"✅ Trades saved: {out_trades}")
    else:
        print("⚠️ Trades dataframe is empty.")

    print(f"✅ Stats saved:  {out_stats}")
    print("\nПояснення по еквіті:")
    print(f"- start_deposit=${args.start_deposit:.2f}, risk_pct={args.risk_pct*100:.2f}% на угоду")
    print("- Формула: deposit += deposit * risk_pct * leverage * (pnl_pct/100) (компаундинг)")

if __name__ == "__main__":
    main()
