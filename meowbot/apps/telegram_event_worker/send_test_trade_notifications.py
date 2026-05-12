from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from aiogram import Bot
from dotenv import load_dotenv

from meowbot.core.services.notifications.trade_event_message_builder import (
    TradeEventMessageBuilder,
)
from meowbot.infra.postgres.client import get_pg_pool


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


USER_QUERY = """
select
    u.id as user_id,
    u.preferred_language,
    tp.telegram_id,
    tp.chat_id,
    tp.username,
    tp.first_name,
    tp.last_name
from users u
join telegram_profiles tp
    on tp.user_id = u.id
where
    u.status = 'active'
    and tp.is_onboarded = true
    and tp.chat_id is not null
order by u.created_at asc
limit $1
"""


TEST_EVENTS = (
    (
        "OPENED",
        "INJUSDT",
        {
            "side": "LONG",
            "tf_entry": "1h",
            "entry_price": 2.9580,
            "sl_price": 2.8988,
            "stake_usd": 10.23,
            "default_leverage": 5,
            "qty": 17.28448157,
        },
    ),
    (
        "TP_HIT",
        "BTCUSDT",
        {
            "tp_index": 1,
            "realized_pnl_usd": 0.32,
        },
    ),
    (
        "STOP",
        "UNIUSDT",
        {
            "realized_pnl_usd": -0.27,
        },
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send test trade notifications to onboarded Telegram users.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100000,
        help="Maximum number of users to send to.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=120,
        help="Delay between messages in milliseconds.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent without calling Telegram API.",
    )
    return parser


def _display_name(row: dict) -> str:
    for key in ("username", "first_name", "last_name", "telegram_id"):
        value = row.get(key)
        if value:
            return str(value)
    return "unknown"


async def _load_targets(pg_pool, *, limit: int) -> list[dict]:
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(USER_QUERY, int(limit))
    return [dict(row) for row in rows]


async def main() -> None:
    args = build_parser().parse_args()

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    pg_pool = await get_pg_pool()
    bot = Bot(token=bot_token)
    builder = TradeEventMessageBuilder()
    sent_count = 0
    failed_count = 0

    try:
        targets = await _load_targets(pg_pool, limit=max(int(args.limit), 1))
        print(f"Loaded targets: {len(targets)}")

        for row in targets:
            chat_id = row.get("chat_id")
            if not chat_id:
                continue

            lang = str(row.get("preferred_language") or "en")
            prefix = "[TEST]"

            for event_type, symbol, payload in TEST_EVENTS:
                text = builder.build(
                    event_type=event_type,
                    symbol=symbol,
                    mode="live",
                    payload=payload,
                    lang=lang,
                    user_id=str(row.get("user_id") or ""),
                )
                text = f"{prefix}\n{text}"

                if args.dry_run:
                    print(
                        f"DRY-RUN chat_id={chat_id} user={_display_name(row)} "
                        f"lang={lang} event={event_type}"
                    )
                else:
                    try:
                        await bot.send_message(
                            chat_id=int(chat_id),
                            text=text,
                            parse_mode="HTML",
                        )
                        sent_count += 1
                    except Exception as exc:
                        failed_count += 1
                        print(
                            f"FAILED chat_id={chat_id} user={_display_name(row)} "
                            f"event={event_type}: {type(exc).__name__}: {exc}"
                        )

                await asyncio.sleep(max(args.sleep_ms, 0) / 1000.0)

        print(
            f"Done. sent={sent_count} failed={failed_count} "
            f"users={len(targets)} dry_run={args.dry_run}"
        )
    finally:
        await bot.session.close()
        await pg_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
