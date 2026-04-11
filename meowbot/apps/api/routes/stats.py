from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from meowbot.core.usecases.get_paper_trade_stats import GetPaperTradeStatsUseCase
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo


router = APIRouter(prefix="/stats", tags=["stats"])


def get_usecase(request: Request) -> GetPaperTradeStatsUseCase:
    mongo = request.app.state.mongo
    repo = TradesRepositoryMongo(mongo.db)
    return GetPaperTradeStatsUseCase(repo)


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
def get_test_users_stats(
    request: Request,
    symbol: str | None = None,
    tf: str | None = None,
    model_id: str | None = None,
    uc: GetPaperTradeStatsUseCase = Depends(get_usecase),
):
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