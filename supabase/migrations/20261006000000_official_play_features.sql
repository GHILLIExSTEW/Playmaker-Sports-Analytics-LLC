-- Structured leg data for auto-settle suggestions, plus member tails and capper follows.
begin;

alter table public.play_legs add column if not exists details jsonb;
alter table public.plays add column if not exists auto_suggested_at timestamptz;

create index if not exists plays_open_unsuggested
  on public.plays (created_at)
  where status = 'open' and auto_suggested_at is null;

create table if not exists public.play_tails (
  play_id bigint not null references public.plays(id) on delete cascade,
  discord_user_id text not null,
  created_at timestamptz not null default now(),
  primary key (play_id, discord_user_id)
);
create index if not exists play_tails_member on public.play_tails (discord_user_id);
alter table public.play_tails enable row level security;
revoke all on public.play_tails from public, anon, authenticated;
grant select, insert, update, delete on public.play_tails to service_role;

create table if not exists public.capper_follows (
  capper_user_id bigint not null references public.users(id) on delete cascade,
  follower_discord_id text not null,
  created_at timestamptz not null default now(),
  primary key (capper_user_id, follower_discord_id)
);
alter table public.capper_follows enable row level security;
revoke all on public.capper_follows from public, anon, authenticated;
grant select, insert, update, delete on public.capper_follows to service_role;

commit;
