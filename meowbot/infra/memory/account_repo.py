from __future__ import annotations
from meowbot.core.ports.account_repo import AccountRepository


class InMemoryAccountRepo(AccountRepository):
    def __init__(self, equity_usd: float):
        self.equity_usd = float(equity_usd)

    def get_equity_usd(self) -> float:
        return self.equity_usd