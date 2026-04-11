from __future__ import annotations

from pprint import pprint

from meowbot.infra.mongo.client import MongoConn, MongoConfig


def main() -> None:
    mongo = MongoConn(MongoConfig())
    mongo.connect()

    try:
        db = mongo.db

        trades_col = db["trades"]
        trade_events_col = db["trade_events"]
        bot_state_col = db["bot_state"]

        result = {
            "sandbox_trades": trades_col.count_documents({"mode": "sandbox"}),
            "demo_user_trades": trades_col.count_documents({"user_id": "demo_user"}),
            "sandbox_trade_events": trade_events_col.count_documents({"mode": "sandbox"}),
            "demo_user_trade_events": trade_events_col.count_documents({"user_id": "demo_user"}),
            "entry_cursors": bot_state_col.count_documents({"key": {"$regex": r"^entry_cursor:"}}),
            "exit_related_state": bot_state_col.count_documents(
                {
                    "$or": [
                        {"key": {"$regex": r"^exit_cursor:"}},
                        {"key": {"$regex": r"^reconcile:"}},
                    ]
                }
            ),
        }

        print("\n=== SANDBOX STATE CHECK ===")
        pprint(result)

        is_clean = (
            result["sandbox_trades"] == 0
            and result["demo_user_trades"] == 0
            and result["sandbox_trade_events"] == 0
            and result["demo_user_trade_events"] == 0
        )

        print(f"\nCLEAN = {is_clean}")

    finally:
        mongo.close()


if __name__ == "__main__":
    main()