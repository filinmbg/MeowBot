from __future__ import annotations

from fastapi import APIRouter, Request, status

from meowbot.apps.api.schemas.telegram_auth import TelegramLoginRequest, TelegramUserResponse
from meowbot.core.usecases.get_or_create_user_by_telegram import GetOrCreateUserByTelegramUseCase
from meowbot.infra.postgres.repos.telegram_auth_repo import TelegramAuthRepo


router = APIRouter(prefix="/auth/telegram", tags=["auth-telegram"])


@router.post("/get-or-create", response_model=TelegramUserResponse, status_code=status.HTTP_200_OK)
async def get_or_create_user_by_telegram(
    payload: TelegramLoginRequest,
    request: Request,
) -> TelegramUserResponse:
    repo = TelegramAuthRepo(request.app.state.pg)
    usecase = GetOrCreateUserByTelegramUseCase(repo)

    user, is_new_user = await usecase.execute(
        telegram_user_id=payload.telegram_user_id,
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
        language_code=payload.language_code,
    )

    return TelegramUserResponse(**user, is_new_user=is_new_user)