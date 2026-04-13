from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ExpireTrialsAndNotifyUseCase:
    def __init__(
        self,
        *,
        trial_expiration_repo,
        bot,
        admin_chat_ids: list[int] | None = None,
    ) -> None:
        self.trial_expiration_repo = trial_expiration_repo
        self.bot = bot
        self.admin_chat_ids = admin_chat_ids or []

    async def execute(self) -> dict[str, Any]:
        expired_rows = await self.trial_expiration_repo.expire_trials_and_fallback_to_free()

        if not expired_rows:
            return {
                "expired_count": 0,
                "user_messages_sent": 0,
                "admin_messages_sent": 0,
            }

        recent_users = await self.trial_expiration_repo.get_users_for_recent_trial_expirations(
            lookback_minutes=10,
            limit=100,
        )

        by_user_id = {str(row["user_id"]): row for row in recent_users}

        user_messages_sent = 0
        admin_messages_sent = 0

        for row in expired_rows:
            affected_user_id = str(row.get("affected_user_id"))
            action = str(row.get("action"))

            user_row = by_user_id.get(affected_user_id)
            if user_row:
                try:
                    await self.bot.send_message(
                        chat_id=int(user_row["chat_id"]),
                        text=self._build_user_message(user_row, action),
                        parse_mode="HTML",
                    )
                    user_messages_sent += 1
                except Exception as exc:
                    await self._notify_admins(
                        f"❌ Trial expire user notify failed\n"
                        f"user_id=<code>{affected_user_id}</code>\n"
                        f"reason=<code>{type(exc).__name__}: {exc}</code>"
                    )

            admin_text = self._build_admin_message(
                affected_user_id=affected_user_id,
                action=action,
                user_row=user_row,
            )
            admin_messages_sent += await self._notify_admins(admin_text)

        return {
            "expired_count": len(expired_rows),
            "user_messages_sent": user_messages_sent,
            "admin_messages_sent": admin_messages_sent,
        }

    def _build_user_message(self, row: dict[str, Any], action: str) -> str:
        ends_at = self._fmt_dt(row.get("ends_at"))

        if action == "trial_expired_paid_exists":
            return (
                "ℹ️ <b>Пробний період завершено</b>\n\n"
                f"Trial завершився: <code>{ends_at}</code>\n"
                "У вас уже є активна платна підписка, тому доступ збережено."
            )

        return (
            "⛔ <b>Пробний період завершено</b>\n\n"
            f"Trial завершився: <code>{ends_at}</code>\n\n"
            "Нові <b>live</b>-угоди більше не відкриваються.\n"
            "Ваш акаунт автоматично переведено у режим <b>FREE / sandbox</b>.\n\n"
            "Щоб знову увімкнути live-торгівлю, оформіть підписку."
        )

    def _build_admin_message(
        self,
        *,
        affected_user_id: str,
        action: str,
        user_row: dict[str, Any] | None,
    ) -> str:
        if user_row is None:
            return (
                "ℹ️ Trial expired\n"
                f"user_id=<code>{affected_user_id}</code>\n"
                f"action=<code>{action}</code>"
            )

        return (
            "ℹ️ Trial expired\n"
            f"user_id=<code>{affected_user_id}</code>\n"
            f"telegram_id=<code>{user_row.get('telegram_id')}</code>\n"
            f"chat_id=<code>{user_row.get('chat_id')}</code>\n"
            f"plan=<code>{user_row.get('plan_code')}</code>\n"
            f"action=<code>{action}</code>\n"
            f"ends_at=<code>{self._fmt_dt(user_row.get('ends_at'))}</code>"
        )

    async def _notify_admins(self, text: str) -> int:
        if not self.admin_chat_ids:
            return 0

        sent = 0
        for chat_id in self.admin_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                pass
        return sent

    @staticmethod
    def _fmt_dt(value) -> str:
        if value is None:
            return "-"
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return str(value)