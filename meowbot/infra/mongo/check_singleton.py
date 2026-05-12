from __future__ import annotations

import asyncio
import logging
import os

from meowbot.infra.mongo.async_client import (
    AsyncMongoConfig,
    close_async_mongo_client,
    get_async_mongo_client,
    get_async_mongo_db,
)
from meowbot.infra.mongo.repos_async.trade_events_repo_async import (
    TradeEventsRepositoryMongoAsync,
)
from meowbot.infra.mongo.repos_async.trades_repo_async import TradesRepositoryMongoAsync


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meowbot")


async def main() -> None:
    config = AsyncMongoConfig()

    try:
        first = await get_async_mongo_client(config)
        second = await get_async_mongo_client(config)
        if first is not second:
            raise RuntimeError("Mongo singleton check failed: clients are different objects")

        db = await get_async_mongo_db(config)
        trades_repo = TradesRepositoryMongoAsync(db)
        trade_events_repo = TradeEventsRepositoryMongoAsync(db)

        log.info(
            "[mongo-check] singleton ok pid=%s client_id=%s db=%s trades_collection=%s events_collection=%s",
            os.getpid(),
            id(first),
            config.db_name,
            trades_repo.col.name,
            trade_events_repo.col.name,
        )
    finally:
        await close_async_mongo_client()


if __name__ == "__main__":
    asyncio.run(main())
