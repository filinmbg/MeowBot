from __future__ import annotations

from typing import Iterable
from pymongo.database import Database
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure


DEFAULT_TIMEFRAMES = ("1m", "15m", "1h", "4h", "1d")
SECONDS_7_DAYS = 7 * 24 * 60 * 60
SECONDS_30_DAYS = 30 * 24 * 60 * 60


def bars_collection_name(tf: str) -> str:
    return f"bars_{tf}"


def _normalize_index_keys(keys) -> list[tuple[str, int]]:
    return [(str(field), int(direction)) for field, direction in keys]


def _has_compatible_index_sync(collection, keys, *, unique: bool = False, sparse: bool = False) -> bool:
    expected_keys = _normalize_index_keys(keys)
    info = collection.index_information()
    for spec in info.values():
        existing_keys = _normalize_index_keys(spec.get("key", []))
        if existing_keys != expected_keys:
            continue
        if bool(spec.get("unique", False)) != bool(unique):
            continue
        if bool(spec.get("sparse", False)) != bool(sparse):
            continue
        return True
    return False


def _ensure_index_sync(collection, keys, *, name: str, **options) -> None:
    unique = bool(options.get("unique", False))
    sparse = bool(options.get("sparse", False))
    if _has_compatible_index_sync(collection, keys, unique=unique, sparse=sparse):
        return
    try:
        collection.create_index(keys, name=name, **options)
    except OperationFailure as exc:
        if getattr(exc, "code", None) == 85 and _has_compatible_index_sync(
            collection,
            keys,
            unique=unique,
            sparse=sparse,
        ):
            return
        raise


async def _has_compatible_index_async(collection, keys, *, unique: bool = False, sparse: bool = False) -> bool:
    expected_keys = _normalize_index_keys(keys)
    info = await collection.index_information()
    for spec in info.values():
        existing_keys = _normalize_index_keys(spec.get("key", []))
        if existing_keys != expected_keys:
            continue
        if bool(spec.get("unique", False)) != bool(unique):
            continue
        if bool(spec.get("sparse", False)) != bool(sparse):
            continue
        return True
    return False


async def _ensure_index_async(collection, keys, *, name: str, **options) -> None:
    unique = bool(options.get("unique", False))
    sparse = bool(options.get("sparse", False))
    if await _has_compatible_index_async(collection, keys, unique=unique, sparse=sparse):
        return
    try:
        await collection.create_index(keys, name=name, **options)
    except OperationFailure as exc:
        if getattr(exc, "code", None) == 85 and await _has_compatible_index_async(
            collection,
            keys,
            unique=unique,
            sparse=sparse,
        ):
            return
        raise


