update public.api_sports_events
set home_id = nullif(raw_event #>> '{fighters,first,id}', ''),
    home_name = nullif(btrim(raw_event #>> '{fighters,first,name}'), ''),
    home_logo = nullif(raw_event #>> '{fighters,first,logo}', ''),
    away_id = nullif(raw_event #>> '{fighters,second,id}', ''),
    away_name = nullif(btrim(raw_event #>> '{fighters,second,name}'), ''),
    away_logo = nullif(raw_event #>> '{fighters,second,logo}', ''),
    event_name = coalesce(nullif(btrim(raw_event #>> '{fighters,first,name}'), ''), 'Home')
        || ' vs ' ||
        coalesce(nullif(btrim(raw_event #>> '{fighters,second,name}'), ''), 'Away'),
    league_name = coalesce(nullif(raw_event ->> 'slug', ''), 'MMA'),
    round_name = nullif(raw_event ->> 'category', '')
where sport_slug = 'mma'
  and jsonb_typeof(raw_event -> 'fighters') = 'object';
