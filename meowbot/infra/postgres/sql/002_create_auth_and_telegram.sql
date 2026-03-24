create table if not exists auth_identities (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users(id) on delete cascade,
    provider text not null,
    provider_user_id text not null,
    created_at timestamptz not null default now(),

    constraint uq_auth_identities_provider_user unique (provider, provider_user_id)
);

create index if not exists ix_auth_identities_user_id
    on auth_identities(user_id);

create index if not exists ix_auth_identities_provider
    on auth_identities(provider);


create table if not exists telegram_profiles (
    user_id uuid primary key references users(id) on delete cascade,
    telegram_user_id bigint not null unique,
    username text,
    first_name text,
    last_name text,
    last_interaction_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists ix_telegram_profiles_telegram_user_id
    on telegram_profiles(telegram_user_id);

create index if not exists ix_telegram_profiles_last_interaction_at
    on telegram_profiles(last_interaction_at);