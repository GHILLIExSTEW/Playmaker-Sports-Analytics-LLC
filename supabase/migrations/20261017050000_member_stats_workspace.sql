begin;

create function public.require_member_stats_access(p_competition text default null)
returns void language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or public.website_member_access()->>'state' is distinct from 'active'
    then raise exception 'Active member access required'; end if;
  if p_competition is not null and p_competition not in
    ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
    then raise exception 'Unsupported research competition'; end if;
end;
$$;
revoke all on function public.require_member_stats_access(text) from public, anon, authenticated;

create function public.member_stats_games(
  p_competition text, p_query text default '', p_start timestamptz default null,
  p_end timestamptz default null, p_offset integer default 0
)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_member_stats_access(p_competition);
  if p_competition is null then raise exception 'Unsupported research competition'; end if;
  if p_offset is null or p_offset < 0 then raise exception 'Invalid search offset'; end if;
  if p_start is not null and p_end is not null and p_start >= p_end
    then raise exception 'Start date must not be after end date'; end if;
  if p_query is null or length(p_query) > 100 then raise exception 'Search must be at most 100 characters'; end if;
  return (
    with matches as materialized (
      select * from public.research_games g
      where g.competition = p_competition
        and (p_start is null or g.start_at >= p_start)
        and (p_end is null or g.start_at < p_end)
        and not exists (
          select 1 from regexp_split_to_table(lower(btrim(p_query)), '\s+') term
          where term <> '' and strpos(lower(concat_ws(' ', g.home_name, g.away_name, g.game_id, g.status)), term) = 0
        )
    )
    select jsonb_build_object(
      'total', (select count(*) from matches),
      'games', coalesce((select jsonb_agg(to_jsonb(page) order by start_at, game_id) from (
        select * from matches order by start_at, game_id limit 20 offset p_offset
      ) page), '[]'::jsonb)
    )
  );
