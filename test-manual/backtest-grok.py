import pandas as pd
import os
from datetime import datetime

INITIAL_BALANCE = 10000.0
RISK_PER_TRADE = 0.01
FEE = 0.0004
RESULTS_DIR = "test-manual/results-grok"
os.makedirs(RESULTS_DIR, exist_ok=True)

# Завантажуємо 4h для сигналів
print("=== Завантаження 4h для сигналів ===")
df4h = pd.read_csv("test-manual/indicators/BTCUSDT/BTCUSDT_4h_indicators.csv.gz",
                   compression='gzip', parse_dates=['datetime'])
df4h = df4h.sort_values('datetime').reset_index(drop=True)

# Сигнали тільки на 4h
adx_threshold = 27
df4h['strong_bull'] = (
    (df4h['ema_50'] > df4h['ema_200']) &
    (df4h['close'] > df4h['ema_200'] * 1.005) &
    (df4h.get('adx_14', 0) >= adx_threshold) &
    (df4h['supertrend_dir_10_3'] == 1)
)

df4h['strong_bear'] = (
    (df4h['ema_50'] < df4h['ema_200']) &
    (df4h['close'] < df4h['ema_200'] * 0.995) &
    (df4h.get('adx_14', 0) >= adx_threshold) &
    (df4h['supertrend_dir_10_3'] == -1)
)

df4h['long_signal_4h'] = df4h['strong_bull'] & (df4h['rsi_14'].between(48, 66)) & (df4h['macd_hist'] > 0)
df4h['short_signal_4h'] = df4h['strong_bear'] & (df4h['rsi_14'].between(34, 52)) & (df4h['macd_hist'] < 0)

# Завантажуємо 1h для виконання
print("=== Завантаження 1h для входу ===")
df1h = pd.read_csv("test-manual/indicators/BTCUSDT/BTCUSDT_1h_indicators.csv.gz",
                   compression='gzip', parse_dates=['datetime'])
df1h = df1h.sort_values('datetime').reset_index(drop=True)

# Мапимо сигнали 4h на 1h
df1h['long_signal'] = False
df1h['short_signal'] = False

for idx, row in df4h.iterrows():
    if row['long_signal_4h']:
        mask = (df1h['datetime'] >= row['datetime']) & (df1h['datetime'] < row['datetime'] + pd.Timedelta(hours=4))
        df1h.loc[mask, 'long_signal'] = True
    if row['short_signal_4h']:
        mask = (df1h['datetime'] >= row['datetime']) & (df1h['datetime'] < row['datetime'] + pd.Timedelta(hours=4))
        df1h.loc[mask, 'short_signal'] = True

# Бектест на 1h
balance = INITIAL_BALANCE
trades = []
position = None

for i in range(len(df1h)):
    row = df1h.iloc[i]
    price = row['close']
    atr = row.get('atr_14', 0)

    # Закриття
    if position:
        if position['type'] == 'long':
            pnl_pct = (price - position['entry_price']) / position['entry_price']
            if row.get('supertrend_dir_10_3', 0) == -1 or price < position.get('trailing', 0):
                pnl = pnl_pct * position['size'] - FEE * 2 * position['size']
                balance += pnl
                trades.append({...})  # (заповни аналогічно попереднім версіям)
                position = None
        else:  # short
            # аналогічно
            pass

    # Вхід
    if position is None and atr > 0:
        if row['long_signal']:
            sl_dist = atr * 3.0
            size = (balance * RISK_PER_TRADE) / (sl_dist / price)
            position = {
                'type': 'long',
                'entry_price': price,
                'entry_time': row['datetime'],
                'size': size,
                'trailing': price - atr * 2.0
            }
        elif row['short_signal']:
            # аналогічно для short
            pass

# Збереження
trades_df = pd.DataFrame(trades)
trades_df.to_csv(f"{RESULTS_DIR}/trades_grok_v6_multitimeframe.csv", index=False)

total_return = (balance / INITIAL_BALANCE - 1) * 100
winrate = (trades_df['pnl_pct'] > 0).mean() * 100 if len(trades_df) > 0 else 0
print(f"Баланс: ${balance:,.2f} | Прибуток: {total_return:+.2f}% | Угод: {len(trades)} | Winrate: {winrate:.1f}%")