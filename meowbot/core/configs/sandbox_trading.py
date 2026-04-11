from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SandboxEntryMode = Literal["percent", "fixed"]


class SandboxTradingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    starting_balance_usd: float = Field(default=1000.0, gt=0)
    leverage: float = Field(default=20.0, gt=0)

    entry_mode: SandboxEntryMode = "percent"

    # якщо entry_mode="percent"
    entry_percent: float = Field(default=0.01, gt=0, le=1)

    # якщо entry_mode="fixed"
    entry_fixed_usd: float = Field(default=10.0, gt=0)

    min_stake_usd: float = Field(default=5.0, gt=0)

    def resolve_stake_usd(self, current_balance_usd: float) -> float:
        if self.entry_mode == "fixed":
            return float(min(current_balance_usd, self.entry_fixed_usd))

        return float(max(self.min_stake_usd, current_balance_usd * self.entry_percent))


DEFAULT_SANDBOX_TRADING_CONFIG = SandboxTradingConfig()