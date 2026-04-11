from __future__ import annotations

import logging


log = logging.getLogger("meowbot")


USER_EVENT_TYPES = {"OPENED", "TP_HIT", "CLOSED"}
ADMIN_DEBUG_EVENT_TYPES = {
    "ENTRY_PRICE_UNAVAILABLE",
    "ENTRY_PRICE_MISMATCH",
    "LIVE_MODE_NOT_CONNECTED",
}


class AsyncSendTradeEventsToTelegramUseCase:
    def __init__(
        self,
        *,
        trade_events_repo,
        telegram_users_repo,
        bot,
        message_builder,
        admin_chat_id: str | None,
    ) -> None:
        self.trade_events_repo = trade_events_repo
        self.telegram_users_repo = telegram_users_repo
        self.bot = bot
        self.message_builder = message_builder
        self.admin_chat_id = str(admin_chat_id).strip() if admin_chat_id else None

    async def run_once(self) -> int:
        events = await self.trade_events_repo.get_unsent_events(limit=100)
        if not events:
            return 0

        sent_count = 0

        for event in events:
            ok = await self._process_event(event)
            if ok:
                await self.trade_events_repo.mark_sent(event["_id"])
                sent_count += 1

        return sent_count

    async def _process_event(self, event: dict) -> bool:
        event_type = str(event.get("event_type") or "")
        symbol = str(event.get("symbol") or "-")
        user_id = str(event.get("user_id") or "system")
        mode = str(event.get("mode") or "sandbox")
        payload = event.get("payload") or {}

        try:
            if event_type in USER_EVENT_TYPES:
                target = await self.telegram_users_repo.resolve_notification_target_by_user_id(user_id)
                if not target:
                    log.warning(
                        "[telegram-events] no target found for user_id=%s event_type=%s",
                        user_id,
                        event_type,
                    )
                    return True

                if not bool(target.get("notifications_enabled", True)):
                    log.info(
                        "[telegram-events] notifications disabled for user_id=%s event_type=%s",
                        user_id,
                        event_type,
                    )
                    return True

                chat_id = target.get("chat_id")
                if not chat_id:
                    log.warning(
                        "[telegram-events] no chat_id for user_id=%s event_type=%s",
                        user_id,
                        event_type,
                    )
                    return True

                lang = str(target.get("preferred_language") or "uk")
                text = self.message_builder.build(
                    event_type=event_type,
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload=payload,
                    lang=lang,
                )

                await self.bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode="HTML",
                )
                return True

            if event_type in ADMIN_DEBUG_EVENT_TYPES:
                if not self.admin_chat_id:
                    return True

                text = self.message_builder.build_admin_debug(
                    event_type=event_type,
                    symbol=symbol,
                    user_id=user_id,
                    mode=mode,
                    payload=payload,
                )
                await self.bot.send_message(
                    chat_id=int(self.admin_chat_id),
                    text=text,
                    parse_mode="HTML",
                )
                return True

            return True

        except Exception:
            log.exception(
                "[telegram-events] failed event_type=%s user_id=%s symbol=%s",
                event_type,
                user_id,
                symbol,
            )
            return False