begin;

create function public.search_research_games(
  p_competition text, p_query text default '', p_start timestamptz default null,
  p_end timestamptz default null, p_offset integer default 0
)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_operator_research_access();
  if p_competition is null or p_competition not in
    ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
    then raise exception 'Unsupported research competition'; end if;
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
revoke all on function public.search_research_games(text,text,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.search_research_games(text,text,timestamptz,timestamptz,integer) to authenticated;

commit;
