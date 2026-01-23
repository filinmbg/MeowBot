# test/incremental_trade_check_async.py
import os
import json
import uuid
import random
import logging
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

import pandas as pd

# 🔌 твій конектор
from database.mongo.connection import init_mongo, get_db, is_connected

# -------- Налаштування --------
os.environ.setdefault("MONGO_DB_NAME", "test")

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
TIMEFRAMES = os.getenv("TIMEFRAMES", "15m,30m,1h,4h,1d").split(",")
LOGS_DIR = "test/logs/incremental_check_async"
os.makedirs(LOGS_DIR, exist_ok=True)

RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))  # 1%
LEVERAGE = float(os.getenv("LEVERAGE", "20"))
START_DEPOSIT = float(os.getenv("START_DEPOSIT", "100"))

TP_STEPS = [0.005, 0.010, 0.015, 0.020]
SL_AFTER_STEP = {1: 0.000, 2: 0.002, 3: 0.004}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("incremental_check_async")

# -------- Трейд --------
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
    opened_at: int
    tp_hits: int = 0
    sl_level: float = -0.02
    is_closed: bool = False
    closed_reason: Optional[str] = None
    closed_price: Optional[float] = None
    closed_at: Optional[int] = None
    pnl_abs: float = 0.0
    pnl_pct_on_deposit: float = 0.0
    meta: Dict = field(default_factory=dict)

# -------- Утиліти --------
def calc_pnl_pct(side: str, entry: float, current: float) -> float:
    return (current - entry) / entry if side == "LONG" else (entry - current) / entry

def update_trailing_after_tp(trade: Trade):
    if trade.tp_hits in SL_AFTER_STEP:
        trade.sl_level = SL_AFTER_STEP[trade.tp_hits]

def save_trade_log(trade: Trade):
    d = os.path.join(LOGS_DIR, trade.trade_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "trade.json"), "w", encoding="utf-8") as f:
        json.dump(trade.__dict__, f, ensure_ascii=False, indent=2)

def save_summary(trades: List[Trade], filename: str = "summary.csv"):
    rows = []
    for tr in trades:
        rows.append({
            "trade_id": tr.trade_id, "symbol": tr.symbol, "tf": tr.tf, "side": tr.side,
            "entry_price": tr.entry_price, "opened_at": tr.opened_at,
            "tp_hits": tr.tp_hits, "closed": tr.is_closed, "closed_reason": tr.closed_reason,
            "closed_price": tr.closed_price, "closed_at": tr.closed_at,
            "pnl_abs": tr.pnl_abs, "pnl_pct_on_deposit": tr.pnl_pct_on_deposit
        })
    pd.DataFrame(rows).to_csv(os.path.join(LOGS_DIR, filename), index=False)
    wins = sum(1 for tr in trades if tr.tp_hits > 0)
    losses = sum(1 for tr in trades if tr.tp_hits == 0 and tr.closed_reason == "SL")
    winrate = (wins / len(trades) * 100) if trades else 0.0
    total_pnl = sum(tr.pnl_abs for tr in trades)
    logger.info(f"Trades: {len(trades)} | Wins: {wins} | Losses: {losses} | Winrate: {winrate:.1f}% | Total PnL: {total_pnl:.2f}")

# -------- Читання з Mongo --------
async def load_last_bars_mongo(symbol: str, tf: str, count: int = 300) -> pd.DataFrame:
    coll = get_db()["bars"]
    docs = await coll.find({"symbol": symbol, "tf": tf}).sort("close_time", 1).to_list(length=None)
    if not docs:
        raise FileNotFoundError(f"У Mongo немає барів для {symbol} {tf}. Запусти loader.")
    return pd.DataFrame(docs[-count:]).reset_index(drop=True)

async def fetch_next_and_history_for_indicators_mongo(symbol: str, tf: str, last_close_time: Optional[int], need_hist: int = 200) -> pd.DataFrame:
    coll = get_db()["bars"]
    if last_close_time is None:
        docs = await coll.find({"symbol": symbol, "tf": tf}).sort("close_time", -1).limit(need_hist + 1).to_list(length=None)
        docs.reverse()
        return pd.DataFrame(docs)

    docs = await coll.find({"symbol": symbol, "tf": tf, "close_time": {"$gte": last_close_time - 10**12}}).sort("close_time", 1).to_list(length=None)
    if not docs:
        return pd.DataFrame()
    try:
        idx = next(i for i, d in enumerate(docs) if d["close_time"] == last_close_time)
    except StopIteration:
        return pd.DataFrame(docs[-(need_hist + 1):])
    start = max(0, idx - need_hist + 1)
    end = min(len(docs), idx + 2)
    return pd.DataFrame(docs[start:end])

async def fetch_1m_bars_from_mongo(symbol: str, ts_start: int, ts_end: Optional[int] = None) -> pd.DataFrame:
    coll = get_db()["bars"]
    q = {"symbol": symbol, "tf": "1m", "close_time": {"$gte": ts_start}}
    if ts_end is not None:
        q["close_time"]["$lte"] = ts_end
    docs = await coll.find(q).sort("close_time", 1).to_list(length=None)
    if not docs:
        raise FileNotFoundError("У Mongo немає 1m барів. Завантаж їх loader'ом (timeframe=1m).")
    return pd.DataFrame(docs)

# -------- Моделі (заглушка) --------
def load_model(symbol: str, tf: str, variant: Optional[str] = None):
    return {"name": f"{symbol}_{tf}_{variant or 'Vx'}"}

async def model_predict_async(model, row: pd.Series) -> Optional[Literal["LONG", "SHORT", "HOLD"]]:
    # тут підставиш torch/onnx inference; зараз — випадковий сигнал для демо
    r = random.random()
    if r < 0.05:
        return "LONG"
    elif r < 0.08:
        return "SHORT"
    return "HOLD"

