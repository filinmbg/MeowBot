import pandas as pd

df = pd.read_csv("test-manual/results/manual_backtest_longbreakout_long_breakout_base_trades.csv")

# тільки виконані трейди
df = df.copy()

# win / loss
df["result"] = df["is_win"].map({1: "win", 0: "loss"})

# =========================
# RSI аналіз
# =========================

df["rsi_bucket"] = (df["ind_rsi_14"] // 5) * 5

rsi_stats = df.groupby("rsi_bucket").agg(
    trades=("result", "count"),
    wins=("is_win", "sum"),
    winrate=("is_win", "mean"),
    avg_pnl=("pnl_usd", "mean")
).reset_index()

print("\nRSI ANALYSIS")
print(rsi_stats.sort_values("rsi_bucket"))

df["atr_bucket"] = (df["ind_atr_14_pct"] * 10 // 1) / 10

atr_stats = df.groupby("atr_bucket").agg(
    trades=("result", "count"),
    winrate=("is_win", "mean"),
    avg_pnl=("pnl_usd", "mean")
).reset_index()

print("\nATR ANALYSIS")
print(atr_stats.sort_values("atr_bucket"))


df["ema_bucket"] = (df["ind_dist_to_ema_50_pct"] // 0.5) * 0.5

ema_stats = df.groupby("ema_bucket").agg(
    trades=("result", "count"),
    winrate=("is_win", "mean"),
    avg_pnl=("pnl_usd", "mean")
).reset_index()

print("\nEMA DIST ANALYSIS")
print(ema_stats.sort_values("ema_bucket"))

df["vol_bucket"] = (df["ind_volume_ratio_sma_20"] // 0.5) * 0.5

vol_stats = df.groupby("vol_bucket").agg(
    trades=("result", "count"),
    winrate=("is_win", "mean"),
    avg_pnl=("pnl_usd", "mean")
).reset_index()

print("\nVOLUME ANALYSIS")
print(vol_stats.sort_values("vol_bucket"))


group_stats = df.groupby(["symbol", "timeframe"]).agg(
    trades=("result", "count"),
    winrate=("is_win", "mean"),
    pnl=("pnl_usd", "sum")
).reset_index()

print("\nWORST GROUPS")
print(group_stats.sort_values("pnl").head(10))


print("\nBEST GROUPS")
print(group_stats.sort_values("pnl", ascending=False).head(10))