end;
$$;
revoke all on function public.member_stats_games(text,text,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.member_stats_games(text,text,timestamptz,timestamptz,integer) to authenticated;

create function public.member_stats_game(p_competition text, p_game_id text)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  selected public.research_games;
begin
  perform public.require_member_stats_access(p_competition);
  if p_competition is null then raise exception 'Unsupported research competition'; end if;
  select * into selected from public.research_games
    where competition = p_competition and game_id = p_game_id;
  if not found then raise exception 'Select a cached game in this competition'; end if;
  return jsonb_build_object(
    'game', to_jsonb(selected),
    'history', coalesce((select jsonb_agg(to_jsonb(g) order by g.start_at desc, g.game_id) from (
      select * from public.research_games
      where competition = p_competition and start_at < selected.start_at
        and status in ('FT','AOT','AP','COMPLETED','FINISHED')
        and (home_id in (selected.home_id, selected.away_id) or away_id in (selected.home_id, selected.away_id))
      order by start_at desc, game_id limit 100
    ) g), '[]'::jsonb),
    'players', coalesce((select jsonb_agg(to_jsonb(s) order by s.start_at desc, s.game_id) from (
      select s.game_id::text, s.payload, s.synced_at, g.start_at, g.home_name, g.away_name, g.home_id, g.away_id
      from public.api_sports_player_game_stats s
      join public.research_games g on g.sport = s.sport_slug and g.game_id = s.game_id::text
      where g.competition = p_competition and g.start_at <= selected.start_at
        and (g.home_id in (selected.home_id,selected.away_id) or g.away_id in (selected.home_id,selected.away_id))
      order by g.start_at desc, g.game_id limit 10
    ) s), '[]'::jsonb),
    'generated_at', now()
  );
end;
$$;
revoke all on function public.member_stats_game(text,text) from public, anon;
grant execute on function public.member_stats_game(text,text) to authenticated;

create table public.member_stats_watchlist (
  user_id uuid not null references auth.users(id) on delete cascade,
  competition text not null check (competition in ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')),
  kind text not null check (kind in ('team','player')),
  entity_id text not null check (length(btrim(entity_id)) between 1 and 100),
  label text not null check (length(btrim(label)) > 0),
  primary key (user_id, competition, kind, entity_id)
);
alter table public.member_stats_watchlist enable row level security;
revoke all on public.member_stats_watchlist from public, anon, authenticated;

create function public.member_stats_watchlist()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_member_stats_access();
  return coalesce((select jsonb_agg(jsonb_build_object(
    'competition', w.competition, 'kind', w.kind, 'entity_id', w.entity_id, 'label', w.label
  ) order by w.competition, w.kind, w.entity_id)
    from public.member_stats_watchlist w where w.user_id = auth.uid()), '[]'::jsonb);
end;
$$;
revoke all on function public.member_stats_watchlist() from public, anon;
grant execute on function public.member_stats_watchlist() to authenticated;

create function public.save_member_stats_favorite(
  p_competition text, p_kind text, p_entity_id text, p_label text, p_saved boolean
)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  cached_label text;
begin
  perform public.require_member_stats_access(p_competition);
  if p_competition is null then raise exception 'Unsupported research competition'; end if;
  if p_kind is null or p_kind not in ('team','player') or p_entity_id is null
    or length(btrim(p_entity_id)) not between 1 and 100
    or p_label is null or length(btrim(p_label)) not between 1 and 200 or p_saved is null
    then raise exception 'Invalid favorite arguments'; end if;
  -- All writes for a user share a transaction lock, including deletes and upserts.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('member-stats:' || auth.uid()::text, 0));
  if not p_saved then
    delete from public.member_stats_watchlist
      where user_id = auth.uid() and competition = p_competition and kind = p_kind and entity_id = p_entity_id;
    return public.member_stats_watchlist();
  end if;
  if p_kind = 'team' then
    select case when g.home_id = p_entity_id then g.home_name else g.away_name end into cached_label
    from public.research_games g
    where g.competition = p_competition and p_entity_id in (g.home_id, g.away_id)
      and nullif(btrim(case when g.home_id = p_entity_id then g.home_name else g.away_name end), '') is not null
    order by g.start_at desc, g.game_id limit 1;
  else
    select identity.player->>'name' into cached_label
    from public.api_sports_player_game_stats s
    join public.research_games g on g.sport = s.sport_slug and g.game_id = s.game_id::text
    cross join lateral jsonb_array_elements(s.payload) entry
    cross join lateral (
      select entry->'player' as player
      where jsonb_typeof(entry->'player') = 'object' and entry->'game'->>'id' = s.game_id::text
      union all
      select participant->'player'
      from jsonb_array_elements(case when jsonb_typeof(entry->'groups') = 'array' then entry->'groups' else '[]'::jsonb end) grp
      cross join lateral jsonb_array_elements(case when jsonb_typeof(grp->'players') = 'array' then grp->'players' else '[]'::jsonb end) participant
    ) identity
    where g.competition = p_competition
      and entry->'team'->>'id' in (g.home_id, g.away_id)
      and jsonb_typeof(identity.player->'id') in ('string','number')
      and identity.player->>'id' = p_entity_id
      and jsonb_typeof(identity.player->'name') = 'string'
      and nullif(btrim(identity.player->>'name'), '') is not null
    order by g.start_at desc, g.game_id, identity.player->>'name' limit 1;
  end if;
  if cached_label is null then raise exception 'Select a cached entity in this competition'; end if;
  if not exists (select 1 from public.member_stats_watchlist
    where user_id = auth.uid() and competition = p_competition and kind = p_kind and entity_id = p_entity_id)
    and (select count(*) from public.member_stats_watchlist where user_id = auth.uid()) >= 100
    then raise exception 'Watchlist limit is 100 favorites'; end if;
  -- p_label is retained for the UI contract; only provider-cache labels are stored.
  insert into public.member_stats_watchlist(user_id,competition,kind,entity_id,label)
    values (auth.uid(),p_competition,p_kind,p_entity_id,cached_label)
    on conflict (user_id,competition,kind,entity_id) do update set label = excluded.label;
  return public.member_stats_watchlist();
end;
$$;
revoke all on function public.save_member_stats_favorite(text,text,text,text,boolean) from public, anon;
grant execute on function public.save_member_stats_favorite(text,text,text,text,boolean) to authenticated;

commit;
