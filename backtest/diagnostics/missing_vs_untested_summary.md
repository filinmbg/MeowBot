# Missing vs Untested — Summary

- Вхідний CSV: `D:\Project\MeowBot\backtest\diagnostics\diagnostics_missing_models.csv`
- Записано: `D:\Project\MeowBot\backtest\diagnostics\missing_model_files.csv` та `D:\Project\MeowBot\backtest\diagnostics\untested_models.csv`

**Усього комбінацій**: 525
- **no_model_file**: 225
- **no_trades_csv_or_empty** (усі): 284
- **untested (чисті)**: 59  _(без no_model_file)_

## По родинах (кількість проблемних комбінацій)

### Відсутні моделі (no_model_file):
- PPO: 75
- QLEARNING: 75
- XGBOOST: 75

### Моделі є, але не тестувались (чисті untested):
- TRANSFORMER: 20
- CNN: 15
- LSTM: 14
- DQN: 10

## Що робити далі
- Для **missing_model_files.csv**: перевірити наявність файлів за колонками `expected_long_path` та `expected_short_path`. Згенерувати/скопіювати моделі у ці місця.
- Для **untested_models.csv**: запустити бектест (`run_backtest_multi_longshort.py`) — він створить `trades_*` файли.
