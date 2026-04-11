from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from fastapi import Request
from pymongo import MongoClient
from pymongo.database import Database


load_dotenv()


@dataclass(frozen=True)
class MongoConfig:
    uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    db_name: str = os.getenv("MONGO_DB_NAME", "meowbot")


class MongoConn:
    def __init__(self, config: MongoConfig):
        self.config = config
        self.client: MongoClient | None = None
        self.db: Database | None = None

    def connect(self) -> None:
        if self.client is not None and self.db is not None:
            return

        self.client = MongoClient(
            self.config.uri,
            serverSelectionTimeoutMS=5000,
        )
        self.client.admin.command("ping")
        self.db = self.client[self.config.db_name]

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
            self.db = None

    def ping(self) -> bool:
        try:
            if self.client is None:
                return False
            self.client.admin.command("ping")
            return True
        except Exception:
            return False


def get_mongo_conn_from_request(request: Request) -> MongoConn:
    mongo = getattr(request.app.state, "mongo", None)
    if mongo is None:
        raise RuntimeError("Mongo is not initialized in app.state.mongo")
    return mongo


def get_mongo_db(request: Request):
    mongo = get_mongo_conn_from_request(request)
    if mongo.db is None:
        raise RuntimeError("Mongo database is not initialized")
    return mongo.db