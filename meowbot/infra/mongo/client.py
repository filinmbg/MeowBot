from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database


@dataclass(frozen=True)
class MongoConfig:
    uri: str = "mongodb://localhost:27017"
    db_name: str = "meowbot"
    app_name: str = "MeowBot"


class MongoConn:
    """
    Мінімальний thin-wrapper, щоб зручно використовувати в infra/repos.
    """
    def __init__(self, cfg: MongoConfig):
        self.cfg = cfg
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None

    def connect(self) -> None:
        if self._client is not None:
            return
        # serverSelectionTimeoutMS: щоб ping не висів довго, якщо Mongo нема
        self._client = MongoClient(
            self.cfg.uri,
            appname=self.cfg.app_name,
            serverSelectionTimeoutMS=1500,
        )
        self._db = self._client[self.cfg.db_name]

    @property
    def db(self) -> Database:
        if self._db is None:
            self.connect()
        assert self._db is not None
        return self._db

    def ping(self) -> bool:
        try:
            self.connect()
            self.db.command("ping")
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._db = None