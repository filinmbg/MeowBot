from __future__ import annotations

import os

import httpx


class TelegramNotifierAsync:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.bot_token = bot_token or os.getenv("BOT_TOKEN")
        self.default_chat_id = default_chat_id or os.getenv("ADMIN_CHAT_ID")
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.default_chat_id)

    async def send_message(self, text: str, *, chat_id: str | None = None) -> None:
        target_chat_id = chat_id or self.default_chat_id
        if not self.bot_token or not target_chat_id:
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()