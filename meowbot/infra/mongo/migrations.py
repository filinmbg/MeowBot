from __future__ import annotations

from typing import Iterable
from pymongo.database import Database
from pymongo import ASCENDING, DESCENDING


DEFAULT_TIMEFRAMES = ("1m", "15m", "1h", "4h", "1d")


def bars_collection_name(tf: str) -> str:
    return f"bars_{tf}"


def apply_migrations(db: Database, timeframes: Iterable[str] = DEFAULT_TIMEFRAMES) -> None:
    """
    Створює індекси. Безпечне повторне застосування.
    """

    # ---- trades ----
    trades = db["trades"]
    trades.create_index([("status", ASCENDING), ("symbol", ASCENDING)], name="status_symbol")
    trades.create_index([("trade_id", ASCENDING)], unique=True, name="trade_id_unique")
    trades.create_index(
        [("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        name="entry_bar_dedupe",
    )
    trades.create_index([("status", ASCENDING), ("exit_last_check_at", ASCENDING)], name="status_exit_cursor")
    trades.create_index(
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        unique=True,
        name="uniq_entry_per_user_bar",
    )

    # ---- trade_events ----
    events = db["trade_events"]
    events.create_index([("trade_id", ASCENDING), ("ts", ASCENDING)], name="trade_ts")
    events.create_index([("event_type", ASCENDING), ("ts", ASCENDING)], name="event_type_ts")
    events.create_index([("user_id", ASCENDING), ("ts", ASCENDING)], name="user_ts")

    # ---- bot_state ----
    bot_state = db["bot_state"]
    bot_state.create_index([("key", ASCENDING)], unique=True, name="key_unique")

    # ---- feature_state ----
    feature_state = db["feature_state"]
    feature_state.create_index(
        [("symbol", ASCENDING), ("tf", ASCENDING), ("features_ver", ASCENDING)],
        unique=True,
        name="sym_tf_ver_unique",
    )

    # ---- bars_<tf> ----
    for tf in timeframes:
        col = db[bars_collection_name(tf)]

        col.create_index(
            [("symbol", ASCENDING), ("close_time", ASCENDING), ("features_ver", ASCENDING)],
            unique=True,
            name="sym_close_ver_unique",
        )

        col.create_index(
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_close_desc",
        )

        col.create_index(
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("features_ok", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_ok_close_desc",
        )