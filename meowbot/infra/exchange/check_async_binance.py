from __future__ import annotations

import asyncio

from meowbot.infra.exchange.binance_futures_usdtm_async import (
    BinanceFuturesAsyncConfig,
    BinanceFuturesMarketDataAsync,
)


async def main() -> None:
    client = BinanceFuturesMarketDataAsync(BinanceFuturesAsyncConfig())
    try:
        ok = await client.ping()
        print("✅ Async Binance ping successful" if ok else "❌ Async Binance ping failed")

        bars = await client.fetch_klines(
            symbol="BTCUSDT",
            tf="15m",
            start_ms=None,
            end_ms=None,
            limit=5,
        )
        print(f"Fetched bars: {len(bars)}")
        if bars:
            print(f"Last close_time: {bars[-1].close_time}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())