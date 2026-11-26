begin;

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

    if published_count >= 3 then
      final_decision := final_decision || jsonb_build_object('outcome', 'rejected', 'team_daily_limit', true);
      update public.capper_ballots
      set review = jsonb_set(review, '{parsed,units}', to_jsonb(least(units, 1)), true),
        decision = final_decision, votes = counts, state = 'rejected'
      where message_id = p_message_id;
      return final_decision;
    elsif published_count + unresolved_count >= 3 then
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

create or replace function public.claim_capper_slip_free_play_units(
  p_message_id text, p_channel_id text, p_author_id text,
  p_prompt_channel_id text, p_prompt_message_id text, p_units numeric
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_author_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_prompt_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_prompt_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_units::text, '') !~ '^[0-9]+([.][0-9]+)?$'
     or p_units <= 0 or p_units > 1 then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version not in ('slip-v1', 'slip-v2')
     or not (
       (b.state = 'failed' and b.decision is null)
       or (b.state = 'rejected' and b.decision #>> '{outcome}' = 'rejected')
     )
     or b.review_message_id is null
     or b.play_id is not null or b.official_message_id is not null or b.official_channel_id is not null
     or b.error ilike '%original slip was edited or deleted%'
     or b.review ->> 'author_id' is distinct from p_author_id
     or b.review #>> '{personal_free_play,state}' is distinct from 'awaiting_units'
     or b.review #>> '{personal_free_play,channel_id}' is distinct from p_channel_id
     or b.review #>> '{personal_free_play,prompt_channel_id}' is distinct from p_prompt_channel_id
     or b.review #>> '{personal_free_play,prompt_message_id}' is distinct from p_prompt_message_id
     or exists (select 1 from public.plays p where p.ballot_message_id = p_message_id) then
    return false;
  end if;
  if jsonb_typeof(b.review #> '{parsed,legs}') is distinct from 'array'
     or jsonb_array_length(b.review #> '{parsed,legs}') not between 1 and 10 then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(
    jsonb_set(review, '{parsed,units}', to_jsonb(p_units), true),
    '{personal_free_play}', jsonb_build_object(
      'state', 'sending', 'channel_id', p_channel_id,
      'prompt_channel_id', p_prompt_channel_id, 'prompt_message_id', p_prompt_message_id,
      'units', p_units, 'provided_by', p_author_id, 'claimed_at', clock_timestamp()
    ), true
  )
  where message_id = p_message_id;
  return found;
end;
$$;

commit;
