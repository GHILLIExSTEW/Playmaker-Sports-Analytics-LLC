begin;

-- Shared daily ceiling for the bot's paid OpenAI slip reads. API-Sports calls already pass
-- through reserve_api_request(s); vision calls had no ceiling and every image a capper
-- posted could trigger up to four paid reads.
create table if not exists public.openai_vision_budget (
  day date primary key,
  requests integer not null default 0 check (requests >= 0)
);
alter table public.openai_vision_budget enable row level security;
revoke all on public.openai_vision_budget from public, anon, authenticated;
grant select on public.openai_vision_budget to service_role;

-- Returns true and counts the call when under the limit; false (and counts nothing) otherwise.
-- The conditional upsert takes the day's row lock, so concurrent callers cannot exceed it.
create or replace function public.reserve_openai_vision_request(p_daily_limit integer)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  today date := (pg_catalog.now() at time zone 'UTC')::date;
  used integer;
begin
  if p_daily_limit is null or p_daily_limit < 1 or p_daily_limit > 100000 then
    raise exception 'Invalid vision daily limit';
  end if;
  insert into public.openai_vision_budget as budget (day, requests) values (today, 1)
  on conflict (day) do update set requests = budget.requests + 1
    where budget.requests < p_daily_limit
  returning budget.requests into used;
  return used is not null;
end;
$$;
revoke all on function public.reserve_openai_vision_request(integer) from public, anon, authenticated;
grant execute on function public.reserve_openai_vision_request(integer) to service_role;

commit;
