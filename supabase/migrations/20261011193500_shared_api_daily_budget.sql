begin;

create or replace function public.reserve_api_requests(
  p_product text, p_discord_user_id text, p_requests integer
)
returns text language plpgsql set search_path = ''
as $$
declare
  today date := (now() at time zone 'UTC')::date;
  budget public.api_request_budget;
  used integer;
  total_used bigint;
begin
  if p_product is null or p_product not in (
    'american-football', 'basketball', 'football', 'hockey', 'baseball',
    'rugby', 'handball', 'volleyball', 'mma', 'formula-1'
  ) or p_requests is null or p_requests not between 1 and 5 then
    raise exception 'Invalid request batch';
  end if;
  if p_discord_user_id is not null and p_discord_user_id !~ '^[0-9]{1,20}$' then
    raise exception 'Invalid Discord ID';
  end if;

  -- All products and reservation sizes share the same transaction lock.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('api-global-daily-budget', 0)
  );
  if p_discord_user_id is not null then
    perform pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('api-user:' || p_discord_user_id, 0)
    );
  end if;
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('api-product:' || p_product, 0)
  );

  select coalesce(sum(system_requests::bigint + member_requests::bigint), 0)
    into total_used from public.api_request_budget where day = today;
  if total_used + p_requests > 7500 then
    return 'Shared 7500-request daily allowance exhausted';
  end if;

  insert into public.api_request_budget(product, day) values(p_product, today)
    on conflict do nothing;
  select * into budget from public.api_request_budget
    where product = p_product and day = today;
  if p_discord_user_id is null then
    update public.api_request_budget
      set system_requests = system_requests + p_requests
      where product = p_product and day = today;
  else
    if budget.member_requests + p_requests > 20 then
      return 'Shared member daily allowance exhausted';
    end if;
    if budget.last_member_request > now() - interval '5 minutes' then
      return 'Shared five-minute cooldown: use the cache and retry later';
    end if;
    select requests into used from public.api_member_request_usage
      where discord_user_id = p_discord_user_id and day = today;
    if coalesce(used, 0) + p_requests > 5 then
      return 'Your five-request daily allowance cannot cover this lookup';
    end if;
    insert into public.api_member_request_usage(discord_user_id, day, requests)
      values(p_discord_user_id, today, p_requests)
      on conflict(discord_user_id, day) do update
      set requests = public.api_member_request_usage.requests + p_requests;
    update public.api_request_budget
      set member_requests = member_requests + p_requests, last_member_request = now()
      where product = p_product and day = today;
  end if;
  return 'reserved';
end;
$$;

create or replace function public.reserve_api_request(
  p_product text, p_discord_user_id text default null
)
returns text language sql set search_path = ''
as $$
  select public.reserve_api_requests(p_product, p_discord_user_id, 1);
$$;

revoke all on function public.reserve_api_requests(text, text, integer)
  from public, anon, authenticated;
revoke all on function public.reserve_api_request(text, text)
  from public, anon, authenticated;
grant execute on function public.reserve_api_requests(text, text, integer) to service_role;
grant execute on function public.reserve_api_request(text, text) to service_role;

commit;
