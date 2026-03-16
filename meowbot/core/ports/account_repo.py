from __future__ import annotations
from typing import Protocol


class AccountRepository(Protocol):
    def get_equity_usd(self) -> float:
        """Поточна equity (депозит) в USD."""
        ...