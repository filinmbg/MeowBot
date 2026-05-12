alter table trader_settings
    add column if not exists max_risk_trades integer not null default 5,
    add column if not exists sandbox_start_balance_usd numeric(18, 2) not null default 1000.00;

update trader_settings
set
    max_risk_trades = coalesce(max_risk_trades, 5),
    sandbox_start_balance_usd = coalesce(sandbox_start_balance_usd, 1000.00);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'trader_settings_max_risk_trades_positive'
    ) then
        alter table trader_settings
            add constraint trader_settings_max_risk_trades_positive
            check (max_risk_trades >= 1) not valid;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'trader_settings_sandbox_start_balance_positive'
    ) then
        alter table trader_settings
            add constraint trader_settings_sandbox_start_balance_positive
            check (sandbox_start_balance_usd > 0) not valid;
    end if;
end $$;
