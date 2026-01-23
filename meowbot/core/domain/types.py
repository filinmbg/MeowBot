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
class Trade:
    trade_id: str
    symbol: str
    side: Side
    status: TradeStatus

    opened_at: int   # ms
    entry_price: float
    qty: float
    leverage: int
    stake_usd: float

    tf_entry: str
    model_id: str
    entry_bar_close_time: int  # close_time бару, на якому відкрили трейд

    # Exit state
    sl_price: float
    tp_hit_count: int = 0
    remaining_pct: float = 1.0
    exit_last_check_at: int = 0  # ms (1m close time processed)

    # Close info
    closed_at: Optional[int] = None
    close_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl_usd: float = 0.0
