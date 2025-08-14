import asyncio
from dotenv import load_dotenv
from database.mongo.connection import init_mongo
from database.mongo.symbols import add_symbol

async def main():
    load_dotenv()
    await init_mongo()
    await add_symbol("BTCUSDT")

if __name__ == "__main__":
    asyncio.run(main())
