from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from meowbot.core.usecases.get_paper_trade_stats import GetPaperTradeStatsUseCase
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo


router = APIRouter(prefix="/stats", tags=["stats"])


def get_usecase(request: Request) -> GetPaperTradeStatsUseCase:
    mongo = request.app.state.mongo
    repo = TradesRepositoryMongo(mongo.db)
    return GetPaperTradeStatsUseCase(repo)


def get_usecase_for_test_users(
    request: Request,
    *,
    test_user_ids: set[str],
) -> GetPaperTradeStatsUseCase:
    mongo = request.app.state.mongo
    repo = TradesRepositoryMongo(mongo.db)
    return GetPaperTradeStatsUseCase(repo, test_user_ids=test_user_ids)


async def resolve_test_user_ids(request: Request) -> set[str]:
    explicit_ids = {
        x.strip()
        for x in os.getenv("TEST_USER_IDS", "").split(",")
        if x.strip()
    }
    if explicit_ids:
        return explicit_ids

    emails = [
        x.strip().lower()
        for x in os.getenv("TEST_USER_EMAILS", "").split(",")
        if x.strip()
    ]
    if not emails:
        return {"demo_user"}

    pool = request.app.state.pg
    query = """
    select ('tg:' || tp.telegram_id::text) as runtime_user_id
    from users u
    join telegram_profiles tp
        on tp.user_id = u.id
    where lower(u.email) = any($1::text[])
    order by array_position($1::text[], lower(u.email))
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, emails)

    runtime_ids = {
        str(row["runtime_user_id"])
        for row in rows
        if row["runtime_user_id"]
    }
    return runtime_ids or {"demo_user"}


@router.get("/global")
def get_global_stats(
    request: Request,
    symbol: str | None = None,
    tf: str | None = None,
    model_id: str | None = None,
    uc: GetPaperTradeStatsUseCase = Depends(get_usecase),
):
    return uc.get_global_stats(
        symbol=symbol,
        tf=tf,
        model_id=model_id,
    )


@router.get("/test-users")
async def get_test_users_stats(
    request: Request,
    symbol: str | None = None,
    tf: str | None = None,
    model_id: str | None = None,
):
    test_user_ids = await resolve_test_user_ids(request)
    uc = get_usecase_for_test_users(request, test_user_ids=test_user_ids)
    return uc.get_test_user_stats(
        symbol=symbol,
        tf=tf,
        model_id=model_id,
    )


@router.get("/user/{user_id}")
def get_user_stats(
    request: Request,
    user_id: str,
    symbol: str | None = None,
    tf: str | None = None,
    model_id: str | None = None,
    uc: GetPaperTradeStatsUseCase = Depends(get_usecase),
):
    return uc.get_user_stats(
        user_id=user_id,
        symbol=symbol,
        tf=tf,
        model_id=model_id,
    )
