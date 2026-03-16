from pathlib import Path
from test.backtest.backtest_rsi_rebound_supertrend_core import run_backtest_for_tf

if __name__ == "__main__":
    run_backtest_for_tf(
        symbol="BTCUSDT",
        tf="4h",
        data_dir=Path(r"D:\Project\MeowBot\test\data\BTCUSDT"),
        results_dir=Path(r"D:\Project\MeowBot\test\results"),
    )
