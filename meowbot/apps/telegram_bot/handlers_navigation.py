from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from meowbot.apps.telegram_bot.handlers_admin import render_admin_menu, render_admin_summary
from meowbot.apps.telegram_bot.handlers_menu import render_help_screen, render_home_screen
from meowbot.apps.telegram_bot.handlers_plan import render_plan_screen
from meowbot.apps.telegram_bot.handlers_settings import render_settings_menu
from meowbot.apps.telegram_bot.handlers_trades import render_trades_menu
from meowbot.apps.telegram_bot.menu_support import safe_callback_answer


router = Router()


@router.callback_query(F.data == "nav:back:home")
async def nav_back_home(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_home_screen(callback, **deps)


@router.callback_query(F.data == "nav:back:settings")
async def nav_back_settings(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_settings_menu(callback, **deps)


@router.callback_query(F.data == "nav:back:trades")
async def nav_back_trades(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_menu(callback, **deps)


@router.callback_query(F.data == "nav:back:admin")
async def nav_back_admin(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_menu(callback, **deps)


@router.callback_query(F.data == "nav:refresh:home")
async def nav_refresh_home(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_home_screen(callback, **deps)


@router.callback_query(F.data == "nav:refresh:plan")
async def nav_refresh_plan(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_screen(callback, **deps)


@router.callback_query(F.data == "nav:refresh:trades")
async def nav_refresh_trades(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_menu(callback, **deps)


@router.callback_query(F.data == "nav:refresh:admin_summary")
async def nav_refresh_admin_summary(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_summary(callback, **deps)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_home_screen(callback, **deps)


@router.callback_query(F.data == "menu:plan")
async def menu_plan(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_screen(callback, **deps)


@router.callback_query(F.data == "menu:trades")
async def menu_trades(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_menu(callback, **deps)


@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_settings_menu(callback, **deps)


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    if callback.message is not None:
        await render_help_screen(callback.message, **deps)


@router.callback_query(F.data == "menu:admin")
async def menu_admin(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_menu(callback, **deps)
