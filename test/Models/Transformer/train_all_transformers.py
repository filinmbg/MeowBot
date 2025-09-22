import os
import sys

TIMEFRAMES = ["1d", "4h", "1h", "30m", "15m"]
TARGETS = ["long", "short"]

for tf in TIMEFRAMES:
    for target in TARGETS:
        print(f"\n🚀 Навчання: {tf} / {target.upper()}")
        exit_code = os.system(
            f"{sys.executable} test/Models/Transformer/train_transformer.py --timeframe {tf} --target {target}"
        )
        if exit_code != 0:
            print(f"❌ Помилка навчання для {tf} / {target.upper()}")
