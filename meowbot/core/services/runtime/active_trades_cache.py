from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

from meowbot.core.domain.enums import TradeStatus
from meowbot.core.domain.types import Trade


log = logging.getLogger("meowbot")
PLACEHOLDER_RUNTIME_USER_IDS = {"tg:123456789"}


class ActiveTradesCache:
    """Process-local open trades index used by the trading runtime.

    Mongo remains the persistence layer, but entry/exit decisions need a fast
    and reliable source that does not block on Atlas latency.
    """

    def __init__(self) -> None:
        self.active_trades_by_user: dict[str, dict[str, Trade]] = defaultdict(dict)

    def load_open_trades(self, trades: Iterable[Trade], *, source: str = "startup") -> None:
        self.active_trades_by_user.clear()
        loaded = 0
        duplicates: dict[tuple[str, str], list[str]] = defaultdict(list)
        for trade in trades:
            if not self.is_open_trade(trade):
                continue
            user_id = self._user_id(trade)
            symbol = self._symbol(trade)
            if self._is_placeholder_user_id(user_id):
                log.warning(
                    "[trades-cache] fake debug user trade skipped user=%s symbol=%s trade_id=%s source=%s",
                    user_id,
                    symbol,
                    getattr(trade, "trade_id", None),
                    source,
                )
                continue
            if self.has_open_trade(user_id=user_id, symbol=symbol):
                duplicates[(user_id, symbol)].append(str(getattr(trade, "trade_id", "") or ""))
                log.error(
                    "[DUPLICATE_OPEN_TRADES_FOUND] source=%s user_id=%s symbol=%s duplicate_count=%s canonical_trade_id=%s incoming_trade_id=%s action=ignored",
                    source,
                    user_id,
                    symbol,
                    2 + len(duplicates[(user_id, symbol)]) - 1,
                    getattr(self.active_trades_by_user[user_id][symbol], "trade_id", None),
                    getattr(trade, "trade_id", None),
                )
                continue
            self.upsert(trade)
            loaded += 1
        log.info("[trades-cache] loaded open trades source=%s count=%s users=%s", source, loaded, len(self.active_trades_by_user))

    def upsert(self, trade: Trade) -> None:
        user_id = self._user_id(trade)
        symbol = self._symbol(trade)
        if not user_id or not symbol:
            return

        if not self.is_open_trade(trade):
            self.remove(user_id=user_id, symbol=symbol)
            return

        current = self.active_trades_by_user[user_id].get(symbol)
        if current is not None and getattr(current, "trade_id", None) != getattr(trade, "trade_id", None):
            log.error(
                "[DUPLICATE_OPEN_TRADES_FOUND] user_id=%s symbol=%s duplicate_count=2 canonical_trade_id=%s superseded_trade_ids=%s action=ignored_cache_upsert",
                user_id,
                symbol,
                getattr(current, "trade_id", None),
                [getattr(trade, "trade_id", None)],
            )
            return

        self.active_trades_by_user[user_id][symbol] = trade

    def has_open_trade(self, *, user_id: str, symbol: str, mode: str | None = None) -> bool:
        return self.get_open_trade(user_id=user_id, symbol=symbol, mode=mode) is not None

    def remove(self, *, user_id: str, symbol: str) -> None:
        normalized_user_id = str(user_id)
        normalized_symbol = str(symbol).upper()
        user_rows = self.active_trades_by_user.get(normalized_user_id)
        if not user_rows:
            return
        user_rows.pop(normalized_symbol, None)
        if not user_rows:
            self.active_trades_by_user.pop(normalized_user_id, None)

    def update_from_trade(self, trade: Trade) -> None:
        if self.is_open_trade(trade):
            self.upsert(trade)
            return
        self.remove(user_id=self._user_id(trade), symbol=self._symbol(trade))

    def get_user_open_trades(self, user_id: str, *, mode: str | None = None) -> list[Trade]:
        rows = list(self.active_trades_by_user.get(str(user_id), {}).values())
        return self._filter_open(rows, mode=mode)

    def get_all_open_trades(self, *, mode: str | None = None) -> list[Trade]:
        rows: list[Trade] = []
        for user_rows in self.active_trades_by_user.values():
            rows.extend(user_rows.values())
        return self._filter_open(rows, mode=mode)

    def get_open_trade(self, *, user_id: str, symbol: str, mode: str | None = None) -> Trade | None:
        trade = self.active_trades_by_user.get(str(user_id), {}).get(str(symbol).upper())
        if trade is None or not self.is_open_trade(trade):
            return None
        if mode is not None and str(getattr(trade, "mode", "")) != str(mode):
            return None
        return trade

    def list_open_live_user_ids(self) -> list[str]:
        result: list[str] = []
        for user_id, user_rows in self.active_trades_by_user.items():
            if any(str(getattr(trade, "mode", "")) == "live" and self.is_open_trade(trade) for trade in user_rows.values()):
                result.append(user_id)
        return sorted(result)

    def count(self) -> int:
        return sum(len(rows) for rows in self.active_trades_by_user.values())

    @staticmethod
    def is_open_trade(trade: Trade) -> bool:
        status = getattr(trade, "status", None)
        status_value = status.value if hasattr(status, "value") else str(status)
        return status_value == TradeStatus.OPEN.value

    def _filter_open(self, rows: list[Trade], *, mode: str | None = None) -> list[Trade]:
        result = [trade for trade in rows if self.is_open_trade(trade)]
        if mode is not None:
            result = [trade for trade in result if str(getattr(trade, "mode", "")) == str(mode)]
        return result

    @staticmethod
    def _user_id(trade: Trade) -> str:
        return str(getattr(trade, "user_id", "") or "")

    @staticmethod
    def _symbol(trade: Trade) -> str:
        return str(getattr(trade, "symbol", "") or "").upper()

    @staticmethod
    def _is_placeholder_user_id(user_id: str) -> bool:
        return str(user_id or "").strip() in PLACEHOLDER_RUNTIME_USER_IDS
