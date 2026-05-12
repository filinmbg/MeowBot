import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

dsn = os.environ.get("PG_DSN") or os.environ.get("POSTGRES_URL")

if not dsn:
    raise ValueError("PG_DSN or POSTGRES_URL is not set")

conn = psycopg2.connect(dsn)
cur = conn.cursor()


def run_sql_file(path: str) -> None:
    print(f"Running {path}...")
    with open(path, "r", encoding="utf-8") as f:
        cur.execute(f.read())


try:
    run_sql_file("meowbot/infra/postgres/sql/alter_strategy_version_v18.sql")
    run_sql_file("meowbot/infra/postgres/sql/seed_strategy_version_test_users.sql")
    conn.commit()
    print("Migrations applied")
except Exception:
    conn.rollback()
    raise
finally:
    cur.close()
    conn.close()