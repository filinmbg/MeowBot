# Diagnostics: Missing/Unrun Models

- CSV: `D:\Project\MeowBot\backtest\diagnostics\diagnostics_missing_models.csv` (118646 bytes)
- DATA_DIR: `D:\Project\MeowBot\backtest\data\BTCUSDT`
- MODEL_ROOT: `D:\Project\MeowBot\models\BTCUSDT`

## Top issues (count)

- **no_trades_csv_or_empty** — 284
- **no_model_file** — 225

## Legend
- `no_tf_data` — немає TF-файлу з індикаторами
- `no_m1_data` — немає даних 1m
- `no_model_file` — відсутній файл моделі з точним суфіксом
- `missing_lib:*` — потрібна бібліотека не встановлена
- `missing_features_in_tf` — у spec/features.json є фічі, яких немає у TF-файлі
- `no_trades_csv_or_empty` — не запускалася перевірка або результати порожні
- `ok` — проблем не виявлено

## Примітка
- Колонки `long_issues` та `short_issues` показують причини окремо по кожній стороні.