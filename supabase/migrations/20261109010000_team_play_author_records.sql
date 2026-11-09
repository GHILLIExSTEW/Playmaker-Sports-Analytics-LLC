-- Preserve official team rows while adding each affirmative voter to personal records.
begin;

alter table public.plays add column if not exists team_approval jsonb;
alter table public.capper_ballots
  add column if not exists decision jsonb,
  add column if not exists review jsonb;
alter table public.capper_ballots add column if not exists author_id text;

update public.capper_ballots
set author_id = coalesce(nullif(author_id, ''), nullif(decision ->> 'author_id', ''), nullif(review ->> 'author_id', ''))
where author_id is null;

drop function public.public_settled_results();
create function public.public_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text, capper_record boolean
)
language sql stable security definer set search_path = ''
as $$
  with published as (
    select distinct on (p.message_id) p.*
    from public.plays p
    where p.message_id is not null and p.settled_at is not null
      and p.status in ('win', 'loss', 'void', 'partial')
      and p.tracking_scope in ('official', 'capper')
      and (p.tracking_scope <> 'capper' or p.units <= 3)
      and p.created_at >= (select starts_at from public.tracking_display_settings where singleton)
    order by p.message_id, p.id
  )
  select p.created_at, p.settled_at,
    coalesce(nullif(btrim(s.name), ''), 'Other'),
    case when p.tracking_scope = 'official' then 'Official Playmaker Picks'
      else coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') end,
    case when u.public_avatar_url like 'https://%' then u.public_avatar_url else null end,
    coalesce(
      (select string_agg(nullif(btrim(l.selection), ''), ' / ' order by l.leg_number)
       from public.play_legs l where l.play_id = p.id),
      nullif(btrim(p.team_name), ''), 'Official play'
    ),
    p.odds, p.units, p.status,
    case p.status
      when 'win' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end, 2)
      when 'loss' then -p.units
      when 'partial' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end * 0.5, 2)
      else 0::numeric
    end,
    p.tracking_scope,
    p.tracking_scope = 'capper'
  from published p
  left join public.sports s on s.id = p.sport_id
  left join public.users u on u.id = p.user_id
  order by p.settled_at desc, p.created_at desc;
$$;
revoke all on function public.public_settled_results() from public, anon, authenticated;
grant execute on function public.public_settled_results() to anon, authenticated;

create function public.public_capper_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text, capper_record boolean
)
language sql stable security definer set search_path = ''
as $$
  with published as (
    select distinct on (p.message_id) p.*
    from public.plays p
    where p.message_id is not null and p.settled_at is not null
      and p.status in ('win', 'loss', 'void', 'partial')
      and p.tracking_scope = 'official'
      and p.created_at >= greatest(
        '2026-10-15T00:00:00-04:00'::timestamptz,
        (select starts_at from public.tracking_display_settings where singleton)
      )
    order by p.message_id, p.id
  )
  select p.created_at, p.settled_at,
    coalesce(nullif(btrim(s.name), ''), 'Other'),
    coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks'),
    case when u.public_avatar_url like 'https://%' then u.public_avatar_url else null end,
    coalesce(
      (select string_agg(nullif(btrim(l.selection), ''), ' / ' order by l.leg_number)
       from public.play_legs l where l.play_id = p.id),
      nullif(btrim(p.team_name), ''), 'Official play'
    ),
    p.odds, p.units, p.status,
    case p.status
      when 'win' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end, 2)
      when 'loss' then -p.units
      when 'partial' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end * 0.5, 2)
      else 0::numeric
    end,
    p.tracking_scope, true
  from published p
  cross join lateral jsonb_each_text(coalesce(p.team_approval -> 'voter_choices', '{}'::jsonb)) voter
  join public.users u on u.discord_user_id = voter.key
  left join public.sports s on s.id = p.sport_id
  where voter.value = 'yes'
  order by p.settled_at desc, p.created_at desc, u.display_name;
$$;
revoke all on function public.public_capper_settled_results() from public, anon, authenticated;
grant execute on function public.public_capper_settled_results() to anon, authenticated;

