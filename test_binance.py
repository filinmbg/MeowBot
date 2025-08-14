import asyncio
from binance_connector.binance_conn import init_binance, get_balances_nonzero, is_connected

async def main():
    await init_binance()  # читає ключі з .env
    print("connected:", is_connected())
    bals = await get_balances_nonzero()
    print("balances:", bals)

asyncio.run(main())
