from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from meowbot.core.configs.trading_plan_limits import (
    MAX_OPEN_TRADES_PER_SYMBOL,
    PLAN_TRADE_LIMITS,
    PlanTradeLimits,
    RISK_MAX_ACTIVE_TRADES,
    normalize_plan_code,
)
from meowbot.core.domain.enums import TradeStatus
from meowbot.core.domain.types import Trade


@dataclass(frozen=True)
class TradeOpenCheckResult:
    allowed: bool
    reason: str
    current_open_trades: int = 0
    current_risk_trades: int = 0
    max_open_trades_total: int | None = None
    max_open_trades_per_symbol: int = MAX_OPEN_TRADES_PER_SYMBOL
    max_risk_trades: int = RISK_MAX_ACTIVE_TRADES
    allowed_symbols_count: int | None = None


class TradeManager:
    def __init__(
        self,
        *,
        risk_max_active_trades: int = RISK_MAX_ACTIVE_TRADES,
        plan_trade_limits: dict[str, PlanTradeLimits] | None = None,
    ) -> None:
        self.risk_max_active_trades = int(risk_max_active_trades)
        self.plan_trade_limits = plan_trade_limits or PLAN_TRADE_LIMITS

    def evaluate_new_trade(
        self,
        *,
        user_id: str,
        symbol: str,
        open_trades: Iterable[Trade],
        plan_code: str | None = None,
        plan_features: dict[str, Any] | str | None = None,
        allowed_symbols: Sequence[str] | None = None,
        enabled_symbols: Sequence[str] | None = None,
        max_open_trades_total: int | None = None,
        max_open_trades_per_symbol: int | None = None,
        max_risk_trades: int | None = None,
    ) -> TradeOpenCheckResult:
        trades = list(open_trades)
        normalized_symbol = str(symbol).upper()
        risk_limit = self._resolve_risk_limit(max_risk_trades)
        whitelist = self._build_effective_whitelist(
            plan_code=plan_code,
            plan_features=plan_features,
            allowed_symbols=allowed_symbols,
            enabled_symbols=enabled_symbols,
        )

        if whitelist and normalized_symbol not in whitelist:
            return TradeOpenCheckResult(
                allowed=False,
                reason="symbol_not_allowed",
                current_open_trades=self.count_open_trades(user_id=user_id, open_trades=trades),
                current_risk_trades=self.count_risk_trades(user_id=user_id, open_trades=trades),
                max_open_trades_total=self._resolve_total_limit(
                    plan_code=plan_code,
                    plan_features=plan_features,
                    fallback_max_open_trades_total=max_open_trades_total,
                ),
                max_open_trades_per_symbol=self._resolve_per_symbol_limit(
                    plan_code=plan_code,
                    plan_features=plan_features,
                    fallback_per_symbol_limit=max_open_trades_per_symbol,
                ),
                max_risk_trades=risk_limit,
                allowed_symbols_count=len(whitelist),
            )

        same_symbol_limit = self._resolve_per_symbol_limit(
            plan_code=plan_code,
            plan_features=plan_features,
            fallback_per_symbol_limit=max_open_trades_per_symbol,
        )
        same_symbol_open = self.count_open_trades_for_symbol(
            user_id=user_id,
            symbol=normalized_symbol,
            open_trades=trades,
        )
        if same_symbol_open >= same_symbol_limit:
            return TradeOpenCheckResult(
                allowed=False,
                reason="open_trade_exists_for_symbol",
                current_open_trades=self.count_open_trades(user_id=user_id, open_trades=trades),
                current_risk_trades=self.count_risk_trades(user_id=user_id, open_trades=trades),
                max_open_trades_total=self._resolve_total_limit(
                    plan_code=plan_code,
                    plan_features=plan_features,
                    fallback_max_open_trades_total=max_open_trades_total,
                ),
                max_open_trades_per_symbol=same_symbol_limit,
                max_risk_trades=risk_limit,
                allowed_symbols_count=len(whitelist) if whitelist else None,
            )

        open_count = self.count_open_trades(user_id=user_id, open_trades=trades)
        total_limit = self._resolve_total_limit(
            plan_code=plan_code,
            plan_features=plan_features,
            fallback_max_open_trades_total=max_open_trades_total,
        )
        if total_limit is not None and open_count >= total_limit:
            return TradeOpenCheckResult(
                allowed=False,
                reason="max_open_trades_total_exceeded",
                current_open_trades=open_count,
                current_risk_trades=self.count_risk_trades(user_id=user_id, open_trades=trades),
                max_open_trades_total=total_limit,
                max_open_trades_per_symbol=same_symbol_limit,
                max_risk_trades=risk_limit,
                allowed_symbols_count=len(whitelist) if whitelist else None,
            )

        risk_trades = self.count_risk_trades(user_id=user_id, open_trades=trades)
        if risk_trades >= risk_limit:
            return TradeOpenCheckResult(
                allowed=False,
                reason="risk_limit_reached",
                current_open_trades=open_count,
                current_risk_trades=risk_trades,
                max_open_trades_total=total_limit,
                max_open_trades_per_symbol=same_symbol_limit,
                max_risk_trades=risk_limit,
                allowed_symbols_count=len(whitelist) if whitelist else None,
            )

        return TradeOpenCheckResult(
            allowed=True,
            reason="ok",
            current_open_trades=open_count,
            current_risk_trades=risk_trades,
            max_open_trades_total=total_limit,
            max_open_trades_per_symbol=same_symbol_limit,
            max_risk_trades=risk_limit,
            allowed_symbols_count=len(whitelist) if whitelist else None,
        )

    def count_open_trades(self, *, user_id: str, open_trades: Iterable[Trade]) -> int:
        return sum(
            1
            for trade in open_trades
            if self._trade_user_id(trade) == user_id and self._is_open_trade(trade)
        )

    def count_risk_trades(self, *, user_id: str, open_trades: Iterable[Trade]) -> int:
        return sum(
            1
            for trade in open_trades
            if self._trade_user_id(trade) == user_id
            and self._is_open_trade(trade)
            and not self._has_tp1_hit(trade)
        )

    def count_open_trades_for_symbol(
        self,
        *,
        user_id: str,
        symbol: str,
        open_trades: Iterable[Trade],
    ) -> int:
        normalized_symbol = str(symbol).upper()
        return sum(
            1
            for trade in open_trades
            if self._trade_user_id(trade) == user_id
            and self._is_open_trade(trade)
            and str(getattr(trade, "symbol", "")).upper() == normalized_symbol
        )

    def _resolve_total_limit(
        self,
        *,
        plan_code: str | None,
        plan_features: dict[str, Any] | str | None,
        fallback_max_open_trades_total: int | None,
    ) -> int | None:
        configured = self.plan_trade_limits.get(normalize_plan_code(plan_code))
        if configured is not None:
            return configured.max_open_trades_total

        features = self._normalize_features(plan_features)
        if "max_open_trades_total" in features:
            return self._to_optional_int(features.get("max_open_trades_total"))

        return self._to_optional_int(fallback_max_open_trades_total)

    def _resolve_per_symbol_limit(
        self,
        *,
        plan_code: str | None,
        plan_features: dict[str, Any] | str | None,
        fallback_per_symbol_limit: int | None,
    ) -> int:
        configured = self.plan_trade_limits.get(normalize_plan_code(plan_code))
        if configured is not None:
            return int(configured.max_open_trades_per_symbol)

        features = self._normalize_features(plan_features)
        feature_limit = self._to_optional_int(features.get("max_open_trades_per_symbol"))
        if feature_limit is not None:
            return int(feature_limit)

        fallback = self._to_optional_int(fallback_per_symbol_limit)
        if fallback is not None:
            return int(fallback)

        return MAX_OPEN_TRADES_PER_SYMBOL

    def _resolve_max_symbols(
        self,
        *,
        plan_code: str | None,
        plan_features: dict[str, Any] | str | None,
    ) -> int | None:
        configured = self.plan_trade_limits.get(normalize_plan_code(plan_code))
        if configured is not None:
            return configured.max_symbols

        features = self._normalize_features(plan_features)
        return self._to_optional_int(features.get("max_symbols"))

    def _build_effective_whitelist(
        self,
        *,
        plan_code: str | None,
        plan_features: dict[str, Any] | str | None,
        allowed_symbols: Sequence[str] | None,
        enabled_symbols: Sequence[str] | None,
    ) -> list[str]:
        features = self._normalize_features(plan_features)
        if self._allows_all_symbols(features):
            return []

        ordered_allowed = self._normalize_symbols(allowed_symbols or ())
        ordered_enabled = self._normalize_symbols(enabled_symbols or ())

        if ordered_allowed and ordered_enabled:
            enabled_set = set(ordered_enabled)
            whitelist = [symbol for symbol in ordered_allowed if symbol in enabled_set]
        elif ordered_allowed:
            whitelist = ordered_allowed
        else:
            whitelist = ordered_enabled

        max_symbols = self._resolve_max_symbols(
            plan_code=plan_code,
            plan_features=plan_features,
        )
        if max_symbols is not None and max_symbols >= 0:
            return whitelist[:max_symbols]

        return whitelist

    def _allows_all_symbols(self, features: dict[str, Any]) -> bool:
        if bool(features.get("can_trade_all_symbols")):
            return True
        allowed_symbols = features.get("allowed_symbols")
        if isinstance(allowed_symbols, str):
            return allowed_symbols.strip().lower() == "all"
        return False

    def _normalize_symbols(self, symbols: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            normalized.append(symbol)
            seen.add(symbol)
        return normalized

    def _normalize_features(self, features: dict[str, Any] | str | None) -> dict[str, Any]:
        if isinstance(features, dict):
            return features
        if isinstance(features, str):
            try:
                parsed = json.loads(features)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _to_optional_int(self, value: Any) -> int | None:
        if value in (None, "", "null"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_risk_limit(self, value: Any) -> int:
        parsed = self._to_optional_int(value)
        if parsed is None:
            return self.risk_max_active_trades
        return max(int(parsed), 1)

    def _trade_user_id(self, trade: Trade) -> str:
        return str(getattr(trade, "user_id", ""))

    def _is_open_trade(self, trade: Trade) -> bool:
        status = getattr(trade, "status", None)
        if isinstance(status, TradeStatus):
            return status == TradeStatus.OPEN
        return str(status) == getattr(TradeStatus.OPEN, "value", "OPEN")

    def _has_tp1_hit(self, trade: Trade) -> bool:
        tp1_hit = getattr(trade, "tp1_hit", None)
        if isinstance(tp1_hit, bool):
            return tp1_hit
        try:
            return int(getattr(trade, "tp_hit_count", 0) or 0) >= 1
        except (TypeError, ValueError):
            return False
