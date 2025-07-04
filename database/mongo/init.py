from motor.motor_asyncio import AsyncIOMotorClient
from os import getenv
from dotenv import load_dotenv

load_dotenv()

client = None
db = None

def init_mongo():
    global client, db
    mongo_uri = getenv("MONGODB_URI")
    db_name = getenv("MONGODB_DB")
    client = AsyncIOMotorClient(mongo_uri)
    db = client[db_name]

def get_collection(name: str):
    if db is None:
        raise Exception("MongoDB not initialized")
    return db[name]
