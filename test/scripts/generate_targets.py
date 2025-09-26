import os
import math
import numpy as np
import pandas as pd
from tqdm import tqdm

# -----------------------------
# ATR
# -----------------------------
def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Classic Wilder's ATR (RMA). Не використовує майбутню інформацію.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    # RMA: EMA з alpha=1/period
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    return atr


def atr_sma(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(window=period, min_periods=period).mean()


# -----------------------------
# Targets (first-touch)
# -----------------------------
def add_targets_first_touch(
    df: pd.DataFrame,
    atr_period: int = 14,
    atr_kind: str = "wilder",         # "wilder" | "sma"
    atr_multiplier_tp: float = 0.5,
    atr_multiplier_sl: float = 1.0,
    lookahead: int = 10,
    fee_buffer: float = 0.0           # наприклад 0.0005 (~5 б.п.) щоб врахувати комісії/спред
) -> pd.DataFrame:
    """
    Маркує таргети за правилом 'хто перший торкнеться' протягом наступних `lookahead` свічок.
    Додає:
      - target_long, target_short (бінарні)
      - profit_pct_first_touch: знак і величина руху до першого дотику (TP позитивний, SL негативний)
      - ATR_<kind> (для діагностики)
    """

    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    # --- ATR ---
    if atr_kind == "wilder":
        atr = atr_wilder(df, period=atr_period)
    elif atr_kind == "sma":
        atr = atr_sma(df, period=atr_period)
    else:
        raise ValueError("atr_kind must be 'wilder' or 'sma'")

    df[f"atr_{atr_kind}"] = atr

    # --- targets init ---
    df["target_long"] = 0
    df["target_short"] = 0
    df["profit_pct_first_touch"] = np.nan  # +% якщо першим TP, -% якщо першим SL

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    atr_vals = df[f"atr_{atr_kind}"].values

    n = len(df)
    for i in tqdm(range(n - 1 - lookahead + 1), desc="Labeling first-touch"):
        atr_i = atr_vals[i]
        if not np.isfinite(atr_i):
            continue

        entry = close[i]

        tp_long = entry + (atr_i * atr_multiplier_tp) * (1.0 + fee_buffer)
        sl_long = entry - (atr_i * atr_multiplier_sl) * (1.0 + fee_buffer)

        tp_short = entry - (atr_i * atr_multiplier_tp) * (1.0 + fee_buffer)
        sl_short = entry + (atr_i * atr_multiplier_sl) * (1.0 + fee_buffer)

        # вікно майбутнього
        start = i + 1
        end = i + lookahead + 1
        hi_seg = high[start:end]
        lo_seg = low[start:end]

        # індекси перших дотиків (None, якщо не трапилось)
        # LONG
        idx_hit_tp_long = np.argmax(hi_seg >= tp_long) if (hi_seg >= tp_long).any() else None
        idx_hit_sl_long = np.argmax(lo_seg <= sl_long) if (lo_seg <= sl_long).any() else None
        # SHORT
        idx_hit_tp_short = np.argmax(lo_seg <= tp_short) if (lo_seg <= tp_short).any() else None
        idx_hit_sl_short = np.argmax(hi_seg >= sl_short) if (hi_seg >= sl_short).any() else None

        # First-touch логіка:
        # LONG: якщо TP трапився і (SL не трапився або TP раніше SL) → 1
        long_win = False
        if idx_hit_tp_long is not None:
            if idx_hit_sl_long is None or idx_hit_tp_long <= idx_hit_sl_long:
                long_win = True

        # SHORT: якщо TP трапився і (SL не трапився або TP раніше SL) → 1
        short_win = False
        if idx_hit_tp_short is not None:
            if idx_hit_sl_short is None or idx_hit_tp_short <= idx_hit_sl_short:
                short_win = True

        # Встановлюємо бінарні мітки
        if long_win:
            df.at[i, "target_long"] = 1
        if short_win:
            df.at[i, "target_short"] = 1

        # Контінуальна винагорода за перший дотик (корисно для RL)
        # беремо той сценарій, який настав найпершим серед чотирьох можливих дотиків
        candidates = []
        if idx_hit_tp_long is not None:  candidates.append(("tp_long",  idx_hit_tp_long))
        if idx_hit_sl_long is not None:  candidates.append(("sl_long",  idx_hit_sl_long))
        if idx_hit_tp_short is not None: candidates.append(("tp_short", idx_hit_tp_short))
        if idx_hit_sl_short is not None: candidates.append(("sl_short", idx_hit_sl_short))

        if candidates:
            first_label, first_idx = min(candidates, key=lambda t: t[1])
            # приблизний рух в % до першого дотику (від close i):
            if first_label == "tp_long":
                move = (tp_long / entry) - 1.0
            elif first_label == "sl_long":
                move = (sl_long / entry) - 1.0
            elif first_label == "tp_short":
                move = (entry / tp_short) - 1.0   # еквівалентно -(tp_short/entry - 1)
            else:  # sl_short
                move = -(sl_short / entry - 1.0)
            df.at[i, "profit_pct_first_touch"] = move

    return df


# -----------------------------
# I/O
# -----------------------------
def process_file(
    file_path: str,
    output_dir: str,
    atr_period: int = 14,
    atr_kind: str = "wilder",
    atr_multiplier_tp: float = 0.5,
    atr_multiplier_sl: float = 1.0,
    lookahead: int = 10,
    fee_buffer: float = 0.0,
):
    df = pd.read_csv(file_path)
    df.columns = [c.strip().lower() for c in df.columns]

    df = add_targets_first_touch(
        df,
        atr_period=atr_period,
        atr_kind=atr_kind,
        atr_multiplier_tp=atr_multiplier_tp,
        atr_multiplier_sl=atr_multiplier_sl,
        lookahead=lookahead,
        fee_buffer=fee_buffer,
    )

    base = os.path.splitext(os.path.basename(file_path))[0]

    # Комбінований файл
    out_all = os.path.join(output_dir, f"{base}_with_targets_all.csv")
    df.to_csv(out_all, index=False)

    # Окремі файли під існуючі пайплайни
    out_long = os.path.join(output_dir, f"{base}_with_targets_long.csv")
    out_short = os.path.join(output_dir, f"{base}_with_targets_short.csv")

    cols_common = [c for c in df.columns if c not in ["target_long", "target_short"]]
    df[cols_common + ["target_long"]].to_csv(out_long, index=False)
    df[cols_common + ["target_short"]].to_csv(out_short, index=False)

    print(f"✅ Saved: {out_all}")
    print(f"✅ Saved: {out_long}")
    print(f"✅ Saved: {out_short}")


def main():
    input_dir = "test/data/ETHUSDT"
    output_dir = "test/data/ETHUSDT"

    # Налаштування за замовчуванням — можеш змінити під таймфрейм
    ATR_PERIOD = 14
    ATR_KIND = "wilder"     # "wilder" | "sma"
    TPx = 0.5               # ATR * 0.5
    SLx = 1.0               # ATR * 1.0
    LOOKAHEAD = 10          # наступні 10 свічок
    FEE = 0.0005            # ~5 б.п. буферу (опційно 0.0)

    for file in os.listdir(input_dir):
        if not file.endswith("_indicators.csv"):
            continue
        path = os.path.join(input_dir, file)
        print(f"📂 Processing: {file}")
        try:
            process_file(
                path, output_dir,
                atr_period=ATR_PERIOD,
                atr_kind=ATR_KIND,
                atr_multiplier_tp=TPx,
                atr_multiplier_sl=SLx,
                lookahead=LOOKAHEAD,
                fee_buffer=FEE,
            )
        except Exception as e:
            print(f"❌ Error: {file}: {e}")


if __name__ == "__main__":
    main()
