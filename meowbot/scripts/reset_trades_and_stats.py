from __future__ import annotations

import asyncio

from meowbot.infra.mongo.async_client import AsyncMongoConfig, AsyncMongoConn


async def main() -> None:
    mongo = AsyncMongoConn(AsyncMongoConfig())
    await mongo.connect()

    try:
        collections = [
            "trades",
            "trade_events",
            "runtime_logs",
            "telegram_delivery_logs",
        ]

        for name in collections:
            result = await mongo.db[name].delete_many({})
            print(f"{name}: deleted {result.deleted_count}")
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())