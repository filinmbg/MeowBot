from __future__ import annotations

import asyncio

from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn


COLLECTIONS_TO_CLEAR = [
    "telegram_users",
    "trades",
    "trade_events",
    "bot_state",
]


async def main() -> None:
    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    try:
        for name in COLLECTIONS_TO_CLEAR:
            result = await mongo.db[name].delete_many({})
            print(f"✅ {name}: deleted {result.deleted_count}")
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())