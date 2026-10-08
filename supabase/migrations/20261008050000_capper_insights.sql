begin;

create table public.play_insights (
  play_id bigint primary key references public.plays(id) on delete cascade,
  justification text not null default '' check (char_length(justification) <= 2000),
  updated_at timestamptz,
  prompt_message_id text check (prompt_message_id ~ '^[0-9]{1,20}$')
);
alter table public.play_insights enable row level security;
revoke all on public.play_insights from public, anon, authenticated;

create function public.capper_insight_context(p_play_id bigint default null)
returns table (play_id bigint, discord_user_id text, selection text, justification text, prompt_message_id text)
language sql stable security definer set search_path = ''
as $$
  select p.id, u.discord_user_id, coalesce(nullif(p.team_name, ''), 'Official play'),
    coalesce(i.justification, ''), i.prompt_message_id
  from public.plays p
  join public.users u on u.id = p.user_id
  left join public.play_insights i on i.play_id = p.id
  where p.status = 'open' and p.settled_at is null and p.message_id is not null
    and not exists (select 1 from public.plays older where older.message_id = p.message_id and older.id < p.id)
    and u.discord_user_id = any(public.website_capper_ids())
    and (p_play_id is null and i.prompt_message_id is null and coalesce(i.justification, '') = ''
         or p.id = p_play_id)
  order by p.created_at, p.id
  limit 50;
$$;

create function public.save_capper_insight(p_play_id bigint, p_discord_user_id text, p_justification text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
  author_id text;
begin
  select u.discord_user_id into author_id
  from public.plays p join public.users u on u.id = p.user_id
  where p.id = p_play_id and p.status = 'open' and p.settled_at is null
    and p.message_id is not null
    and not exists (select 1 from public.plays older where older.message_id = p.message_id and older.id < p.id)
  for update of p;
  if author_id is null or author_id is distinct from p_discord_user_id
     or not (author_id = any(public.website_capper_ids())) then
    raise exception 'Only the original current OPERATOR author of an open published pick can submit insight.'
      using errcode = '42501';
  end if;
  if p_justification is null or btrim(p_justification) = '' or char_length(p_justification) > 2000 then
    raise exception 'Insight must contain 1 to 2000 characters.';
  end if;
  insert into public.play_insights (play_id, justification, updated_at)
  values (p_play_id, btrim(p_justification), now())
  on conflict (play_id) do update set justification = excluded.justification, updated_at = excluded.updated_at;
  return true;
end;
$$;

create function public.mark_capper_insight_prompt(p_play_id bigint, p_message_id text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if p_message_id is null or p_message_id !~ '^[0-9]{1,20}$' then
    raise exception 'Invalid insight prompt message identity.';
  end if;
  insert into public.play_insights (play_id, prompt_message_id)
  values (p_play_id, p_message_id)
  on conflict (play_id) do update set prompt_message_id = excluded.prompt_message_id;
  return true;
end;
$$;

-- Preserve the entitlement, roster and publication filters; never use slip text as insight.
create or replace function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric
)
language plpgsql stable security definer set search_path = ''
as $$
declare
  capper_ids text[];
begin
  if public.website_member_access() ->> 'state' <> 'active' then
    raise exception 'Verified active membership required.' using errcode = '42501';
  end if;
  capper_ids := public.website_capper_ids();
  return query
  select source.id, source.created_at, source.sport, source.capper, source.avatar_url,
    source.selection, coalesce(insight.justification, ''), source.odds, source.units
  from public.website_current_picks_source(p_sport, p_capper) as source
  join public.plays as plays on plays.id = source.id
  join public.users as users on users.id = plays.user_id
  left join public.play_insights as insight on insight.play_id = source.id
  where users.discord_user_id = any(capper_ids)
  order by source.created_at desc, source.id desc;
end;
$$;

revoke all on function public.capper_insight_context(bigint) from public, anon, authenticated;
revoke all on function public.save_capper_insight(bigint, text, text) from public, anon, authenticated;
revoke all on function public.mark_capper_insight_prompt(bigint, text) from public, anon, authenticated;
grant execute on function public.capper_insight_context(bigint) to service_role;
grant execute on function public.save_capper_insight(bigint, text, text) to service_role;
grant execute on function public.mark_capper_insight_prompt(bigint, text) to service_role;

commit;
