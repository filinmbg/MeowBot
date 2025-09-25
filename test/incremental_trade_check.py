# файл: test/incremental_trade_check.py
# призначення: інкрементальна перевірка "останній бар -> моделі -> пауза -> моніторинг 1m -> відновити"
# залежності: pandas, numpy (та твої локальні утиліти, якщо є)

import os
import json
import time
import uuid
import math
import shutil
import random
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

import pandas as pd
import numpy as np

# =========================
# НАЛАШТУВАННЯ
# =========================
SYMBOL = "BTCUSDT"
TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]   # твій робочий набір
DATA_ROOT = "test/data"                         # де лежать CSV з індикаторами
BARS_ROOT = "bars"                              # якщо читаєш з файлової системи
BARS_LAST_ROOT = "bars_last"
LOGS_DIR = "test/logs/incremental_check"        # куди складати логи трейдів та підсумки
MODELS_DIR = "Models"                            # де шукати моделі
RISK_PCT = 0.01                                  # 1% депозиту на трейд (можеш змінити)
LEVERAGE = 20
START_DEPOSIT = 100.0
MAX_TRADES_PER_SIGNAL = 1                        # кожна модель відкриває максимум 1 трейд

# TP/SL-правила (сходинки)
TP_STEPS = [0.005, 0.010, 0.015, 0.020]  # +0.5%, +1.0%, +1.5%, +2.0%
# після спрацювання 1-ї сходинки -> move SL to 0.0%, 2-ї -> +0.2%, 3-ї -> +0.4%, 4-ї -> закриваємо
SL_AFTER_STEP = {1: 0.000, 2: 0.002, 3: 0.004}

os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("incremental_check")

# =========================
# УТИЛІТИ ЗАВАНТАЖЕННЯ/ІНДИКАТОРІВ/МОДЕЛЕЙ (СТАБИ)
# =========================

def load_last_bars(symbol: str, tf: str, count: int = 300) -> pd.DataFrame:
    """
    Завантажуємо останні N барів для TF.
    ТУТ: зроби реально — з Mongo/CSV. Зараз — демо із CSV індикаторів.
    Очікується файл: test/data/SYMBOL/SYMBOL_<tf>_indicators.csv
    """
    path = os.path.join(DATA_ROOT, symbol, f"{symbol}_{tf}_indicators.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Не знайдено {path}. Підкинь дані або зміни loader.")
    df = pd.read_csv(path)
    if "close_time" in df.columns:
        df.sort_values("close_time", inplace=True)
    else:
        df.sort_index(inplace=True)
    return df.tail(count).reset_index(drop=True)

