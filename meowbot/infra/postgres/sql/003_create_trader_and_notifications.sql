create table if not exists trader_settings (
    user_id uuid primary key references users(id) on delete cascade,

    enabled boolean not null default false,
    mode text not null default 'sandbox',
    entry_mode text not null default 'fixed',
    entry_value numeric(18,8) not null default 10,

    risk_profile text not null default 'conservative',

    allow_long boolean not null default true,
    allow_short boolean not null default false,

    night_mode boolean not null default false,
    quiet_hours_from smallint,
    quiet_hours_to smallint,

    max_open_trades integer not null default 1,
    max_daily_loss numeric(18,8),
    max_trades_per_day integer,

    current_exchange_account_id uuid,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists ix_trader_settings_mode
    on trader_settings(mode);


create table if not exists notification_preferences (
    user_id uuid primary key references users(id) on delete cascade,

    telegram_enabled boolean not null default true,
    email_enabled boolean not null default false,

    trade_alerts boolean not null default true,
    tp_alerts boolean not null default true,
    sl_alerts boolean not null default true,

    daily_report boolean not null default true,
    weekly_report boolean not null default false,

    system_alerts boolean not null default true,
    marketing_alerts boolean not null default false,

    critical_night_override boolean not null default true,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);