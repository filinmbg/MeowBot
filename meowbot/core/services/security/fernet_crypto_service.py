from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class FernetCryptoService:
    def __init__(self, fernet_key: str) -> None:
        if not fernet_key:
            raise ValueError("Fernet key is empty")
        self._fernet = Fernet(fernet_key.encode("utf-8"))

    @classmethod
    def from_env(cls, env_name: str = "MEOWBOT_SECRETS_FERNET_KEY") -> "FernetCryptoService":
        key = os.getenv(env_name)
        if not key:
            raise RuntimeError(f"{env_name} is not set")
        return cls(key)

    def encrypt_text(self, value: str) -> str:
        if value is None:
            raise ValueError("Cannot encrypt None")
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt_text(self, value: str) -> str:
        if not value:
            raise ValueError("Encrypted value is empty")
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Failed to decrypt secret: invalid token or wrong Fernet key") from exc