drop function public.member_current_picks(text, text);
create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric,
  approval_yes integer, eligible_cappers integer,
  approval_policy text, approval_no integer, approval_pass integer,
  can_edit_insight boolean
)
language sql stable security definer set search_path = ''
as $$
  select source.id, source.created_at, source.sport,
    case when p.tracking_scope = 'official' and supporter.id is not null
      then coalesce(nullif(btrim(supporter.display_name), ''), 'Playmaker Picks')
      else source.capper end,
    case when p.tracking_scope = 'official' and supporter.public_avatar_url like 'https://%'
      then supporter.public_avatar_url else source.avatar_url end,
    source.selection, source.analysis, source.odds, source.units,
    (p.team_approval ->> 'yes')::integer, (p.team_approval ->> 'roster_size')::integer,
    p.team_approval ->> 'policy_version',
    (p.team_approval -> 'counts' ->> 'no')::integer,
    (p.team_approval -> 'counts' ->> 'pass')::integer,
    p.tracking_scope = 'official' and p.team_approval ->> 'policy_version' = 'slip-v1'
      and public.play_insight_author(p.id) = any(public.website_capper_ids())
      and exists(select 1 from auth.identities i where i.user_id = auth.uid()
        and i.provider = 'discord' and i.provider_id = public.play_insight_author(p.id))
  from public.archived_current_picks(null, null) source
  join public.plays p on p.id = source.id
  left join lateral (
    select u.*
    from jsonb_each_text(coalesce(p.team_approval -> 'voter_choices', '{}'::jsonb)) voter
    join public.users u on u.discord_user_id = voter.key
    where voter.value = 'yes'
      and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper
      and u.discord_user_id = any(public.website_capper_ids())
    order by u.id
    limit 1
  ) supporter on p_capper is not null
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
    and (p_sport is null or source.sport = p_sport)
    and (p_capper is null or source.capper = p_capper or supporter.id is not null)
  order by source.created_at desc, source.id desc;
$$;
revoke all on function public.member_current_picks(text, text) from public, anon, authenticated;
grant execute on function public.member_current_picks(text, text) to authenticated;

drop function if exists public.public_capper_directory();
create function public.public_capper_directory()
returns table (name text, avatar_url text)
language sql stable security definer set search_path = ''
as $$
  select distinct
    coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks'),
    case when u.public_avatar_url like 'https://%' then u.public_avatar_url else null end
  from public.users u
  where u.discord_user_id = any(public.website_capper_ids())
  order by 1, 2;
$$;
revoke all on function public.public_capper_directory() from public, anon, authenticated;
grant execute on function public.public_capper_directory() to anon, authenticated;

drop function if exists public.public_favorite_cappers();
create function public.public_favorite_cappers()
returns table (sport_id bigint, sport text, capper_name text)
language sql stable security definer set search_path = ''
as $$
  select distinct p.sport_id, s.name, coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks')
  from public.plays p
  join public.sports s on s.id = p.sport_id
  join public.users u on u.id = p.user_id
  where p.tracking_scope = 'capper'
    and u.discord_user_id = any(public.website_capper_ids())
    and p.message_id is not null
    and (p.status = 'open' and p.settled_at is null
      or p.status in ('win', 'loss', 'void', 'partial') and p.settled_at is not null)
    and s.api_slug <> 'official'
  union
  select distinct p.sport_id, s.name, coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks')
  from public.plays p
  cross join lateral jsonb_each_text(coalesce(p.team_approval -> 'voter_choices', '{}'::jsonb)) voter
  join public.users u on u.discord_user_id = voter.key
  join public.sports s on s.id = p.sport_id
  where p.tracking_scope = 'official'
    and p.created_at >= '2026-10-15T00:00:00-04:00'::timestamptz
    and voter.value = 'yes'
    and u.discord_user_id = any(public.website_capper_ids())
    and p.message_id is not null
    and (p.status = 'open' and p.settled_at is null
      or p.status in ('win', 'loss', 'void', 'partial') and p.settled_at is not null)
    and s.api_slug <> 'official'
  order by 2, 3;
$$;
revoke all on function public.public_favorite_cappers() from public, anon, authenticated;
grant execute on function public.public_favorite_cappers() to anon, authenticated;

commit;
