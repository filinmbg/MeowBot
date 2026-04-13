from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SendTrialExpiryWarningsUseCase:
    def __init__(
        self,
        *,
        trial_notifications_repo,
        bot,
        admin_chat_ids: list[int] | None = None,
        hours_before_end: int = 6,
    ) -> None:
        self.trial_notifications_repo = trial_notifications_repo
        self.bot = bot
        self.admin_chat_ids = admin_chat_ids or []
        self.hours_before_end = int(hours_before_end)

    async def execute(self) -> dict[str, Any]:
        rows = await self.trial_notifications_repo.get_trials_needing_expiry_warning(
            hours_before_end=self.hours_before_end,
            limit=100,
        )

        sent_count = 0
        failed_count = 0

        for row in rows:
            subscription_id = row["subscription_id"]
            chat_id = row.get("chat_id")
            if not chat_id:
                failed_count += 1
                continue

            text = self._build_user_message(row)

            try:
                await self.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode="HTML",
                )
                await self.trial_notifications_repo.mark_trial_expiry_warning_sent(
                    subscription_id=subscription_id,
                )
                sent_count += 1

                await self._notify_admins(
                    f"⚠️ Trial warning sent\n"
                    f"user_id=<code>{row.get('user_id')}</code>\n"
                    f"plan=<code>{row.get('plan_code')}</code>\n"
                    f"telegram_id=<code>{row.get('telegram_id')}</code>\n"
                    f"ends_at=<code>{self._fmt_dt(row.get('ends_at'))}</code>"
                )
            except Exception as exc:
                failed_count += 1
                await self._notify_admins(
                    f"❌ Trial warning failed\n"
                    f"user_id=<code>{row.get('user_id')}</code>\n"
                    f"telegram_id=<code>{row.get('telegram_id')}</code>\n"
                    f"reason=<code>{type(exc).__name__}: {exc}</code>"
                )

        return {
            "checked": len(rows),
            "sent": sent_count,
            "failed": failed_count,
        }

    def _build_user_message(self, row: dict[str, Any]) -> str:
        ends_at = row.get("ends_at")
        ends_at_text = self._fmt_dt(ends_at)

        return (
            "⏳ <b>Пробний період скоро завершиться</b>\n\n"
            f"Ваш trial доступ закінчиться: <code>{ends_at_text}</code>\n\n"
            "Щоб зберегти <b>live-торгівлю</b>, оформіть підписку.\n"
            "Якщо підписку не підключити, акаунт автоматично перейде у режим "
            "<b>FREE / sandbox</b>."
        )

    async def _notify_admins(self, text: str) -> None:
        if not self.admin_chat_ids:
            return

        for chat_id in self.admin_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    @staticmethod
    def _fmt_dt(value) -> str:
        if value is None:
            return "-"
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return str(value)