# -------- Моніторинг по 1m --------
def monitor_trades_on_1m(trades: List[Trade], one_minute_df: pd.DataFrame, price_col: str = "close", time_col: str = "close_time"):
    for _, row in one_minute_df.iterrows():
        price = float(row[price_col]); ts = int(row[time_col])
        for tr in trades:
            if tr.is_closed:
                continue
            move_pct = calc_pnl_pct(tr.side, tr.entry_price, price)

            # TP
            next_tp_idx = tr.tp_hits
            if next_tp_idx < len(TP_STEPS) and move_pct >= TP_STEPS[next_tp_idx]:
                tr.tp_hits += 1
                update_trailing_after_tp(tr)
                if tr.tp_hits == len(TP_STEPS):
                    tr.is_closed = True
                    tr.closed_reason = f"TP{tr.tp_hits}"
                    tr.closed_price = price
                    tr.closed_at = ts
                    effective_move = sum(TP_STEPS[:tr.tp_hits]) / len(TP_STEPS)
                    tr.pnl_abs = tr.deposit_at_entry * tr.risk_pct * tr.leverage * effective_move
                    tr.pnl_pct_on_deposit = tr.pnl_abs / tr.deposit_at_entry if tr.deposit_at_entry > 0 else 0.0
                    continue

            # SL
            if move_pct <= tr.sl_level:
                tr.is_closed = True
                tr.closed_reason = "SL"
                tr.closed_price = price
                tr.closed_at = ts
                if tr.tp_hits > 0:
                    fixed = sum(TP_STEPS[:tr.tp_hits]) / len(TP_STEPS)
                    rest = tr.sl_level
                    effective_move = (tr.tp_hits * 0.25) * fixed + (1 - tr.tp_hits * 0.25) * rest
                else:
                    effective_move = tr.sl_level
                tr.pnl_abs = tr.deposit_at_entry * tr.risk_pct * tr.leverage * effective_move
                tr.pnl_pct_on_deposit = tr.pnl_abs / tr.deposit_at_entry if tr.deposit_at_entry > 0 else 0.0

# -------- Runner --------
class IncrementalRunner:
    def __init__(self, symbol: str, tfs: List[str], max_parallel_fetch: int = 3):
        self.symbol = symbol
        self.tfs = tfs
        self.models = {tf: load_model(symbol, tf) for tf in tfs}
        self.deposit = START_DEPOSIT
        self.all_trades: List[Trade] = []
        self.pause_fetch = asyncio.Event()
        self.pause_fetch.clear()
        self.sem = asyncio.Semaphore(max_parallel_fetch)

    async def fetch_and_decide(self, tf: str):
        async with self.sem:
            df_last = await load_last_bars_mongo(self.symbol, tf, count=300)
            last_close_time = int(df_last["close_time"].iloc[-1])

            df_next = await fetch_next_and_history_for_indicators_mongo(self.symbol, tf, last_close_time, need_hist=200)
            if df_next.empty:
                logger.info(f"[{tf}] Немає даних для next/history — пропуск.")
                return

            row = df_next.iloc[-1]
            signal = await model_predict_async(self.models[tf], row)
            logger.info(f"[{tf}] сигнал моделі: {signal}")

            if signal in ("LONG", "SHORT"):
                # глобальна пауза
                self.pause_fetch.set()
                entry_price = float(row["close"])
                tr = Trade(
                    trade_id=str(uuid.uuid4())[:8],
                    symbol=self.symbol, tf=tf, side=signal,  # type: ignore
                    entry_price=entry_price, risk_pct=RISK_PCT, leverage=LEVERAGE,
                    deposit_at_entry=self.deposit,
                    position_qty=(self.deposit * RISK_PCT * LEVERAGE) / entry_price,
                    opened_at=int(row["close_time"])
                )
                logger.info(f"[{tf}] ВІДКРИТО трейд #{tr.trade_id} {tr.side} @ {tr.entry_price:.2f}")

                one_min_df = await fetch_1m_bars_from_mongo(self.symbol, ts_start=tr.opened_at)
                monitor_trades_on_1m([tr], one_minute_df=one_min_df)

                self.deposit += tr.pnl_abs
                logger.info(f"[{tf}] ЗАКРИТО трейд #{tr.trade_id} причина={tr.closed_reason} pnl={tr.pnl_abs:.2f} депо={self.deposit:.2f}")
                save_trade_log(tr)
                self.all_trades.append(tr)

    async def run_once_over_all_tfs(self):
        tasks = []
        for tf in self.tfs:
            if self.pause_fetch.is_set():
                logger.info("⏸️ Пауза активна — нові завантаження не стартуємо.")
                break
            tasks.append(asyncio.create_task(self.fetch_and_decide(tf)))
        if tasks:
            await asyncio.gather(*tasks)

        if self.pause_fetch.is_set():
            logger.info("✅ Всі відкриті трейди перевірені — знімаємо паузу.")
            self.pause_fetch.clear()

    async def run(self, loops: int = 1, sleep_sec: float = 0.0):
        for i in range(loops):
            logger.info(f"========== Ітерація {i+1}/{loops} ==========")
            await self.run_once_over_all_tfs()
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)
        save_summary(self.all_trades, filename="summary.csv")

async def amain():
    await init_mongo()  # читає з .env MONGO_URI / MONGO_DB_NAME
    if not is_connected():
        raise RuntimeError("Mongo не ініціалізовано. Перевір .env (MONGO_URI, MONGO_DB_NAME).")

    runner = IncrementalRunner(SYMBOL, TIMEFRAMES, max_parallel_fetch=3)
    await runner.run(loops=1, sleep_sec=0.0)

if __name__ == "__main__":
    asyncio.run(amain())
