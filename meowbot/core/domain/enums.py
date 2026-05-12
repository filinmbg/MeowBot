from __future__ import annotations
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    ERROR_INVALID_SYMBOL = "ERROR_INVALID_SYMBOL"
    DUPLICATE_SUPERSEDED = "DUPLICATE_SUPERSEDED"


class SignalAction(str, Enum):
    HOLD = "HOLD"
    LONG = "LONG"
    SHORT = "SHORT"
