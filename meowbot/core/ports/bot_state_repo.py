from __future__ import annotations
from typing import Protocol


class BotStateRepository(Protocol):
    def get_int(self, key: str, default: int = 0) -> int:
        ...

    def set_int(self, key: str, value: int) -> None:
        ...