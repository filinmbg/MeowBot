import pandas as pd
df = pd.read_csv("test/data/BTCUSDT/BTCUSDT_1m.csv.gz", nrows=1)
print(df.columns.tolist())