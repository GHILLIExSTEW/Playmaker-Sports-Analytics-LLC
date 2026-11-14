begin;

-- Keep the existing RPC signature for bots that have not yet restarted.
create or replace function public.open_capper_slip(
  p_message_id text, p_author_id text, p_roster text[], p_parsed jsonb,
  p_start timestamptz, p_cutoff timestamptz, p_daily_limit integer default 2
)
returns void language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version is distinct from 'slip-v2' or b.state <> 'pending'
     or b.cutoff_at is not null or b.review_message_id is null
     or b.review ->> 'author_id' is distinct from p_author_id then
    raise exception 'This slip is not awaiting its author confirmation.';
  end if;
  if p_roster is null or cardinality(p_roster) < 2
     or array_position(p_roster, null) is not null
     or exists(select 1 from unnest(p_roster) id where id !~ '^[0-9]{1,20}$')
     or (select count(distinct id) from unnest(p_roster) id) <> cardinality(p_roster)
     or not (p_author_id = any(p_roster)) then
    raise exception 'A verified roster including the author and another capper is required.';
  end if;
  if p_cutoff is null or p_start is null or p_cutoff <= clock_timestamp() or p_cutoff >= p_start then
    raise exception 'Set a future voting cutoff before the earliest event starts.';
  end if;
  if jsonb_typeof(p_parsed -> 'legs') is distinct from 'array'
     or jsonb_array_length(p_parsed -> 'legs') not between 1 and 2 then
    raise exception 'Only 1-2 leg slips can open an official review. Parlays of 3 or more legs post as personal Free Plays (1u maximum).';
  end if;
  update public.capper_ballots set eligible_voter_ids = p_roster,
    review = jsonb_set(review, '{parsed}', p_parsed),
    event_starts_at = p_start, cutoff_at = p_cutoff, opened_at = clock_timestamp(),
    response_states = jsonb_build_object(p_author_id, jsonb_build_object('yes', true))
  where message_id = p_message_id;
end;
$$;

create or replace function public.finalize_capper_slip(p_message_id text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots; choices jsonb; counts jsonb; final_decision jsonb;
  y integer; size integer; legs_count integer; units integer; qualified boolean;
  published_count integer; unresolved_count integer; today date;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state <> 'pending' or b.policy_version is distinct from 'slip-v2'
     or b.cutoff_at is null then return null; end if;
  if clock_timestamp() >= b.event_starts_at then
    update public.capper_ballots set state = 'failed',
      error = 'Event already started; no late official publication is permitted.'
    where message_id = p_message_id;
    return jsonb_build_object('outcome', 'expired');
  end if;
  select coalesce(jsonb_object_agg(voter, 'yes') filter(where tailed), '{}'::jsonb)
  into choices from (
    select voter, coalesce((b.response_states -> voter ->> 'yes')::boolean, false) tailed
    from unnest(b.eligible_voter_ids) voter
  ) votes;
  size := cardinality(b.eligible_voter_ids);
  if clock_timestamp() < b.cutoff_at and (select count(*) from jsonb_each(choices)) < size then
    return null;
  end if;
  if choices ->> (b.review ->> 'author_id') is distinct from 'yes' then
    raise exception 'The author must support the confirmed slip.';
  end if;
  select count(*) into y from jsonb_each_text(choices);
  counts := jsonb_build_object('yes', y);
  legs_count := jsonb_array_length(b.review -> 'parsed' -> 'legs');
  qualified := y >= 2 and legs_count between 1 and 2;
  units := case when not qualified then null when legs_count >= 2 then 1 else least(y, 3) end;
  final_decision := jsonb_build_object(
    'policy_version', 'slip-v2', 'author_id', b.review ->> 'author_id',
    'roster_size', size, 'yes', y, 'counts', counts, 'voter_choices', choices,
    'units', units, 'stars', units + 2, 'cutoff_at', b.cutoff_at, 'event_starts_at', b.event_starts_at,
    'outcome', case when qualified then 'processing' else 'rejected' end
  );
  if qualified then
    perform pg_advisory_xact_lock(hashtextextended('capper-team-play:' || (b.review ->> 'author_id'), 0));
    today := (clock_timestamp() at time zone 'America/New_York')::date;
    -- Discord message snowflakes retain the actual publication time, including old records.
    select count(*) into published_count
    from (
      select coalesce(p.message_id, prior.official_message_id) as discord_id
      from public.plays p
      left join public.capper_ballots prior on prior.message_id = p.ballot_message_id
      left join public.users u on u.id = p.user_id
      where p.tracking_scope = 'official'
        and coalesce(p.team_approval ->> 'author_id', prior.review ->> 'author_id',
                     prior.author_id, u.discord_user_id) = b.review ->> 'author_id'
      union all
      select prior.official_message_id
      from public.capper_ballots prior
      where coalesce(prior.review ->> 'author_id', prior.author_id) = b.review ->> 'author_id'
        and not exists (
          select 1 from public.plays p
          where p.ballot_message_id = prior.message_id and p.tracking_scope = 'official'
        )
    ) sent
    where case when sent.discord_id ~ '^[0-9]{1,20}$' then
      (to_timestamp((floor(sent.discord_id::numeric / 4194304) + 1420070400000) / 1000)
       at time zone 'America/New_York')::date = today
    else false end;

    select count(*) into unresolved_count
    from public.capper_ballots prior
    left join public.plays p on p.ballot_message_id = prior.message_id and p.tracking_scope = 'official'
    where prior.message_id <> p_message_id
      and prior.review ->> 'author_id' = b.review ->> 'author_id'
      and prior.decision ->> 'outcome' = 'processing'
      and prior.state in ('processing', 'failed')
      and prior.official_message_id is null and p.message_id is null;

    if published_count >= 2 then
      final_decision := final_decision || jsonb_build_object('outcome', 'rejected', 'team_daily_limit', true);
      update public.capper_ballots
      set review = jsonb_set(review, '{parsed,units}', to_jsonb(units), true),
        decision = final_decision, votes = counts, state = 'rejected'
      where message_id = p_message_id;
      return final_decision;
    elsif published_count + unresolved_count >= 2 then
      return null;
    end if;
    final_decision := final_decision || jsonb_build_object('team_limit_day', today);
  end if;
  update public.capper_ballots set decision = final_decision, votes = counts,
    state = case when qualified then 'processing' else 'rejected' end
  where message_id = p_message_id;
  return final_decision;
end;
$$;

commit;
