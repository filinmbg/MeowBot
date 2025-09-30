# -*- coding: utf-8 -*-
"""
backtest/run_backtest.py

Лончер: спочатку запускає префлайт-перевірку (check_models.py),
і лише у разі успіху — запускає бектест (backtest_all_in_one.py).

Приклади:
  python backtest/run_backtest.py --symbols ALL --timeframes 15m,30m,1h,4h,1d --signals_mode separate
  python backtest/run_backtest.py --symbols BTCUSDT --timeframes 1d --signals_mode unified
"""

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "backtest"

def main():
    # Проксі-аргументи: що передали run_backtest — піде і в check_models, і в backtest
    args = sys.argv[1:]

    check_cmd = [sys.executable, str(BACKTEST_DIR / "check_models.py")] + args
    print("🔎 Запускаю префлайт-перевірку моделей і сигналів…")
    rc = subprocess.call(check_cmd)
    if rc != 0:
        print(f"❌ Перевірка не пройдена (exit={rc}). Бектест НЕ буде запущено.")
        sys.exit(rc)

    print("✅ Перевірка пройдена. Запускаю бектест…")
    bt_cmd = [sys.executable, str(BACKTEST_DIR / "backtest_all_in_one.py")] + args
    rc2 = subprocess.call(bt_cmd)
    if rc2 != 0:
        print(f"⚠️  Бектест завершився з кодом {rc2}.")
    else:
        print("🏁 Готово.")

if __name__ == "__main__":
    main()
