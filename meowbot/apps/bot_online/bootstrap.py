from __future__ import annotations

from meowbot.core.usecases.get_or_create_user_by_telegram import GetOrCreateUserByTelegramUseCase
from meowbot.infra.postgres.repos.telegram_auth_repo import TelegramAuthRepo


def build_get_or_create_user_by_telegram_usecase(pg_pool) -> GetOrCreateUserByTelegramUseCase:
    repo = TelegramAuthRepo(pg_pool)
    return GetOrCreateUserByTelegramUseCase(repo)