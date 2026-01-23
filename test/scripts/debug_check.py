import pandas as pd

df = pd.read_csv("test/data/BTCUSDT/BTCUSDT_1d_critical_indicators_with_targets_long.csv")
print(df.columns.tolist())