from pathlib import Path
import pandas as pd


DATA_DIR = Path(r"D:\Project\MeowBot\test\data\BTCUSDT")
RESULT_DIR = Path(r"D:\Project\MeowBot\test\results")

TRADES_FILE = RESULT_DIR / "BTCUSDT_15m_strategy_trades_fast.csv"
IND_FILE = DATA_DIR / "BTCUSDT_15m_indicators.csv.gz"


def load_data() -> pd.DataFrame:
    trades = pd.read_csv(TRADES_FILE)
    indicators = pd.read_csv(IND_FILE, compression="gzip")

    trades["entry_time"] = trades["entry_time"].astype("int64")
    indicators["close_time"] = indicators["close_time"].astype("int64")

    merged = trades.merge(
        indicators,
        left_on="entry_time",
        right_on="close_time",
        how="left",
    )

    return merged


def compute_bucket_stats(df: pd.DataFrame, column: str, buckets: list[float]) -> pd.DataFrame:
    work = df.copy()

    # залишаємо тільки рядки, де індикатор і pnl існують
    work = work.dropna(subset=[column, "realized_pnl_usd"]).copy()

    work["bucket"] = pd.cut(work[column], buckets, include_lowest=True)

    stats = (
        work.groupby("bucket", observed=False)
        .agg(
            trades=("realized_pnl_usd", "count"),
            wins=("realized_pnl_usd", lambda x: (x > 0).sum()),
            losses=("realized_pnl_usd", lambda x: (x <= 0).sum()),
            avg_pnl_usd=("realized_pnl_usd", "mean"),
            total_pnl_usd=("realized_pnl_usd", "sum"),
            avg_fee_usd=("fees_usd", "mean"),
            total_fee_usd=("fees_usd", "sum"),
        )
        .reset_index()
    )

    stats["winrate_pct"] = (stats["wins"] / stats["trades"] * 100).fillna(0.0)
    stats["bucket"] = stats["bucket"].astype(str)

    return stats


def analyze_indicator(df: pd.DataFrame, column: str, buckets: list[float], name: str) -> None:
    print(f"\nAnalyzing {column}", flush=True)

    stats = compute_bucket_stats(df, column, buckets)

    out = RESULT_DIR / f"analysis_{name}.csv"
    stats.to_csv(out, index=False)

    print(f"Saved {out}", flush=True)
    print(stats.to_string(index=False), flush=True)


def main() -> None:
    df = load_data()

    long_df = df[df["side"] == "LONG"].copy()
    short_df = df[df["side"] == "SHORT"].copy()

    # ---------------- RSI ----------------
    rsi_bins = [0, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 100]

    analyze_indicator(long_df, "rsi14", rsi_bins, "rsi14_long")
    analyze_indicator(short_df, "rsi14", rsi_bins, "rsi14_short")

    analyze_indicator(long_df, "rsi7", rsi_bins, "rsi7_long")
    analyze_indicator(short_df, "rsi7", rsi_bins, "rsi7_short")

    # ---------------- ADX ----------------
    adx_bins = [0, 10, 15, 18, 20, 22, 25, 30, 40, 100]

    analyze_indicator(long_df, "adx14", adx_bins, "adx14_long")
    analyze_indicator(short_df, "adx14", adx_bins, "adx14_short")

    analyze_indicator(long_df, "adx20", adx_bins, "adx20_long")
    analyze_indicator(short_df, "adx20", adx_bins, "adx20_short")

    # ---------------- ATR % ----------------
    atr_bins = [0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.007, 0.01, 1]

    analyze_indicator(long_df, "atr14_pct", atr_bins, "atr14_long")
    analyze_indicator(short_df, "atr14_pct", atr_bins, "atr14_short")

    # ---------------- Relative Volume ----------------
    vol_bins = [0, 0.8, 1.0, 1.1, 1.2, 1.3, 1.5, 2, 3, 10]

    analyze_indicator(long_df, "relative_volume20", vol_bins, "relvol20_long")
    analyze_indicator(short_df, "relative_volume20", vol_bins, "relvol20_short")

    analyze_indicator(long_df, "relative_volume50", vol_bins, "relvol50_long")
    analyze_indicator(short_df, "relative_volume50", vol_bins, "relvol50_short")

    # ---------------- CMF ----------------
    cmf_bins = [-1, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2, 1]

    analyze_indicator(long_df, "cmf20", cmf_bins, "cmf20_long")
    analyze_indicator(short_df, "cmf20", cmf_bins, "cmf20_short")

    # ---------------- CCI ----------------
    cci_bins = [-400, -200, -150, -100, -50, 0, 50, 100, 150, 200, 400]

    analyze_indicator(long_df, "cci20", cci_bins, "cci20_long")
    analyze_indicator(short_df, "cci20", cci_bins, "cci20_short")

    # ---------------- Stoch RSI ----------------
    stoch_bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]

    analyze_indicator(long_df, "stoch_rsi14", stoch_bins, "stoch_long")
    analyze_indicator(short_df, "stoch_rsi14", stoch_bins, "stoch_short")

    print("\nAnalysis complete.", flush=True)


if __name__ == "__main__":
    main()