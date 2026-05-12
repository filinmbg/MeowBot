from __future__ import annotations

import asyncio
from pymongo.errors import NetworkTimeout

from meowbot.apps.telegram_bot import handlers_trades
from meowbot.apps.telegram_bot.menu_support import get_user_trade_rows


class CapturingTradesRepo:
    def __init__(self) -> None:
        self.limit: int | None = None

    async def get_trade_rows_by_user(self, *, user_id: str, mode: str | None, limit: int):
        self.limit = limit
        return []


class TimeoutTradesRepo:
    async def get_trade_rows_by_user(self, *, user_id: str, mode: str | None, limit: int):
        raise NetworkTimeout("temporary timeout")


def test_menu_support_caps_trade_rows_limit():
    asyncio.run(_run_menu_support_caps_trade_rows_limit())


async def _run_menu_support_caps_trade_rows_limit():
    repo = CapturingTradesRepo()

    rows = await get_user_trade_rows(repo, user_id="tg:1", mode="sandbox", limit=10_000)

    assert rows == []
    assert repo.limit == 100


def test_trade_rows_ui_timeout_returns_fallback(monkeypatch):
    asyncio.run(_run_trade_rows_ui_timeout_returns_fallback(monkeypatch))


async def _run_trade_rows_ui_timeout_returns_fallback(monkeypatch):
    monkeypatch.setattr(handlers_trades, "MONGO_UI_RETRY_DELAY_SECONDS", 0.0)

    rows, failed = await handlers_trades._get_user_trade_rows_for_ui(
        TimeoutTradesRepo(),
        user_id="tg:1",
        mode="sandbox",
        screen="stats",
    )

    assert rows == []
    assert failed is True
