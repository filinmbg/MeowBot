import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from database.supabase.supabase_connector import supabase

def test_supabase():
    try:
        response = supabase.table("trades").select("*").limit(1).execute()
        print("✅ Supabase test passed.")
        print("Result:", response.data)
    except Exception as e:
        print("❌ Supabase test failed:", e)

if __name__ == "__main__":
    test_supabase()