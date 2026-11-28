begin;

create table public.capper_tracking_settings (
  user_id bigint primary key references public.users(id) on delete cascade,
  starts_at date not null,
  updated_at timestamptz not null default now()
);
alter table public.capper_tracking_settings enable row level security;
revoke all on public.capper_tracking_settings from public, anon, authenticated;
grant select, insert, update, delete on public.capper_tracking_settings to service_role;

drop function public.public_settled_results();
create function public.public_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text, capper_record boolean,
  analysis text
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
      and (
        p.tracking_scope <> 'capper'
        or p.created_at >= coalesce(
          (select s.starts_at::timestamp at time zone 'America/New_York'
           from public.capper_tracking_settings s where s.user_id = p.user_id),
          '-infinity'::timestamptz
        )
      )
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
    p.tracking_scope = 'capper',
    coalesce(i.justification, '')
  from published p
  left join public.sports s on s.id = p.sport_id
  left join public.users u on u.id = p.user_id
  left join public.play_insights i on i.play_id = p.id
  order by p.settled_at desc, p.created_at desc;
$$;
revoke all on function public.public_settled_results() from public, anon, authenticated;
grant execute on function public.public_settled_results() to anon, authenticated;

drop function public.public_capper_settled_results();
create function public.public_capper_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text, capper_record boolean,
  analysis text, record_type text
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
    p.odds, stake.units, p.status,
    case p.status
      when 'win' then round(stake.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end, 2)
      when 'loss' then -stake.units
      when 'partial' then round(stake.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end * 0.5, 2)
      else 0::numeric
    end,
    p.tracking_scope, true,
    coalesce(i.justification, ''),
    case when author.is_author then 'authored' else 'tailed' end
  from published p
  cross join lateral jsonb_each_text(coalesce(p.team_approval -> 'voter_choices', '{}'::jsonb)) voter
  join public.users u on u.discord_user_id = voter.key
  left join public.capper_ballots b on b.message_id = p.ballot_message_id
  cross join lateral (
    select voter.key = coalesce(p.team_approval ->> 'author_id', b.author_id)
      or u.id = p.user_id as is_author
  ) author
  cross join lateral (
    select case when author.is_author then p.units
      else least(p.units, 1::numeric) end as units
  ) stake
  left join public.sports s on s.id = p.sport_id
  left join public.play_insights i on i.play_id = p.id
  where voter.value = 'yes'
    and p.created_at >= coalesce(
      (select setting.starts_at::timestamp at time zone 'America/New_York'
       from public.capper_tracking_settings setting where setting.user_id = u.id),
      '-infinity'::timestamptz
    )
  order by p.settled_at desc, p.created_at desc, u.display_name;
$$;
revoke all on function public.public_capper_settled_results() from public, anon, authenticated;
grant execute on function public.public_capper_settled_results() to anon, authenticated;

commit;
