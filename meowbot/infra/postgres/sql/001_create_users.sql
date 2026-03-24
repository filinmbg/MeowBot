create extension if not exists pgcrypto;

create table if not exists users (
    id uuid primary key default gen_random_uuid(),
    email text unique,
    status text not null default 'active',
    role text not null default 'user',
    timezone text not null default 'Europe/Kiev',
    language_code text not null default 'uk',
    display_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists ix_users_status on users(status);
create index if not exists ix_users_created_at on users(created_at);