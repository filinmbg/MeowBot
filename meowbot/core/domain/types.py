from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .enums import Side, SignalAction, TradeStatus


@dataclass(frozen=True)
class Bar:
    symbol: str
    tf: str
    open_time: int
    close_time: int
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

    mode: str = "sandbox"
    execution_engine: str = "paper"
    exchange_name: Optional[str] = None
    strategy_version: str = "v1"
    subscription_type: Optional[str] = None
    exit_profile: Optional[Dict[str, Any]] = None
    signal_level: Optional[str] = None
    signal_score: Optional[int] = None
    position_size_multiplier: float = 1.0
    signal_debug: Optional[Dict[str, Any]] = None

    tp_hit_count: int = 0
    tp_count: int = 0
    tp_levels: list[float] = field(default_factory=list)
    tp_close_fractions: list[float] = field(default_factory=list)
    tp_plan: list[Dict[str, Any]] = field(default_factory=list)
    tp_order_ids: list[str] = field(default_factory=list)
    tp_algo_ids: list[str] = field(default_factory=list)
    remaining_pct: float = 1.0
    exit_last_check_at: int = 0
    last_replayed_candle_close: Optional[int] = None
    replay_slow_count: int = 0
    next_replay_at: Optional[int] = None
    entry_indicators: Optional[Dict[str, Any]] = None

    qty_requested: Optional[float] = None
    qty_filled: Optional[float] = None
    qty_remaining: Optional[float] = None

    closed_at: Optional[int] = None
    close_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl_usd: float = 0.0
    local_realized_pnl_usd: Optional[float] = None
    local_realized_pnl_usdt: Optional[float] = None

    exchange_entry_order_id: Optional[str] = None
    exchange_entry_client_order_id: Optional[str] = None
    exchange_entry_status: Optional[str] = None
    exchange_avg_entry_price: Optional[float] = None
    exchange_position_mode: Optional[str] = None
    exchange_position_side: Optional[str] = None

    exchange_stop_order: Optional[Dict[str, Any]] = None
    exchange_tp_orders: list[Dict[str, Any]] = field(default_factory=list)
    exchange_order_ids: list[str] = field(default_factory=list)
    exchange_fill_ids: list[str] = field(default_factory=list)

    exchange_last_event_ts: Optional[int] = None
    exchange_last_sync_at: Optional[int] = None
    exchange_last_sync_reason: Optional[str] = None
    exchange_sync_status: Optional[str] = None
    exchange_sync_error: Optional[str] = None
    exchange_position_amt: Optional[float] = None
    exchange_realized_pnl_usd: Optional[float] = None
    exchange_realized_pnl_usdt: Optional[float] = None
    exchange_gross_realized_pnl_usd: Optional[float] = None
    exchange_gross_realized_pnl_usdt: Optional[float] = None
    exchange_net_realized_pnl_usd: Optional[float] = None
    exchange_net_realized_pnl_usdt: Optional[float] = None
    pnl_source: str = "local"
    exchange_commission_usd: Optional[float] = None
    exchange_commission_usdt: Optional[float] = None
    exchange_close_order_id: Optional[str] = None
    exchange_close_client_order_id: Optional[str] = None
    exchange_close_time: Optional[int] = None
    pnl_verified_at: Optional[int] = None
    pnl_status: Optional[str] = None
    last_income_sync_at: Optional[int] = None
    income_sync_completed: bool = False
    income_sync_attempts: int = 0
    income_sync_next_retry_at: Optional[int] = None
    income_sync_error: Optional[str] = None
    matched_income_records: list[Dict[str, Any]] = field(default_factory=list)
    matched_commission_records: list[Dict[str, Any]] = field(default_factory=list)
    exchange_position_confirmed_flat: bool = False
    cleanup_completed: bool = False
    cleanup_completed_at: Optional[int] = None
    soft_stop_enabled: bool = False
    soft_stop_activated_at: Optional[int] = None
    soft_stop_current_pct: Optional[float] = None
    soft_stop_last_raise_at: Optional[int] = None
    soft_stop_next_raise_at: Optional[int] = None
    soft_stop_trigger_price: Optional[float] = None
    exchange_safety_sl_price: Optional[float] = None
    protection_status: str = "protected"
    protection_error: Optional[str] = None
    protection_details: Optional[Dict[str, Any]] = None
    entry_notification_sent: bool = False
    entry_notification_sent_at: Optional[int] = None
    entry_notification_recovery_queued_at: Optional[int] = None
    entry_notification_last_send_attempt_at: Optional[int] = None
    entry_notification_send_attempt_count: int = 0
    entry_notification_last_send_exception: Optional[str] = None
    risk_warning_sent: bool = False
    risk_warning_sent_at: Optional[int] = None

    def __post_init__(self) -> None:
        strategy_version = str(self.strategy_version or "v1").strip().lower()
        self.strategy_version = strategy_version if strategy_version in {"v1", "v2"} else "v1"
        if self.subscription_type is not None:
            self.subscription_type = str(self.subscription_type).strip().lower() or None
        if self.exit_profile is not None and not isinstance(self.exit_profile, dict):
            self.exit_profile = None
        if self.signal_level is not None:
            signal_level = str(self.signal_level).strip().lower()
            self.signal_level = signal_level if signal_level in {"weak", "medium", "strong"} else None
        if self.signal_score is not None:
            try:
                self.signal_score = int(self.signal_score)
            except (TypeError, ValueError):
                self.signal_score = None
        try:
            self.position_size_multiplier = float(self.position_size_multiplier or 1.0)
        except (TypeError, ValueError):
            self.position_size_multiplier = 1.0
        if self.position_size_multiplier <= 0:
            self.position_size_multiplier = 1.0
        if self.signal_debug is not None and not isinstance(self.signal_debug, dict):
            self.signal_debug = None
        if self.qty_requested is None:
            self.qty_requested = self.qty
        if self.qty_filled is None:
            self.qty_filled = self.qty
        if self.qty_remaining is None:
            self.qty_remaining = self.qty_filled
        if self.exchange_avg_entry_price is None:
            self.exchange_avg_entry_price = self.entry_price
        if self.local_realized_pnl_usd is None:
            self.local_realized_pnl_usd = (
                float(self.local_realized_pnl_usdt)
                if self.local_realized_pnl_usdt is not None
                else float(self.realized_pnl_usd or 0.0)
            )
        if self.local_realized_pnl_usdt is None:
            self.local_realized_pnl_usdt = self.local_realized_pnl_usd
        if self.exchange_realized_pnl_usd is None and self.exchange_realized_pnl_usdt is not None:
            self.exchange_realized_pnl_usd = float(self.exchange_realized_pnl_usdt)
        if self.exchange_realized_pnl_usdt is None:
            self.exchange_realized_pnl_usdt = self.exchange_realized_pnl_usd
        if self.exchange_gross_realized_pnl_usd is None and self.exchange_gross_realized_pnl_usdt is not None:
            self.exchange_gross_realized_pnl_usd = float(self.exchange_gross_realized_pnl_usdt)
        if self.exchange_gross_realized_pnl_usdt is None:
            self.exchange_gross_realized_pnl_usdt = self.exchange_gross_realized_pnl_usd
        if self.exchange_net_realized_pnl_usd is None and self.exchange_net_realized_pnl_usdt is not None:
            self.exchange_net_realized_pnl_usd = float(self.exchange_net_realized_pnl_usdt)
        if self.exchange_net_realized_pnl_usdt is None:
            self.exchange_net_realized_pnl_usdt = self.exchange_net_realized_pnl_usd
        if self.exchange_net_realized_pnl_usd is None and self.exchange_realized_pnl_usd is not None:
            self.exchange_net_realized_pnl_usd = self.exchange_realized_pnl_usd
            self.exchange_net_realized_pnl_usdt = self.exchange_realized_pnl_usd
        if self.exchange_commission_usd is None and self.exchange_commission_usdt is not None:
            self.exchange_commission_usd = float(self.exchange_commission_usdt)
        if self.exchange_commission_usdt is None:
            self.exchange_commission_usdt = self.exchange_commission_usd
        pnl_source = str(self.pnl_source or "local").strip().lower()
        self.pnl_source = pnl_source if pnl_source in {"local", "exchange"} else "local"
        if self.pnl_status is not None:
            self.pnl_status = str(self.pnl_status).strip().lower() or None
        self.income_sync_completed = bool(self.income_sync_completed)
        try:
            self.income_sync_attempts = max(0, int(self.income_sync_attempts or 0))
        except (TypeError, ValueError):
            self.income_sync_attempts = 0
        if self.income_sync_error is not None:
            self.income_sync_error = str(self.income_sync_error)
        if not isinstance(self.matched_income_records, list):
            self.matched_income_records = []
        if not isinstance(self.matched_commission_records, list):
            self.matched_commission_records = []
        self.exchange_position_confirmed_flat = bool(self.exchange_position_confirmed_flat)
        self.cleanup_completed = bool(self.cleanup_completed)
        self.soft_stop_enabled = bool(self.soft_stop_enabled)
        if self.soft_stop_current_pct is not None:
            self.soft_stop_current_pct = float(self.soft_stop_current_pct)
        if self.soft_stop_trigger_price is not None:
            self.soft_stop_trigger_price = float(self.soft_stop_trigger_price)
        if self.exchange_safety_sl_price is not None:
            self.exchange_safety_sl_price = float(self.exchange_safety_sl_price)
        self.entry_notification_sent = bool(self.entry_notification_sent)
        self.risk_warning_sent = bool(self.risk_warning_sent)
        try:
            self.replay_slow_count = max(0, int(self.replay_slow_count or 0))
        except (TypeError, ValueError):
            self.replay_slow_count = 0
        if self.last_replayed_candle_close is not None:
            try:
                self.last_replayed_candle_close = int(self.last_replayed_candle_close)
            except (TypeError, ValueError):
                self.last_replayed_candle_close = None
        if self.next_replay_at is not None:
            try:
                self.next_replay_at = int(self.next_replay_at)
            except (TypeError, ValueError):
                self.next_replay_at = None
        try:
            self.entry_notification_send_attempt_count = max(0, int(self.entry_notification_send_attempt_count or 0))
        except (TypeError, ValueError):
            self.entry_notification_send_attempt_count = 0
        if self.entry_notification_last_send_exception is not None:
            self.entry_notification_last_send_exception = str(self.entry_notification_last_send_exception)

    @property
    def is_open(self) -> bool:
        return self.status == TradeStatus.OPEN

    @property
    def tp1_hit(self) -> bool:
        return int(self.tp_hit_count or 0) >= 1

    @property
    def tp2_hit(self) -> bool:
        return int(self.tp_hit_count or 0) >= 2

    @property
    def tp3_hit(self) -> bool:
        return int(self.tp_hit_count or 0) >= 3

    @property
    def closed(self) -> bool:
        return self.status == TradeStatus.CLOSED
