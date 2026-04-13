from __future__ import annotations


RISK_EVENT_TYPES_FOR_USER = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
}

RISK_EVENT_TYPES_FOR_ADMIN = {
    "ENTRY_BLOCKED_SUBSCRIPTION",
    "ENTRY_BLOCKED_RISK",
    "ENTRY_WARNING_RISK",
    "ENTRY_BLOCKED_COOLDOWN",
}


class AsyncSendRiskEventsToTelegramUseCase:
    def __init__(
        self,
        *,
        trade_events_repo,
        runtime_repo,
        bot,
        builder,
        admin_chat_ids: list[int] | None = None,
    ) -> None:
        self.trade_events_repo = trade_events_repo
        self.runtime_repo = runtime_repo
        self.bot = bot
        self.builder = builder
        self.admin_chat_ids = admin_chat_ids or []

    async def run_once(self) -> int:
        """
        Тут очікується, що repo вміє віддавати pending/new events.
        Якщо в тебе метод називається інакше — просто підставиш його.
        """
        events = await self.trade_events_repo.get_pending_risk_events()
        sent = 0

        for event in events:
            event_type = str(event.get("event_type") or "")
            payload = dict(event.get("payload") or {})
            user_id = str(event.get("user_id") or "")
            symbol = str(event.get("symbol") or "")
            mode = str(event.get("mode") or "")

            payload["symbol"] = symbol

            if event_type in RISK_EVENT_TYPES_FOR_USER:
                target = await self.runtime_repo.resolve_notification_target_by_user_id(user_id)
                if target and target.get("chat_id"):
                    text = self.builder.build_user_message(event_type, payload)
                    if text:
                        try:
                            await self.bot.send_message(
                                chat_id=int(target["chat_id"]),
                                text=text,
                                parse_mode="HTML",
                            )
                            sent += 1
                        except Exception:
                            pass

            if event_type in RISK_EVENT_TYPES_FOR_ADMIN:
                admin_text = self.builder.build_admin_message(
                    event_type,
                    payload,
                    user_id=user_id,
                    symbol=symbol,
                )
                for admin_chat_id in self.admin_chat_ids:
                    try:
                        await self.bot.send_message(
                            chat_id=int(admin_chat_id),
                            text=admin_text,
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

            await self.trade_events_repo.mark_event_delivered(event["id"], channel="telegram_risk")

        return sent