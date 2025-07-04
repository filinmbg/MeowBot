# exchange/price_provider.py

import aiohttp

session = aiohttp.ClientSession()

async def get_price(symbol: str) -> float:
    # Тут приклад-заглушка
    return 106000.0
