from __future__ import annotations

import argparse
from pprint import pprint

from meowbot.infra.mongo.client import MongoConn, MongoConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Виконати реальне видалення")
    parser.add_argument("--user-id", default="demo_user", help="Sandbox/test user id")
    parser.add_argument("--features-ver", default="v2_core", help="Версія features для cursor-keys")
    args = parser.parse_args()

    mongo = MongoConn(MongoConfig())
    mongo.connect()

    try:
        db = mongo.db

        trades_col = db["trades"]
        trade_events_col = db["trade_events"]
        bot_state_col = db["bot_state"]

        trades_query = {
            "$or": [
                {"mode": "sandbox"},
                {"user_id": args.user_id},
            ]
        }

        trade_events_query = {
            "$or": [
                {"mode": "sandbox"},
                {"user_id": args.user_id},
                {"trade_id": {"$regex": f"^{args.user_id}:"}},
            ]
        }

        bot_state_query = {
            "$or": [
                {"key": {"$regex": r"^entry_cursor:"}},
                {"key": {"$regex": r"^exit_cursor:"}},
                {"key": {"$regex": r"^reconcile:"}},
            ]
        }

        snapshot = {
            "trades_to_delete": trades_col.count_documents(trades_query),
            "trade_events_to_delete": trade_events_col.count_documents(trade_events_query),
            "bot_state_to_delete": bot_state_col.count_documents(bot_state_query),
        }

        print("\n=== DRY RUN SNAPSHOT ===")
        pprint(snapshot)

        if not args.execute:
            print("\nНічого не видалено. Для реального скидання запусти з --execute")
            return

        trades_result = trades_col.delete_many(trades_query)
        events_result = trade_events_col.delete_many(trade_events_query)
        bot_state_result = bot_state_col.delete_many(bot_state_query)

        print("\n=== RESET DONE ===")
        pprint(
            {
                "deleted_trades": trades_result.deleted_count,
                "deleted_trade_events": events_result.deleted_count,
                "deleted_bot_state": bot_state_result.deleted_count,
            }
        )

    finally:
        mongo.close()


if __name__ == "__main__":
    main()