def fetch_next_and_history_for_indicators(symbol: str, tf: str, last_close_time: Optional[int], need_hist: int = 200) -> pd.DataFrame:
    """
    Дозавантажуємо наступний бар + достатню історію для індикаторів.
    У реалі: тягнемо з Binance або з власного агрегатора. Тут — емулюємо невеличким зрізом з CSV.
    """
    path = os.path.join(DATA_ROOT, symbol, f"{symbol}_{tf}_indicators.csv")
    df = pd.read_csv(path)
    if "close_time" in df.columns:
        df.sort_values("close_time", inplace=True)
        df = df.reset_index(drop=True)
        if last_close_time is not None:
            idx = df.index[df["close_time"] == last_close_time]
            if len(idx) == 0:
                # якщо не знайшли — беремо останні need_hist+1 рядків
                return df.tail(need_hist + 1).reset_index(drop=True)
            i = idx[0]
            # беремо історію і +1 наступний бар
            start = max(0, i - need_hist + 1)
            end = min(len(df), i + 2)  # i (останній) + 1 новий
            return df.iloc[start:end].reset_index(drop=True)
        else:
            return df.tail(need_hist + 1).reset_index(drop=True)
    else:
        return df.tail(need_hist + 1).reset_index(drop=True)

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Якщо індикатори вже є — можна пропустити.
    Інакше — додай тут обчислення.
    Поки що повертаємо як є.
    """
    return df

def load_model(symbol: str, tf: str, variant: Optional[str] = None):
    """
    Завантаження моделі (CNN/LSTM/DQN/PPO/Transformer).
    Тут — заглушка, яка імітує сигнал із невеликою ймовірністю.
    Підключиш свої реальні моделі (torch.load / pickle / onnx).
    """
    key = f"{symbol}_{tf}_{variant or 'Vx'}"
    return {"name": key}

def model_predict(model, row: pd.Series) -> Optional[Literal["LONG", "SHORT", "HOLD"]]:
    """
    Повертає сигнал моделі на основі останнього рядка індикаторів.
    Зараз — демо: 5% шанс LONG, 3% шанс SHORT, інакше HOLD.
    Замінити на реальний inferece.
    """
    r = random.random()
    if r < 0.05:
        return "LONG"
    elif r < 0.08:
        return "SHORT"
    else:
        return "HOLD"

# =========================
# ТРЕЙДІНГ-ЛОГІКА
# =========================

@dataclass
class Trade:
    trade_id: str
    symbol: str
    tf: str
    side: Literal["LONG", "SHORT"]
    entry_price: float
    risk_pct: float
    leverage: float
    deposit_at_entry: float
    position_qty: float
    opened_at: int  # close_time бару TF
    tp_hits: int = 0
    sl_level: float = -0.02  # початковий SL = -2% (можеш змінити)
    is_closed: bool = False
    closed_reason: Optional[str] = None
    closed_price: Optional[float] = None
    closed_at: Optional[int] = None
    pnl_abs: float = 0.0
    pnl_pct_on_deposit: float = 0.0
    meta: Dict = field(default_factory=dict)

def calc_pnl_pct(side: str, entry: float, current: float) -> float:
    """Відсоток руху ціни щодо входу (для LONG позитивний, для SHORT позитивний коли ціна падає)."""
    if side == "LONG":
        return (current - entry) / entry
    else:
        return (entry - current) / entry

def update_trailing_after_tp(trade: Trade):
    """Після TP пересуваємо SL згідно правил."""
    if trade.tp_hits in SL_AFTER_STEP:
        trade.sl_level = SL_AFTER_STEP[trade.tp_hits]

def monitor_trades_on_1m(trades: List[Trade], one_minute_df: pd.DataFrame, price_col: str = "close", time_col: str = "close_time"):
    """
    Моніторимо відкриті трейди по 1m-барам:
    - TP сходинки: 0.5%, 1.0%, 1.5%, 2.0%
    - SL рухається після 1/2/3-го TP
    - Закриття коли: або спрацював останній TP (4-та сходинка), або ціна повернулась до SL-рівня
    """
    for _, row in one_minute_df.iterrows():
        price = float(row[price_col])
        ts = int(row[time_col])

        for tr in trades:
            if tr.is_closed:
                continue

            move_pct = calc_pnl_pct(tr.side, tr.entry_price, price)

            # спочатку TP
            next_tp_idx = tr.tp_hits  # 0..3
            if next_tp_idx < len(TP_STEPS) and move_pct >= TP_STEPS[next_tp_idx]:
                tr.tp_hits += 1
                update_trailing_after_tp(tr)
                if tr.tp_hits == len(TP_STEPS):
                    # досягли останнього TP -> закриваємо
                    tr.is_closed = True
                    tr.closed_reason = f"TP{tr.tp_hits}"
                    tr.closed_price = price
                    tr.closed_at = ts
                    # часткові закриття: 25% + 25% + 25% + 25%
                    effective_move = sum(TP_STEPS[:tr.tp_hits]) / len(TP_STEPS)  # грубий середній
                    tr.pnl_abs = tr.deposit_at_entry * tr.risk_pct * tr.leverage * effective_move
                    tr.pnl_pct_on_deposit = (tr.pnl_abs / tr.deposit_at_entry) if tr.deposit_at_entry > 0 else 0.0
                    continue  # наступний трейд

            # потім SL (динамічний)
            if move_pct <= tr.sl_level:
                tr.is_closed = True
                tr.closed_reason = "SL"
                tr.closed_price = price
                tr.closed_at = ts
                # якщо був хоча б один TP — вважаємо виграшним (за твоїм правилом)
                if tr.tp_hits > 0:
                    # приблизний ефект: TP-частки вже зафіксовані, решта закрита по SL
                    fixed = sum(TP_STEPS[:tr.tp_hits]) / len(TP_STEPS)
                    rest = tr.sl_level  # негативний/невеликий позитив
                    effective_move = (tr.tp_hits * 0.25) * fixed + (1 - tr.tp_hits * 0.25) * rest
                else:
                    effective_move = tr.sl_level
                tr.pnl_abs = tr.deposit_at_entry * tr.risk_pct * tr.leverage * effective_move
                tr.pnl_pct_on_deposit = (tr.pnl_abs / tr.deposit_at_entry) if tr.deposit_at_entry > 0 else 0.0

def fetch_1m_bars_from(ts_start: int, ts_end: Optional[int] = None) -> pd.DataFrame:
    """
    Повертає 1m-бари починаючи з ts_start. У реалі — тягнемо з твого сховища чи Binance.
    Тут беремо з CSV: test/data/SYMBOL/BTCUSDT_1m.csv.gz (зміни шлях, якщо інший).
    Колонки очікуються: close_time, open, high, low, close, volume
    """
    path = os.path.join(DATA_ROOT, SYMBOL, f"{SYMBOL}_1m.csv.gz")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Потрібен 1m CSV: {path}")
    df = pd.read_csv(path)
    df.sort_values("close_time", inplace=True)
    if ts_start is not None:
        df = df[df["close_time"] >= ts_start]
    if ts_end is not None:
        df = df[df["close_time"] <= ts_end]
    return df.reset_index(drop=True)

# =========================
# ЗБЕРЕЖЕННЯ ЛОГІВ/СТАТИСТИКИ
# =========================

def save_trade_log(trade: Trade):
    trade_dir = os.path.join(LOGS_DIR, trade.trade_id)
    os.makedirs(trade_dir, exist_ok=True)
    with open(os.path.join(trade_dir, "trade.json"), "w", encoding="utf-8") as f:
        json.dump(trade.__dict__, f, ensure_ascii=False, indent=2)

def save_summary(trades: List[Trade], filename: str = "summary.csv"):
    rows = []
    for tr in trades:
        rows.append({
            "trade_id": tr.trade_id,
            "symbol": tr.symbol,
            "tf": tr.tf,
            "side": tr.side,
            "entry_price": tr.entry_price,
            "opened_at": tr.opened_at,
            "tp_hits": tr.tp_hits,
            "closed": tr.is_closed,
            "closed_reason": tr.closed_reason,
            "closed_price": tr.closed_price,
            "closed_at": tr.closed_at,
            "pnl_abs": tr.pnl_abs,
            "pnl_pct_on_deposit": tr.pnl_pct_on_deposit
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(LOGS_DIR, filename), index=False)
    logger.info(f"Збережено підсумок: {os.path.join(LOGS_DIR, filename)}")
    # короткий звіт
    wins = sum(1 for tr in trades if tr.tp_hits > 0)
    losses = sum(1 for tr in trades if tr.tp_hits == 0 and tr.closed_reason == "SL")
    winrate = (wins / len(trades) * 100) if trades else 0.0
    total_pnl = sum(tr.pnl_abs for tr in trades)
    logger.info(f"Trades: {len(trades)} | Wins: {wins} | Losses: {losses} | Winrate: {winrate:.1f}% | Total PnL: {total_pnl:.2f}")

# =========================
# ОСНОВНИЙ ЦИКЛ
# =========================

def run_incremental_check():
    deposit = START_DEPOSIT
    all_trades: List[Trade] = []

    # Попередньо завантажимо моделі по кожному TF (можеш змінити структуру під свої)
    models = {tf: load_model(SYMBOL, tf) for tf in TIMEFRAMES}

    # 1) беремо останні бари
    last_per_tf: Dict[str, pd.DataFrame] = {}
    for tf in TIMEFRAMES:
        last_per_tf[tf] = load_last_bars(SYMBOL, tf, count=300)

    # 2) Головний цикл: для прикладу — один “крок” по кожному TF
    # За потреби обгорни в while True
    for tf in TIMEFRAMES:
        df_last = last_per_tf[tf]
        last_close_time = int(df_last["close_time"].iloc[-1]) if "close_time" in df_last.columns else None

        # 2a) дозавантажуємо "наступний бар + історію для індикаторів"
        df_next = fetch_next_and_history_for_indicators(SYMBOL, tf, last_close_time, need_hist=200)
        df_feat = compute_indicators(df_next)

        # 3) Проганяємо МОДЕЛЬ по останньому рядку
        model = models[tf]
        row = df_feat.iloc[-1]
        signal = model_predict(model, row)

        logger.info(f"[{tf}] сигнал моделі: {signal}")

        # 4) Якщо є команда на відкриття — ставимо паузу завантаження і моніторимо 1m
        if signal in ("LONG", "SHORT") and MAX_TRADES_PER_SIGNAL > 0:
            entry_price = float(row["close"]) if "close" in row else float(df_feat["close"].iloc[-1])
            tr = Trade(
                trade_id=str(uuid.uuid4())[:8],
                symbol=SYMBOL,
                tf=tf,
                side=signal,  # type: ignore
                entry_price=entry_price,
                risk_pct=RISK_PCT,
                leverage=LEVERAGE,
                deposit_at_entry=deposit,
                position_qty=(deposit * RISK_PCT * LEVERAGE) / entry_price,
                opened_at=int(row["close_time"]) if "close_time" in row else int(time.time()*1000)
            )
            logger.info(f"[{tf}] ВІДКРИТО трейд #{tr.trade_id} {tr.side} @ {tr.entry_price:.2f} (qty={tr.position_qty:.6f})")
            # Пауза завантаження TF — робимо моніторинг 1m
            ts_start = tr.opened_at
            one_min_df = fetch_1m_bars_from(ts_start=ts_start)
            monitor_trades_on_1m([tr], one_minute_df=one_min_df)

            # Оновлюємо депозит
            deposit += tr.pnl_abs
            logger.info(f"[{tf}] ЗАКРИТО трейд #{tr.trade_id} причина={tr.closed_reason} pnl={tr.pnl_abs:.2f} депо={deposit:.2f}")

            # зберегти лог трейду
            save_trade_log(tr)
            all_trades.append(tr)

        else:
            logger.info(f"[{tf}] Сигналу на відкриття немає — продовжуємо завантаження наступних барів.")

    # 5) Коли завершилась перевірка всіх відкритих трейдів — відновлюємо завантаження (в цій демо-версії не реалізовано, бо все робимо послідовно)
    # 6) Підсумкова статистика
    save_summary(all_trades, filename="summary.csv")


if __name__ == "__main__":
    run_incremental_check()
