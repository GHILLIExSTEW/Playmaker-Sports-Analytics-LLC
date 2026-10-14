begin;

alter table public.plays
  add column tracking_scope text not null default 'official'
    check (tracking_scope in ('official', 'capper', 'test')),
  add column stars integer check (stars in (3, 4, 5)),
  add column ballot_message_id text unique;
alter table public.plays add constraint team_ballot_rating
  check (stars is null or tracking_scope = 'official' and units = stars - 2);

create table public.capper_ballots (
  message_id text primary key check (message_id ~ '^[0-9]{1,20}$'),
  state text not null default 'pending'
    check (state in ('pending', 'processing', 'rejected', 'published', 'failed')),
  play_id bigint references public.plays(id),
  official_message_id text,
  votes jsonb,
  error text,
  created_at timestamptz not null default now()
);
create table public.capper_ballot_scan (
  singleton boolean primary key default true check (singleton),
  started_at timestamptz not null default '2015-01-01T00:00:00Z',
  last_message_id text
);
insert into public.capper_ballot_scan (singleton) values (true);
alter table public.capper_ballots enable row level security;
alter table public.capper_ballot_scan enable row level security;
revoke all on public.capper_ballots, public.capper_ballot_scan from public, anon, authenticated;
grant all on public.capper_ballots, public.capper_ballot_scan to service_role;

create function public.claim_capper_ballot(p_message_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  update public.capper_ballots set state = 'processing'
  where message_id = p_message_id and state = 'pending';
  return found;
end;
$$;
revoke all on function public.claim_capper_ballot(text) from public, anon, authenticated;
grant execute on function public.claim_capper_ballot(text) to service_role;

drop function public.public_settled_results();
create function public.public_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text
)
language sql stable security definer set search_path = ''
as $$
  with published as (
    select distinct on (p.message_id) p.*
    from public.plays p
    where p.message_id is not null and p.settled_at is not null
      and p.status in ('win', 'loss', 'void', 'partial')
      and p.tracking_scope in ('official', 'capper')
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
    p.tracking_scope
  from published p
  left join public.sports s on s.id = p.sport_id
  left join public.users u on u.id = p.user_id
  order by p.settled_at desc, p.id desc;
$$;
revoke all on function public.public_settled_results() from public, anon, authenticated;
grant execute on function public.public_settled_results() to anon, authenticated;

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
  select source.id, source.created_at, source.sport,
    case when p.tracking_scope = 'official' then 'Official Playmaker Picks' else source.capper end,
    source.avatar_url, source.selection, coalesce(insight.justification, ''), source.odds, source.units
  from public.website_current_picks_source(p_sport, null) source
  join public.plays p on p.id = source.id
  join public.users u on u.id = p.user_id
  left join public.play_insights insight on insight.play_id = p.id
  where (p.tracking_scope = 'official'
    or p.tracking_scope = 'capper' and u.discord_user_id = any(capper_ids))
    and (p_capper is null or p_capper = case when p.tracking_scope = 'official'
      then 'Official Playmaker Picks' else source.capper end)
  order by source.created_at desc, source.id desc;
end;
$$;

create or replace function public.public_favorite_cappers()
returns table (sport_id bigint, sport text, capper_name text)
language sql stable security definer set search_path = ''
as $$
  select distinct p.sport_id, s.name,
    coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks')
  from public.plays p
  join public.sports s on s.id = p.sport_id
  join public.users u on u.id = p.user_id
  where p.tracking_scope = 'capper'
    and u.discord_user_id = any(public.website_capper_ids())
    and p.message_id is not null
    and (p.status = 'open' and p.settled_at is null
      or p.status in ('win', 'loss', 'void', 'partial') and p.settled_at is not null)
    and s.api_slug <> 'official'
  order by s.name, 3;
$$;

commit;
