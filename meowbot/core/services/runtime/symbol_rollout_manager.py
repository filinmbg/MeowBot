from __future__ import annotations

import time
import logging

from meowbot.core.services.runtime.symbol_validator import filter_valid_binance_usdt_symbols


log = logging.getLogger("meowbot")


class SymbolRolloutManager:
    def __init__(
        self,
        *,
        all_symbols: list[str],
        base_count: int = 5,
        step: int = 5,
        max_count: int | None = None,
        grow_interval_seconds: int = 900,
    ) -> None:
        cleaned, invalid = filter_valid_binance_usdt_symbols(all_symbols)
        if invalid:
            log.warning(
                "[rollout] invalid symbols skipped count=%s examples=%s",
                len(invalid),
                [f"{item.normalized_symbol or item.symbol}:{item.reason}" for item in invalid[:10]],
            )
        self.all_symbols = cleaned
        self.base_count = max(1, base_count)
        self.step = max(1, step)
        self.max_count = min(max_count or len(cleaned), len(cleaned))
        self.grow_interval_seconds = max(60, int(grow_interval_seconds))

        self._current_count = min(self.base_count, self.max_count)
        self._last_grow_monotonic = time.monotonic()

    @property
    def current_count(self) -> int:
        return self._current_count

    def get_active_symbols(self) -> list[str]:
        return self.all_symbols[: self._current_count]

    def maybe_grow(self) -> bool:
        now = time.monotonic()

        if self._current_count >= self.max_count:
            return False

        if (now - self._last_grow_monotonic) < self.grow_interval_seconds:
            return False

        new_count = min(self._current_count + self.step, self.max_count)
        changed = new_count != self._current_count

        if changed:
            self._current_count = new_count
            self._last_grow_monotonic = now

        return changed
