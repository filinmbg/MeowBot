from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from .enums import Side, TradeStatus, SignalAction


@dataclass(frozen=True)
class Bar:
    symbol: str
    tf: str
    open_time: int   # ms
    close_time: int  # ms
    o: float
    h: float
    l: float
    c: float
    v: float
    features: Optional[Dict[str, float]] = None
    features_ok: bool = False
    features_ver: str = "v1"


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    score: float = 0.0
    meta: Optional[Dict[str, Any]] = None


@dataclass
@dataclass
class Trade:
    trade_id: str
    user_id: str
    symbol: str
    side: Side
    status: TradeStatus

    opened_at: int
    entry_price: float
    qty: float

    leverage: int
    stake_usd: float

    tf_entry: str
    model_id: str
    entry_bar_close_time: int

    sl_price: float

    # поля з дефолтами — тільки нижче
    mode: str = "sandbox"
    tp_hit_count: int = 0
    remaining_pct: float = 1.0
    exit_last_check_at: int = 0

    qty_remaining: Optional[float] = None
    closed_at: Optional[int] = None
    close_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl_usd: float = 0.0

    def __post_init__(self):
        if self.qty_remaining is None:
            self.qty_remaining = self.qty