from __future__ import annotations

from meowbot.infra.mongo.client import MongoConn, MongoConfig


def main() -> None:
    mongo = MongoConn(MongoConfig())
    try:
        mongo.connect()
        ok = mongo.ping()
        print("✅ Mongo connection successful" if ok else "❌ Mongo ping failed")
        print(f"DB name: {mongo.config.db_name}")
    finally:
        mongo.close()


if __name__ == "__main__":
    main()