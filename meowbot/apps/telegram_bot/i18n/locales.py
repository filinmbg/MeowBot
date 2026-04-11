from __future__ import annotations

LOCALES: dict[str, dict[str, str]] = {
    "uk": {
        "app_name": "BotMeow",
        "start_welcome_new": (
            "🐾 <b>Ласкаво просимо в BotMeow</b>\n\n"
            "Це бот для керування торговим помічником, перегляду статистики, трейдів і налаштувань.\n\n"
            "Щоб почати, натисни кнопку <b>«🚀 Почати»</b>."
        ),
        "start_welcome_old": (
            "🏠 <b>Головне меню</b>\n\n"
            "Оберіть потрібний розділ кнопками знизу."
        ),
        "tutorial_text": (
            "📘 <b>Короткий туторіал</b>\n\n"
            "Ось що доступно в меню:\n\n"
            "🤖 <b>Мій бот</b> — статус бота, режим роботи, швидкі дії\n"
            "📊 <b>Статистика</b> — результати торгівлі\n"
            "📈 <b>Трейди</b> — відкриті та останні закриті угоди\n"
            "⚙️ <b>Налаштування</b> — мова, параметри, режими\n"
            "👤 <b>Профіль</b> — інформація про твій акаунт\n"
            "❓ <b>Допомога</b> — підказки по використанню\n\n"
            "Головне меню буде завжди доступне знизу."
        ),
        "main_menu_title": "🏠 <b>Головне меню</b>\n\nОберіть потрібний розділ кнопками знизу.",
        "btn_start": "🚀 Почати",
        "btn_my_bot": "🤖 Мій бот",
        "btn_stats": "📊 Статистика",
        "btn_trades": "📈 Трейди",
        "btn_settings": "⚙️ Налаштування",
        "btn_profile": "👤 Профіль",
        "btn_help": "❓ Допомога",
        "btn_language": "🌐 Мова",
        "btn_mode": "🔄 Режим",
        "btn_stake": "💰 Розмір позиції",
        "btn_leverage": "⚡ Плече",
        "btn_notifications": "🔔 Нотифікації",
        "btn_back": "⬅️ Назад",
        "btn_menu": "🏠 Меню",
        "help_text": (
            "❓ <b>Допомога</b>\n\n"
            "Доступні розділи:\n"
            "• <b>🤖 Мій бот</b> — стан системи\n"
            "• <b>📊 Статистика</b> — результати торгівлі\n"
            "• <b>📈 Трейди</b> — відкриті та останні угоди\n"
            "• <b>⚙️ Налаштування</b> — параметри та мова\n"
            "• <b>👤 Профіль</b> — інформація про акаунт\n\n"
            "Також працюють команди:\n"
            "• <code>/start</code>\n"
            "• <code>/stats</code>\n"
            "• <code>/stats_global</code>\n"
            "• <code>/stats_symbol BTCUSDT</code>"
        ),
        "my_bot_text": (
            "🤖 <b>Мій бот</b>\n\n"
            "Тут буде:\n"
            "• статус бота\n"
            "• режим sandbox / live\n"
            "• активні монети\n"
            "• швидке керування"
        ),
        "trades_text": (
            "📈 <b>Трейди</b>\n\n"
            "Тут буде:\n"
            "• відкриті трейди\n"
            "• останні закриті трейди\n"
            "• деталі входів і виходів"
        ),
        "settings_text": "⚙️ <b>Налаштування</b>\n\nОберіть, що хочете змінити.",
        "settings_language_text": "🌐 <b>Мова</b>\n\nОберіть мову інтерфейсу:",
        "settings_mode_text": "🔄 <b>Режим торгівлі</b>\n\nОберіть режим:",
        "settings_stake_text": "💰 <b>Розмір позиції</b>\n\nОберіть розмір позиції у % від депозиту:",
        "settings_leverage_text": "⚡ <b>Плече</b>\n\nОберіть плече:",
        "settings_notifications_text": "🔔 <b>Нотифікації</b>\n\nОберіть режим нотифікацій:",
        "language_changed_uk": "✅ Мову змінено на українську.",
        "language_changed_ru": "✅ Язык изменён на русский.",
        "language_changed_en": "✅ Language changed to English.",
        "mode_changed_sandbox": "✅ Режим змінено на Sandbox.",
        "mode_changed_live": "✅ Режим змінено на Live.",
        "stake_changed": "✅ Розмір позиції змінено на {value}%.",
        "leverage_changed": "✅ Плече змінено на x{value}.",
        "notifications_enabled_text": "✅ Нотифікації увімкнено.",
        "notifications_disabled_text": "✅ Нотифікації вимкнено.",
        "profile_title": "👤 <b>Профіль</b>",
        "profile_not_found": "Профіль ще не знайдено.",
        "profile_tg_id": "Telegram ID",
        "profile_username": "Username",
        "profile_first_name": "Ім'я",
        "profile_last_name": "Прізвище",
        "profile_language": "Мова",
        "profile_onboarded": "Онбординг",
        "yes": "так",
        "no": "ні",
        "unknown_user": "Не вдалося визначити користувача.",
        "unknown": "-",
        "stats_title_user": "Твоя статистика",
        "stats_title_global": "Глобальна статистика",
        "stats_title_symbol": "Статистика {symbol}",
        "stats_no_data": "Немає даних",
        "stats_access_denied": "⛔ Немає доступу",
        "stats_symbol_format": "Формат: /stats_symbol BTCUSDT",
        "mode_sandbox": "Sandbox",
        "mode_live": "Live",
        "notifications_on": "Увімкнено",
        "notifications_off": "Вимкнено",
    },
    "ru": {
        "app_name": "BotMeow",
        "start_welcome_new": (
            "🐾 <b>Добро пожаловать в BotMeow</b>\n\n"
            "Это бот для управления торговым помощником, просмотра статистики, сделок и настроек.\n\n"
            "Чтобы начать, нажми кнопку <b>«🚀 Начать»</b>."
        ),
        "start_welcome_old": (
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите нужный раздел кнопками снизу."
        ),
        "tutorial_text": (
            "📘 <b>Краткий туториал</b>\n\n"
            "Вот что доступно в меню:\n\n"
            "🤖 <b>Мой бот</b> — статус бота, режим работы, быстрые действия\n"
            "📊 <b>Статистика</b> — результаты торговли\n"
            "📈 <b>Сделки</b> — открытые и последние закрытые сделки\n"
            "⚙️ <b>Настройки</b> — язык, параметры, режимы\n"
            "👤 <b>Профиль</b> — информация о вашем аккаунте\n"
            "❓ <b>Помощь</b> — подсказки по использованию\n\n"
            "Главное меню всегда будет доступно снизу."
        ),
        "main_menu_title": "🏠 <b>Главное меню</b>\n\nВыберите нужный раздел кнопками снизу.",
        "btn_start": "🚀 Начать",
        "btn_my_bot": "🤖 Мой бот",
        "btn_stats": "📊 Статистика",
        "btn_trades": "📈 Сделки",
        "btn_settings": "⚙️ Настройки",
        "btn_profile": "👤 Профиль",
        "btn_help": "❓ Помощь",
        "btn_language": "🌐 Язык",
        "btn_mode": "🔄 Режим",
        "btn_stake": "💰 Размер позиции",
        "btn_leverage": "⚡ Плечо",
        "btn_notifications": "🔔 Уведомления",
        "btn_back": "⬅️ Назад",
        "btn_menu": "🏠 Меню",
        "help_text": (
            "❓ <b>Помощь</b>\n\n"
            "Доступные разделы:\n"
            "• <b>🤖 Мой бот</b> — состояние системы\n"
            "• <b>📊 Статистика</b> — результаты торговли\n"
            "• <b>📈 Сделки</b> — открытые и последние сделки\n"
            "• <b>⚙️ Настройки</b> — параметры и язык\n"
            "• <b>👤 Профиль</b> — информация об аккаунте\n\n"
            "Также работают команды:\n"
            "• <code>/start</code>\n"
            "• <code>/stats</code>\n"
            "• <code>/stats_global</code>\n"
            "• <code>/stats_symbol BTCUSDT</code>"
        ),
        "my_bot_text": (
            "🤖 <b>Мой бот</b>\n\n"
            "Здесь будет:\n"
            "• статус бота\n"
            "• режим sandbox / live\n"
            "• активные монеты\n"
            "• быстрое управление"
        ),
        "trades_text": (
            "📈 <b>Сделки</b>\n\n"
            "Здесь будет:\n"
            "• открытые сделки\n"
            "• последние закрытые сделки\n"
            "• детали входов и выходов"
        ),
        "settings_text": "⚙️ <b>Настройки</b>\n\nВыберите, что хотите изменить.",
        "settings_language_text": "🌐 <b>Язык</b>\n\nВыберите язык интерфейса:",
        "settings_mode_text": "🔄 <b>Режим торговли</b>\n\nВыберите режим:",
        "settings_stake_text": "💰 <b>Размер позиции</b>\n\nВыберите размер позиции в % от депозита:",
        "settings_leverage_text": "⚡ <b>Плечо</b>\n\nВыберите плечо:",
        "settings_notifications_text": "🔔 <b>Уведомления</b>\n\nВыберите режим уведомлений:",
        "language_changed_uk": "✅ Язык изменён на украинский.",
        "language_changed_ru": "✅ Язык изменён на русский.",
        "language_changed_en": "✅ Language changed to English.",
        "mode_changed_sandbox": "✅ Режим изменён на Sandbox.",
        "mode_changed_live": "✅ Режим изменён на Live.",
        "stake_changed": "✅ Размер позиции изменён на {value}%.",
        "leverage_changed": "✅ Плечо изменено на x{value}.",
        "notifications_enabled_text": "✅ Уведомления включены.",
        "notifications_disabled_text": "✅ Уведомления отключены.",
        "profile_title": "👤 <b>Профиль</b>",
        "profile_not_found": "Профиль ещё не найден.",
        "profile_tg_id": "Telegram ID",
        "profile_username": "Username",
        "profile_first_name": "Имя",
        "profile_last_name": "Фамилия",
        "profile_language": "Язык",
        "profile_onboarded": "Онбординг",
        "yes": "да",
        "no": "нет",
        "unknown_user": "Не удалось определить пользователя.",
        "unknown": "-",
        "stats_title_user": "Твоя статистика",
        "stats_title_global": "Глобальная статистика",
        "stats_title_symbol": "Статистика {symbol}",
        "stats_no_data": "Нет данных",
        "stats_access_denied": "⛔ Нет доступа",
        "stats_symbol_format": "Формат: /stats_symbol BTCUSDT",
        "mode_sandbox": "Sandbox",
        "mode_live": "Live",
        "notifications_on": "Включены",
        "notifications_off": "Выключены",
    },
    "en": {
        "app_name": "BotMeow",
        "start_welcome_new": (
            "🐾 <b>Welcome to BotMeow</b>\n\n"
            "This bot helps you manage your trading assistant, view stats, trades, and settings.\n\n"
            "To begin, press the <b>“🚀 Start”</b> button."
        ),
        "start_welcome_old": (
            "🏠 <b>Main menu</b>\n\n"
            "Choose a section using the buttons below."
        ),
        "tutorial_text": (
            "📘 <b>Quick tutorial</b>\n\n"
            "Here is what you can use in the menu:\n\n"
            "🤖 <b>My Bot</b> — bot status, mode, quick actions\n"
            "📊 <b>Statistics</b> — trading results\n"
            "📈 <b>Trades</b> — open and recently closed trades\n"
            "⚙️ <b>Settings</b> — language, parameters, modes\n"
            "👤 <b>Profile</b> — your account information\n"
            "❓ <b>Help</b> — usage tips\n\n"
            "The main menu will always stay available at the bottom."
        ),
        "main_menu_title": "🏠 <b>Main menu</b>\n\nChoose a section using the buttons below.",
        "btn_start": "🚀 Start",
        "btn_my_bot": "🤖 My Bot",
        "btn_stats": "📊 Statistics",
        "btn_trades": "📈 Trades",
        "btn_settings": "⚙️ Settings",
        "btn_profile": "👤 Profile",
        "btn_help": "❓ Help",
        "btn_language": "🌐 Language",
        "btn_mode": "🔄 Mode",
        "btn_stake": "💰 Position size",
        "btn_leverage": "⚡ Leverage",
        "btn_notifications": "🔔 Notifications",
        "btn_back": "⬅️ Back",
        "btn_menu": "🏠 Menu",
        "help_text": (
            "❓ <b>Help</b>\n\n"
            "Available sections:\n"
            "• <b>🤖 My Bot</b> — system status\n"
            "• <b>📊 Statistics</b> — trading results\n"
            "• <b>📈 Trades</b> — open and recent trades\n"
            "• <b>⚙️ Settings</b> — parameters and language\n"
            "• <b>👤 Profile</b> — account information\n\n"
            "These commands also work:\n"
            "• <code>/start</code>\n"
            "• <code>/stats</code>\n"
            "• <code>/stats_global</code>\n"
            "• <code>/stats_symbol BTCUSDT</code>"
        ),
        "my_bot_text": (
            "🤖 <b>My Bot</b>\n\n"
            "This section will contain:\n"
            "• bot status\n"
            "• sandbox / live mode\n"
            "• active symbols\n"
            "• quick controls"
        ),
        "trades_text": (
            "📈 <b>Trades</b>\n\n"
            "This section will contain:\n"
            "• open trades\n"
            "• recent closed trades\n"
            "• entry and exit details"
        ),
        "settings_text": "⚙️ <b>Settings</b>\n\nChoose what you want to change.",
        "settings_language_text": "🌐 <b>Language</b>\n\nChoose the interface language:",
        "settings_mode_text": "🔄 <b>Trading mode</b>\n\nChoose the mode:",
        "settings_stake_text": "💰 <b>Position size</b>\n\nChoose position size as % of deposit:",
        "settings_leverage_text": "⚡ <b>Leverage</b>\n\nChoose leverage:",
        "settings_notifications_text": "🔔 <b>Notifications</b>\n\nChoose notification mode:",
        "language_changed_uk": "✅ Language changed to Ukrainian.",
        "language_changed_ru": "✅ Language changed to Russian.",
        "language_changed_en": "✅ Language changed to English.",
        "mode_changed_sandbox": "✅ Mode changed to Sandbox.",
        "mode_changed_live": "✅ Mode changed to Live.",
        "stake_changed": "✅ Position size changed to {value}%.",
        "leverage_changed": "✅ Leverage changed to x{value}.",
        "notifications_enabled_text": "✅ Notifications enabled.",
        "notifications_disabled_text": "✅ Notifications disabled.",
        "profile_title": "👤 <b>Profile</b>",
        "profile_not_found": "Profile not found yet.",
        "profile_tg_id": "Telegram ID",
        "profile_username": "Username",
        "profile_first_name": "First name",
        "profile_last_name": "Last name",
        "profile_language": "Language",
        "profile_onboarded": "Onboarding",
        "yes": "yes",
        "no": "no",
        "unknown_user": "Could not identify the user.",
        "unknown": "-",
        "stats_title_user": "Your statistics",
        "stats_title_global": "Global statistics",
        "stats_title_symbol": "Statistics for {symbol}",
        "stats_no_data": "No data",
        "stats_access_denied": "⛔ Access denied",
        "stats_symbol_format": "Format: /stats_symbol BTCUSDT",
        "mode_sandbox": "Sandbox",
        "mode_live": "Live",
        "notifications_on": "Enabled",
        "notifications_off": "Disabled",
    },
}