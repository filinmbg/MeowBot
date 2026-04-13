from __future__ import annotations

from typing import Any


class SaveUserBinanceApiKeysUseCase:
    def __init__(
        self,
        *,
        telegram_user_account_repo,
        user_api_keys_service,
    ) -> None:
        self.telegram_user_account_repo = telegram_user_account_repo
        self.user_api_keys_service = user_api_keys_service

    async def execute(
        self,
        *,
        telegram_id: int,
        label: str,
        api_key: str,
        api_secret: str,
    ) -> dict[str, Any]:
        user = await self.telegram_user_account_repo.get_user_by_telegram_id(int(telegram_id))
        if not user:
            return {
                "ok": False,
                "reason": "user_not_found",
                "message": "Користувача не знайдено в Postgres. Спочатку пройди старт / реєстрацію.",
            }

        if str(user.get("status")) != "active":
            return {
                "ok": False,
                "reason": "user_inactive",
                "message": "Користувач не активний.",
            }

        result = await self.user_api_keys_service.save_binance_key_for_user(
            user_id=user["user_id"],
            label=label,
            api_key=api_key,
            api_secret=api_secret,
        )

        validation_ok = bool(result.get("validation_ok", False))
        validation_reason = str(result.get("validation_reason", "unknown"))
        permissions_json = result.get("permissions_json") or {}
        stored_key = result.get("stored_key") or {}

        return {
            "ok": validation_ok,
            "reason": validation_reason,
            "message": self._build_message(
                validation_ok=validation_ok,
                validation_reason=validation_reason,
                permissions_json=permissions_json,
                stored_key=stored_key,
            ),
            "stored_key": stored_key,
            "permissions_json": permissions_json,
        }

    def _build_message(
        self,
        *,
        validation_ok: bool,
        validation_reason: str,
        permissions_json: dict[str, Any],
        stored_key: dict[str, Any],
    ) -> str:
        enable_reading = bool(permissions_json.get("enableReading", False))
        enable_futures = bool(permissions_json.get("enableFutures", False))
        enable_withdrawals = bool(permissions_json.get("enableWithdrawals", False))

        lines = []

        if validation_ok:
            lines.append("✅ Binance API ключ успішно збережено.")
        else:
            lines.append("❌ Binance API ключ не пройшов валідацію.")

        lines.append(f"Reason: <code>{validation_reason}</code>")
        if stored_key:
            lines.append(f"Label: <code>{stored_key.get('label')}</code>")
            lines.append(f"Validation status: <code>{stored_key.get('validation_status')}</code>")

        lines.append("")
        lines.append("Permissions:")
        lines.append(f"• Reading: <code>{enable_reading}</code>")
        lines.append(f"• Futures: <code>{enable_futures}</code>")
        lines.append(f"• Withdrawals: <code>{enable_withdrawals}</code>")

        if not validation_ok:
            lines.append("")
            lines.append("Для MeowBot ключ повинен:")
            lines.append("• мати Reading = true")
            lines.append("• мати Futures = true")
            lines.append("• мати Withdrawals = false")

        return "\n".join(lines)