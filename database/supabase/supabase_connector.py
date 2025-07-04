import os
from dotenv import load_dotenv
from core.logger import logger
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")

supabase: Client = None

async def init_supabase():
    global supabase
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Connected to Supabase")
    except Exception as e:
        logger.error(f"❌ Supabase connection error: {e}")
        raise e

def get_supabase():
    global supabase
    return supabase
