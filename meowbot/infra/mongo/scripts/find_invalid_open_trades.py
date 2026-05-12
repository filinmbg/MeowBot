from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from meowbot.core.domain.enums import TradeStatus
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol
from meowbot.infra.mongo.client import MongoConfig, MongoConn


logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meowbot")


def _doc_summary(doc: dict[str, Any], invalid_reason: str) -> dict[str, Any]:
    return {
        "trade_id": doc.get("trade_id"),
        "user_id": doc.get("user_id"),
        "symbol": doc.get("symbol"),
        "mode": doc.get("mode"),
        "status": doc.get("status"),
        "opened_at": doc.get("opened_at"),
        "invalid_reason": invalid_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List or quarantine OPEN trades with invalid Binance USDT perpetual symbols."
    )
    parser.add_argument("--repair", action="store_true", help="Mark invalid OPEN trades as ERROR_INVALID_SYMBOL.")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum OPEN trades to scan.")
    args = parser.parse_args()

    conn = MongoConn(MongoConfig(app_name="MeowBot-find-invalid-open-trades"))
    conn.connect()
    assert conn.db is not None

    col = conn.db["trades"]
    cursor = (
        col.find({"status": TradeStatus.OPEN.value})
        .sort("opened_at", 1)
        .limit(max(1, int(args.limit)))
    )

    invalid_rows: list[dict[str, Any]] = []
    repaired = 0
    now_ms = int(time.time() * 1000)
    for doc in cursor:
        validation = validate_binance_usdt_perp_symbol(doc.get("symbol"))
        if validation.valid:
            continue
        invalid_rows.append(_doc_summary(doc, validation.reason))

        if args.repair:
            result = col.update_one(
                {"_id": doc["_id"], "status": TradeStatus.OPEN.value},
                {
                    "$set": {
                        "status": TradeStatus.ERROR_INVALID_SYMBOL.value,
                        "is_open": False,
                        "closed": False,
                        "closed_at": now_ms,
                        "exit_last_check_at": now_ms,
                        "exit_reason": "ERROR_INVALID_SYMBOL",
                        "exchange_sync_status": "invalid_symbol",
                        "exchange_sync_error": validation.reason,
                        "qty_remaining": 0.0,
                        "remaining_pct": 0.0,
                        "updated_at": now_ms,
                    }
                },
            )
            repaired += int(result.modified_count)

    print(json.dumps({"invalid_open_trades": invalid_rows, "count": len(invalid_rows), "repaired": repaired}, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
