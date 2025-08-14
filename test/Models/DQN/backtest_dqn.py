# test/Models/DQN/backtest_dqn.py
import os
import json
import argparse
from pathlib import Path
from typing import Dict, Optional, Tuple

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, f1_score
from tqdm import tqdm

# ====== Project configs (мають бути в твоєму проєкті) ======
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS


# ====== DQN policy (MLP): Q(s) -> [Q_flat, Q_long] ======
class DQNPolicy(nn.Module):
    def __init__(self, input_size: int, hidden: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, 2)  # 2 дії: 0=flat, 1=long
        )

    def forward(self, x):
        # x: (B, F)
        return self.net(x)  # (B, 2)


# ====== Утиліти (узяті з нашого CNN-бектесту і узгоджені) ======
def load_scaler_npz_safe(npz_path: Path):
    """
    Повертає (scaler_obj, warn_note|None, is_identity: bool).
    Якщо файл відсутній/порожній/зіпсований — identity-скейлер (без масштабування).
    """
    class IdentityScaler:
        def transform(self, X):
            return X

    try:
        if (not npz_path.exists()) or (npz_path.stat().st_size == 0):
            return IdentityScaler(), f"warn: scaler missing/empty: {npz_path.name} -> using identity", True
        d = np.load(npz_path)
        if "mean_" not in d or "scale_" not in d:
            return IdentityScaler(), f"warn: scaler keys missing in {npz_path.name} -> using identity", True

        mean_ = d["mean_"].astype(np.float32)
        scale_ = d["scale_"].astype(np.float32)

        class _Scaler:
            def __init__(self, mean_, scale_):
                self.mean_ = mean_
                self.scale_ = scale_
            def transform(self, X):
                return (X - self.mean_) / np.where(self.scale_ == 0, 1.0, self.scale_)
        return _Scaler(mean_, scale_), None, False
    except Exception as e:
        return IdentityScaler(), f"warn: scaler load error ({type(e).__name__}: {e}) in {npz_path.name} -> using identity", True


def find_close_col(df: pd.DataFrame) -> str:
    for c in ["close", "Close", "close_price", "price_close", "closeUSD"]:
        if c in df.columns:
            return c
    raise KeyError("Could not find a 'close' column in dataframe")


