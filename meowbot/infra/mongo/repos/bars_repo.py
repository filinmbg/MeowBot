from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from pymongo import ASCENDING, DESCENDING, UpdateOne
from pymongo.database import Database

from meowbot.core.domain.types import Bar
from meowbot.core.ports.bars_repo import BarsRepository
from meowbot.infra.mongo.migrations import bars_collection_name


def _bar_to_doc(b: Bar) -> dict:
    # зберігаємо як звичайний dict
    return asdict(b)


def _doc_to_bar(doc: dict) -> Bar:
    return Bar(**doc)


class BarsRepositoryMongo(BarsRepository):
    """
    Зберігаємо бари в колекціях bars_<tf>, як ми заклали в migrations.py
    Унікальність гарантується індексом: (symbol, close_time, features_ver)
    """
    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        try:
            self.db.command("ping")
            return True
        except Exception:
            return False

    def _col(self, tf: str):
        return self.db[bars_collection_name(tf)]

    def get_last_close_time(self, symbol: str, tf: str, features_ver: str) -> Optional[int]:
        col = self._col(tf)
        doc = col.find_one(
            {"symbol": symbol, "features_ver": features_ver},
            projection={"close_time": 1, "_id": 0},
            sort=[("close_time", DESCENDING)],
        )
        return int(doc["close_time"]) if doc else None

    def get_tail(
        self,
        symbol: str,
        tf: str,
        n: int,
        features_ver: str,
        require_features_ok: bool = True,
    ) -> List[Bar]:
        col = self._col(tf)

        q = {"symbol": symbol, "features_ver": features_ver}
        if require_features_ok:
            q["features_ok"] = True

        docs = list(
            col.find(q, projection={"_id": 0})
               .sort("close_time", DESCENDING)
               .limit(int(n))
        )
        # повертаємо у зростаючому порядку close_time (як in-memory repo)
        docs.reverse()
        return [_doc_to_bar(d) for d in docs]

    def upsert_many(self, bars: List[Bar]) -> None:
        if not bars:
            return

        # групуємо по tf, бо колекції різні
        by_tf: dict[str, list[Bar]] = {}
        for b in bars:
            by_tf.setdefault(b.tf, []).append(b)

        for tf, tf_bars in by_tf.items():
            col = self._col(tf)

            ops = []
            for b in tf_bars:
                doc = _bar_to_doc(b)
                filt = {"symbol": b.symbol, "close_time": b.close_time, "features_ver": b.features_ver}
                ops.append(UpdateOne(filt, {"$set": doc}, upsert=True))

            col.bulk_write(ops, ordered=False)