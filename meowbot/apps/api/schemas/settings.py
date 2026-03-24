from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TraderSettingsResponse(BaseModel):
    user_id: UUID
    enabled: bool
    mode: str
    entry_mode: str
    entry_value: float
    risk_profile: str
    allow_long: bool
    allow_short: bool
    night_mode: bool
    quiet_hours_from: int | None
    quiet_hours_to: int | None
    max_open_trades: int
    max_daily_loss: float | None
    max_trades_per_day: int | None
    current_exchange_account_id: UUID | None
    created_at: datetime
    updated_at: datetime


class NotificationPreferencesResponse(BaseModel):
    user_id: UUID
    telegram_enabled: bool
    email_enabled: bool
    trade_alerts: bool
    tp_alerts: bool
    sl_alerts: bool
    daily_report: bool
    weekly_report: bool
    system_alerts: bool
    marketing_alerts: bool
    critical_night_override: bool
    created_at: datetime
    updated_at: datetime