def find_high_low_cols(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    high_candidates = ["high", "High", "high_price"]
    low_candidates  = ["low", "Low", "low_price"]
    high = next((c for c in high_candidates if c in df.columns), None)
    low  = next((c for c in low_candidates  if c in df.columns), None)
    return high, low


def find_datetime_col(df: pd.DataFrame) -> Optional[str]:
    candidates = ["open_time", "close_time", "timestamp", "time", "date", "datetime", "ts"]
    for c in candidates:
        if c in df.columns:
            return c
    if isinstance(df.index, pd.DatetimeIndex):
        return "__index__"
    return None


def ensure_datetime(df: pd.DataFrame, col: str) -> pd.Series:
    if col == "__index__":
        dtidx = pd.to_datetime(df.index, errors="coerce", utc=True)
        try:
            dtidx = dtidx.tz_convert(None)
        except Exception:
            pass
        return pd.Series(dtidx.values, index=df.index)

    s = df[col]
    if pd.api.types.is_integer_dtype(s) or pd.api.types.is_float_dtype(s):
        dt = pd.to_datetime(s, errors="coerce", unit="ms", utc=True)
        if dt.isna().mean() > 0.9:
            dt = pd.to_datetime(s, errors="coerce", unit="s", utc=True)
    else:
        dt = pd.to_datetime(s, errors="coerce", utc=True)
    if hasattr(dt, "dt"):
        try:
            dt = dt.dt.tz_convert(None)
        except Exception:
            pass
    if dt.dtype == "object":
        dt = pd.to_datetime(dt, errors="coerce")
    return dt


def filter_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    dt_col = find_datetime_col(df)
    if dt_col is None:
        raise KeyError("No datetime-like column found (expected time/date/timestamp or DatetimeIndex)")
    dt = ensure_datetime(df, dt_col)
    if (dt.isna().mean() == 1.0):
        raise TypeError(f"Datetime parsing failed for '{dt_col}' — all NaT.")
    mask = (dt.dt.year == year)
    return df.loc[mask.fillna(False)].reset_index(drop=True)


def compute_metrics(equity: np.ndarray, daily_like: bool = False) -> dict:
    ret = (equity[1:] / equity[:-1]) - 1.0
    total_return = equity[-1] / equity[0] - 1.0
    if len(ret) > 1 and np.nanstd(ret) > 0:
        ann = 252 if daily_like else len(ret)
        sharpe = np.nanmean(ret) / np.nanstd(ret) * np.sqrt(ann)
    else:
        sharpe = np.nan
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    maxdd = float(np.nanmin(dd)) if len(dd) else np.nan
    years = len(ret) / (252 if daily_like else len(ret))
    cagr = (equity[-1] / equity[0]) ** (1.0 / max(years, 1e-9)) - 1.0 if equity[-1] > 0 else np.nan
    return {"total_return": float(total_return), "sharpe": float(sharpe), "max_drawdown": float(maxdd), "cagr": float(cagr)}


# ====== Бектест LONG-only з TP/SL (такий самий, як у CNN) ======
def backtest_long_only_tp_sl(
    close: np.ndarray,
    signal: np.ndarray,
    fee_bps: float,
    tp_pct: float,
    sl_pct: float,
    high: np.ndarray = None,
    low: np.ndarray = None
):
    N = len(close)
    equity = np.ones(N, dtype=float)
    if N < 3:
        return {"equity": equity, "trades": 0, "win_rate": np.nan}

    p = np.zeros(N, dtype=int)
    p[1:] = signal[:-1].astype(int)

    trades = 0
    prev_p = 0
    pnl_segments = []
    in_pos = False
    seg_start_val = 1.0

    for t in range(1, N):
        if p[t] != prev_p:
            trades += 1
            equity[t-1] *= (1.0 - fee_bps / 10000.0)
            if in_pos:
                pnl_segments.append(equity[t-1] - seg_start_val)
            in_pos = (p[t] == 1)
            seg_start_val = equity[t-1]

        if p[t] == 1:
            entry_price = close[t-1]
            exit_factor = None
            if high is not None and low is not None:
                sl_level = entry_price * (1.0 - sl_pct)
                tp_level = entry_price * (1.0 + tp_pct)
                hit_sl = low[t] <= sl_level
                hit_tp = high[t] >= tp_level
                if hit_sl and not hit_tp:
                    exit_factor = sl_level / entry_price
                elif hit_tp and not hit_sl:
                    exit_factor = tp_level / entry_price
                elif hit_sl and hit_tp:
                    exit_factor = sl_level / entry_price  # консервативно
            if exit_factor is None:
                exit_factor = close[t] / close[t-1]
            equity[t] = equity[t-1] * exit_factor
        else:
            equity[t] = equity[t-1]
        prev_p = p[t]

    if p[-1] == 1:
        equity[-1] *= (1.0 - fee_bps / 10000.0)
        pnl_segments.append(equity[-1] - seg_start_val)

    if len(pnl_segments) > 0:
        wins = sum(1 for x in pnl_segments if x > 0)
        win_rate = wins / len(pnl_segments)
    else:
        win_rate = np.nan

    return {"equity": equity, "trades": trades, "win_rate": float(win_rate)}


# ====== TP/SL за замовч. по таймфреймах ======
DEFAULT_TP = {"15m": 0.004, "30m": 0.006, "1h": 0.008, "4h": 0.012}
DEFAULT_SL = {"15m": 0.003, "30m": 0.004, "1h": 0.006, "4h": 0.009}


def parse_map(s: str) -> Dict[str, float]:
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        tf, val = part.split(":")
        out[tf.strip()] = float(val.strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    # дефолти під запуск "python test/Models/DQN/backtest_dqn.py" з кореня
    ap.add_argument("--data-root", type=str, default="test/data/BTCUSDT")
    ap.add_argument("--models-root", type=str, default="models/DQN")
    ap.add_argument("--out", type=str, default="backtests/DQN")
    ap.add_argument("--fee_bps", type=float, default=5.0, help="Per-side fee in bps (5 = 0.05%)")
    ap.add_argument("--tp", type=str, default="", help='Override TP map, e.g. "15m:0.005,30m:0.007"')
    ap.add_argument("--sl", type=str, default="", help='Override SL map, e.g. "15m:0.004,30m:0.006"')
    ap.add_argument("--year", type=int, default=2024, help="Filter data by calendar year (default: 2024)")
    ap.add_argument(
        "--strict-scalers",
        action="store_true",
        help="Skip models if scaler.npz is missing/corrupted (identity scaler)."
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # TP/SL мапи
    tp_map = DEFAULT_TP.copy()
    sl_map = DEFAULT_SL.copy()
    if args.tp:
        tp_map.update(parse_map(args.tp))
    if args.sl:
        sl_map.update(parse_map(args.sl))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # переліки комбінацій як у CNN
    combos = []
    for timeframe in TIMEFRAMES:
        for target in TARGETS:
            for variant_id in VARIANT_FEATURE_SETS.keys():
                combos.append((timeframe, target, variant_id))
    total = len(combos)

    summaries = []
    with tqdm(total=total, desc="🔎 Backtesting DQN models", ncols=100) as pbar:
        for idx, (timeframe, target, variant_id) in enumerate(combos, start=1):
            model_name = f"DQN_{timeframe}_{target}_V{variant_id}"
            tp_pct = float(tp_map.get(timeframe, list(tp_map.values())[0]))
            sl_pct = float(sl_map.get(timeframe, list(sl_map.values())[0]))

            model_file = Path(args.models_root) / f"{model_name}.pt"
            spec_file  = Path(args.models_root) / f"{model_name}.features.json"
            scaler_npz = Path(args.models_root) / f"{model_name}.scaler.npz"
            csv_path   = Path(args.data_root) / f"BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv"

            percent = (idx / total) * 100.0
            tqdm.write(f"[{idx}/{total}] ({percent:5.1f}%) Тестую: {model_name}")

            if not (model_file.exists() and spec_file.exists() and scaler_npz.exists() and csv_path.exists()):
                summaries.append({"model": model_name, "status": "skip: missing file(s)"})
                pbar.update(1)
                continue

            # Дані + фільтр року
            try:
                df = pd.read_csv(csv_path)
                df = filter_year(df, args.year)
                if len(df) < 3:
                    summaries.append({"model": model_name, "status": f"skip: no data for {args.year}"})
                    pbar.update(1)
                    continue
            except Exception as e:
                summaries.append({"model": model_name, "status": f"skip: {type(e).__name__}: {e}"})
                pbar.update(1)
                continue

            # Фічі (json -> fallback)
            feats = None
            feat_note = None
            try:
                if spec_file.exists() and spec_file.stat().st_size > 0:
                    with open(spec_file, "r", encoding="utf-8") as f:
                        spec = json.load(f)
                    if isinstance(spec, dict) and "features" in spec and isinstance(spec["features"], list):
                        feats = spec["features"]
                    else:
                        feat_note = "warn: invalid spec structure; using variant fallback"
                else:
                    feat_note = "warn: empty or missing spec; using variant fallback"
            except Exception as e:
                feat_note = f"warn: spec json error ({type(e).__name__}: {e}); using variant fallback"
            if not feats or len(feats) == 0:
                features_from_variant = VARIANT_FEATURE_SETS.get(variant_id, [])
                feats = list(features_from_variant) if features_from_variant else None
                tqdm.write(f"   ↳ ⚠️  {model_name}: {feat_note or 'using variant fallback'}")
            if not feats or not set(feats).issubset(df.columns):
                missing = [] if not feats else sorted(list(set(feats) - set(df.columns)))
                miss_str = ",".join(missing[:5]) + ("..." if len(missing) > 5 else "")
                summaries.append({
                    "model": model_name,
                    "status": f"skip: feature mismatch ({feat_note or 'no spec'}) missing=[{miss_str}]"
                })
                pbar.update(1)
                continue

            # Скейлер (identity-фолбек + strict)
            scaler, scaler_note, is_identity = load_scaler_npz_safe(scaler_npz)
            if scaler_note and not (args.strict_scalers and is_identity):
                tqdm.write(f"   ↳ ⚠️  {model_name}: {scaler_note}")
            if args.strict_scalers and is_identity:
                summaries.append({"model": model_name, "status": "skip: identity scaler (strict mode)"})
                pbar.update(1)
                continue

            # Модель DQN (MLP)
            m = DQNPolicy(input_size=len(feats)).to(device)
            try:
                try:
                    state = torch.load(model_file, map_location=device, weights_only=True)
                except TypeError:
                    state = torch.load(model_file, map_location=device)
                m.load_state_dict(state)
            except Exception as e:
                summaries.append({"model": model_name, "status": f"skip: model load error ({type(e).__name__}: {e})"})
                pbar.update(1)
                continue
            m.eval()

            # Підготовка X
            X = df[feats].values.astype(np.float32)
            Xs = scaler.transform(X).astype(np.float32)

            with torch.no_grad():
                batch = torch.tensor(Xs, dtype=torch.float32, device=device)  # (N, F)
                qvals = m(batch).cpu().numpy()  # (N, 2)
                actions = np.argmax(qvals, axis=1).astype(np.int32)  # 0/1
                signal = (actions == 1).astype(np.float32)

            close_col = find_close_col(df)
            close = df[close_col].astype(float).values
            high_col, low_col = find_high_low_cols(df)
            high = df[high_col].astype(float).values if high_col else None
            low  = df[low_col].astype(float).values if low_col else None

            bt = backtest_long_only_tp_sl(
                close=close,
                signal=signal,
                fee_bps=args.fee_bps,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                high=high,
                low=low
            )

            equity = bt["equity"]
            metrics = compute_metrics(equity, daily_like=False)

            # Крива капіталу
            curve_path = out_dir / f"{model_name}_equity_{args.year}.csv"
            pd.DataFrame({"equity": equity}).to_csv(curve_path, index=False)

            # Діагностичні класифікаційні метрики (якщо є target-колонка)
            y_col = f"target_{target}" if f"target_{target}" in df.columns else ("target" if "target" in df.columns else None)
            acc = f1 = np.nan
            if y_col is not None:
                y = df[y_col].values.astype(np.float32)
                pred_cls = (actions == 1).astype(int)
                try:
                    acc = accuracy_score(y, pred_cls)
                    f1  = f1_score(y, pred_cls, zero_division=0)
                except Exception:
                    pass

            status_final = "ok"
            notes = []
            if feat_note:
                notes.append("fallback_features_from_variant")
            if scaler_note and not args.strict_scalers:
                notes.append("identity_scaler_used")
            if notes:
                status_final += " (" + ",".join(notes) + ")"

            summaries.append({
                "model": model_name,
                "status": status_final,
                "timeframe": timeframe,
                "year": args.year,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "trades": bt["trades"],
                "win_rate": bt["win_rate"],
                "total_return": metrics["total_return"],
                "sharpe": metrics["sharpe"],
                "max_drawdown": metrics["max_drawdown"],
                "cagr": metrics["cagr"],
                "cls_acc": acc,
                "cls_f1": f1,
                "equity_path": str(curve_path)
            })

            pbar.update(1)

    out_csv = Path(args.out) / f"dqn_backtest_summary_{args.year}.csv"
    pd.DataFrame(summaries).to_csv(out_csv, index=False)
    print(f"✅ Saved summary: {out_csv}")


if __name__ == "__main__":
    main()
