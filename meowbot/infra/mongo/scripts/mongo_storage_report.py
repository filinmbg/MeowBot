from __future__ import annotations

import logging

from meowbot.infra.mongo.client import MongoConfig, MongoConn


logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("meowbot")


def _fmt_mb(value: int | float | None) -> str:
    return f"{float(value or 0) / (1024 * 1024):.2f} MB"


def main() -> None:
    conn = MongoConn(MongoConfig(app_name="MeowBot-mongo-storage-report"))
    conn.connect()
    assert conn.db is not None

    rows: list[dict[str, object]] = []
    for name in sorted(conn.db.list_collection_names()):
        try:
            stats = conn.db.command("collStats", name)
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "count": "?",
                    "size": f"error:{type(exc).__name__}",
                    "storage": "-",
                    "index": "-",
                    "total": "-",
                }
            )
            continue

        storage_size = int(stats.get("storageSize", 0) or 0)
        index_size = int(stats.get("totalIndexSize", 0) or 0)
        rows.append(
            {
                "name": name,
                "count": int(stats.get("count", 0) or 0),
                "size": _fmt_mb(stats.get("size", 0)),
                "storage": _fmt_mb(storage_size),
                "index": _fmt_mb(index_size),
                "total": _fmt_mb(storage_size + index_size),
            }
        )

    print("collection                              count        data     storage       index       total")
    print("-" * 94)
    for row in rows:
        print(
            f"{str(row['name'])[:38]:38} "
            f"{str(row['count']):>10} "
            f"{str(row['size']):>11} "
            f"{str(row['storage']):>11} "
            f"{str(row['index']):>11} "
            f"{str(row['total']):>11}"
        )

    conn.close()


if __name__ == "__main__":
    main()