def apply_migrations(db: Database, timeframes: Iterable[str] = DEFAULT_TIMEFRAMES) -> None:
    """
    Створює індекси. Безпечне повторне застосування.
    """

    # ---- trades ----
    trades = db["trades"]
    _ensure_index_sync(trades, [("status", ASCENDING), ("symbol", ASCENDING)], name="status_symbol")
    _ensure_index_sync(trades, [("mode", ASCENDING), ("status", ASCENDING)], name="mode_status")
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING)],
        name="user_mode_status",
    )
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
        name="user_mode_status_updated_desc",
    )
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("created_at", DESCENDING)],
        name="user_mode_created_desc",
    )
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("status", ASCENDING)],
        name="user_symbol_status",
    )
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING)],
        name="user_symbol_mode_status",
    )
    _ensure_index_sync(trades, [("trade_id", ASCENDING)], unique=True, name="trade_id_unique")
    _ensure_index_sync(trades, [("exchange_order_ids", ASCENDING)], name="exchange_order_ids")
    _ensure_index_sync(
        trades,
        [("mode", ASCENDING), ("status", ASCENDING), ("exchange_last_sync_at", ASCENDING)],
        name="live_sync_cursor",
    )
    _ensure_index_sync(
        trades,
        [("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        name="entry_bar_dedupe",
    )
    _ensure_index_sync(trades, [("status", ASCENDING), ("exit_last_check_at", ASCENDING)], name="status_exit_cursor")
    _ensure_index_sync(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        unique=True,
        name="uniq_entry_per_user_bar",
    )

    # ---- trade_events ----
    events = db["trade_events"]
    _ensure_index_sync(events, [("trade_id", ASCENDING), ("ts", ASCENDING)], name="trade_ts")
    _ensure_index_sync(events, [("event_type", ASCENDING), ("ts", ASCENDING)], name="event_type_ts")
    _ensure_index_sync(events, [("user_id", ASCENDING), ("ts", ASCENDING)], name="user_ts")
    _ensure_index_sync(events, [("sent", ASCENDING), ("created_at", ASCENDING)], name="sent_created_at")
    _ensure_index_sync(events, [("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created_desc")
    _ensure_index_sync(
        events,
        [("idempotency_key", ASCENDING)],
        unique=True,
        sparse=True,
        name="idempotency_key_unique",
    )
    _ensure_index_sync(
        events,
        [("created_at", ASCENDING)],
        name="trade_events_created_at_ttl",
        expireAfterSeconds=SECONDS_30_DAYS,
    )

    # ---- debug_events ----
    debug_events = db["debug_events"]
    _ensure_index_sync(
        debug_events,
        [("created_at", ASCENDING)],
        name="debug_events_created_at_ttl",
        expireAfterSeconds=SECONDS_7_DAYS,
    )

    # ---- bot_state ----
    bot_state = db["bot_state"]
    _ensure_index_sync(bot_state, [("key", ASCENDING)], unique=True, name="key_unique")

    # ---- feature_state ----
    feature_state = db["feature_state"]
    _ensure_index_sync(
        feature_state,
        [("symbol", ASCENDING), ("tf", ASCENDING), ("features_ver", ASCENDING)],
        unique=True,
        name="sym_tf_ver_unique",
    )

    # ---- bars_<tf> ----
    for tf in timeframes:
        col = db[bars_collection_name(tf)]

        _ensure_index_sync(
            col,
            [("symbol", ASCENDING), ("close_time", ASCENDING), ("features_ver", ASCENDING)],
            unique=True,
            name="sym_close_ver_unique",
        )

        _ensure_index_sync(
            col,
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_close_desc",
        )

        _ensure_index_sync(
            col,
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("features_ok", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_ok_close_desc",
        )
        _ensure_index_sync(
            col,
            [("created_at_dt", ASCENDING)],
            name="bars_created_at_ttl",
            expireAfterSeconds=SECONDS_7_DAYS,
        )


async def apply_migrations_async(db, timeframes: Iterable[str] = DEFAULT_TIMEFRAMES) -> None:
    trades = db["trades"]
    await _ensure_index_async(trades, [("status", ASCENDING), ("symbol", ASCENDING)], name="status_symbol")
    await _ensure_index_async(trades, [("mode", ASCENDING), ("status", ASCENDING)], name="mode_status")
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING)],
        name="user_mode_status",
    )
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
        name="user_mode_status_updated_desc",
    )
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("mode", ASCENDING), ("created_at", DESCENDING)],
        name="user_mode_created_desc",
    )
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("status", ASCENDING)],
        name="user_symbol_status",
    )
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("mode", ASCENDING), ("status", ASCENDING)],
        name="user_symbol_mode_status",
    )
    await _ensure_index_async(trades, [("trade_id", ASCENDING)], unique=True, name="trade_id_unique")
    await _ensure_index_async(trades, [("exchange_order_ids", ASCENDING)], name="exchange_order_ids")
    await _ensure_index_async(
        trades,
        [("mode", ASCENDING), ("status", ASCENDING), ("exchange_last_sync_at", ASCENDING)],
        name="live_sync_cursor",
    )
    await _ensure_index_async(
        trades,
        [("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        name="entry_bar_dedupe",
    )
    await _ensure_index_async(trades, [("status", ASCENDING), ("exit_last_check_at", ASCENDING)], name="status_exit_cursor")
    await _ensure_index_async(
        trades,
        [("user_id", ASCENDING), ("symbol", ASCENDING), ("tf_entry", ASCENDING), ("entry_bar_close_time", ASCENDING)],
        unique=True,
        name="uniq_entry_per_user_bar",
    )

    events = db["trade_events"]
    await _ensure_index_async(events, [("trade_id", ASCENDING), ("ts", ASCENDING)], name="trade_ts")
    await _ensure_index_async(events, [("event_type", ASCENDING), ("ts", ASCENDING)], name="event_type_ts")
    await _ensure_index_async(events, [("user_id", ASCENDING), ("ts", ASCENDING)], name="user_ts")
    await _ensure_index_async(events, [("sent", ASCENDING), ("created_at", ASCENDING)], name="sent_created_at")
    await _ensure_index_async(events, [("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created_desc")
    await _ensure_index_async(
        events,
        [("idempotency_key", ASCENDING)],
        unique=True,
        sparse=True,
        name="idempotency_key_unique",
    )
    await _ensure_index_async(
        events,
        [("created_at", ASCENDING)],
        name="trade_events_created_at_ttl",
        expireAfterSeconds=SECONDS_30_DAYS,
    )

    debug_events = db["debug_events"]
    await _ensure_index_async(
        debug_events,
        [("created_at", ASCENDING)],
        name="debug_events_created_at_ttl",
        expireAfterSeconds=SECONDS_7_DAYS,
    )

    bot_state = db["bot_state"]
    await _ensure_index_async(bot_state, [("key", ASCENDING)], unique=True, name="key_unique")

    feature_state = db["feature_state"]
    await _ensure_index_async(
        feature_state,
        [("symbol", ASCENDING), ("tf", ASCENDING), ("features_ver", ASCENDING)],
        unique=True,
        name="sym_tf_ver_unique",
    )

    for tf in timeframes:
        col = db[bars_collection_name(tf)]
        await _ensure_index_async(
            col,
            [("symbol", ASCENDING), ("close_time", ASCENDING), ("features_ver", ASCENDING)],
            unique=True,
            name="sym_close_ver_unique",
        )
        await _ensure_index_async(
            col,
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_close_desc",
        )
        await _ensure_index_async(
            col,
            [("symbol", ASCENDING), ("features_ver", ASCENDING), ("features_ok", ASCENDING), ("close_time", DESCENDING)],
            name="sym_ver_ok_close_desc",
        )
        await _ensure_index_async(
            col,
            [("created_at_dt", ASCENDING)],
            name="bars_created_at_ttl",
            expireAfterSeconds=SECONDS_7_DAYS,
        )
