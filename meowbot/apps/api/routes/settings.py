from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from meowbot.apps.api.schemas.settings import (
    NotificationPreferencesResponse,
    TraderSettingsResponse,
)
from meowbot.infra.postgres.repos.user_defaults_repo import UserDefaultsRepo


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/trader/{user_id}", response_model=TraderSettingsResponse)
async def get_trader_settings(user_id: UUID, request: Request) -> TraderSettingsResponse:
    repo = UserDefaultsRepo(request.app.state.pg)
    row = await repo.get_trader_settings(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Trader settings not found")
    return TraderSettingsResponse(**row)


@router.get("/notifications/{user_id}", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(user_id: UUID, request: Request) -> NotificationPreferencesResponse:
    repo = UserDefaultsRepo(request.app.state.pg)
    row = await repo.get_notification_preferences(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification preferences not found")
    return NotificationPreferencesResponse(**row)