import requests
import aiohttp
from datetime import datetime, time
from .rate_limiter import RateLimiter

BINANCE_URL = 'https://fapi.binance.com/fapi/v1/klines'
rate_limiter = RateLimiter(max_requests_per_minute=1100)

def get_klines(symbol: str, interval: str, start_time: datetime, limit=1500):
    rate_limiter.wait()
    params = {
        'symbol': symbol,
        'interval': interval,
        'startTime': int(start_time.timestamp() * 1000),
        'limit': limit
    }
    while True:
        try:
            response = requests.get(BINANCE_URL, params=params)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                print("Rate limit exceeded, retrying...")
                time.sleep(2)
            else:
                print(f"Error {response.status_code}: {response.text}")
                time.sleep(1)
        except Exception as e:
            print("Request error:", e)
            time.sleep(2)

async def get_price(symbol: str) -> float:
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    params = {"symbol": symbol}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            return float(data['price'])