from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TelegramLoginRequest(BaseModel):
    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = "uk"


class TelegramUserResponse(BaseModel):
    id: UUID
    email: str | None
    status: str
    role: str
    timezone: str
    language_code: str
    display_name: str | None
    created_at: datetime
    updated_at: datetime

    telegram_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    last_interaction_at: datetime

    is_new_user: bool