begin;

-- Tail policy (slip-v2): thumbs-down and Pass are retired. Cappers only Tail a slip with the
-- thumbs-up reaction. The author's confirmation is their tail. One more tail = official 2u,
-- two or more = official 3u (the cap). Parlays never exceed 1u; 3+ leg slips never go official.
-- Each capper may open a limited number of reviews per Eastern day (cancelled reviews count).

alter table public.capper_ballots
  add column opened_at timestamptz,
  add column cancelled_at timestamptz;

alter table public.capper_ballots
  drop constraint capper_ballot_roster,
  add constraint capper_ballot_roster check (
    eligible_voter_ids is null or (
      cardinality(eligible_voter_ids) >= 2
      and array_position(eligible_voter_ids, null) is null
      and (policy_version in ('slip-v1', 'slip-v2') or cardinality(eligible_voter_ids) between 3 and 5)
    )
  );

alter table public.plays add constraint tail_approval_stake check (
  team_approval ->> 'policy_version' is distinct from 'slip-v2' or (
    (team_approval ->> 'yes')::integer >= 2
    and (team_approval ->> 'yes')::integer = (team_approval -> 'counts' ->> 'yes')::integer
    and (team_approval ->> 'roster_size')::integer >= (team_approval ->> 'yes')::integer
    and team_approval -> 'voter_choices' ->> (team_approval ->> 'author_id') = 'yes'
    and legs between 1 and 2
    and units = case when legs >= 2 then 1 else least((team_approval ->> 'yes')::integer, 3) end
    and stars = units + 2
  ) is true
);

-- Never reinterpret open thumbs-up/down reviews or historical official stakes under the tail policy.
update public.capper_ballots set state = 'failed',
  error = 'Tail policy cutover: this thumbs-up/down review was closed. Submit a fresh slip for tail voting.'
where policy_version = 'slip-v1' and state in ('pending', 'processing');

create or replace function public.register_capper_slip(p_message_id text, p_review jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if jsonb_typeof(p_review) is distinct from 'object'
     or coalesce(p_review ->> 'author_id', '') !~ '^[0-9]{1,20}$'
     or coalesce(p_review ->> 'slip_base64', '') = '' then
    raise exception 'A slip requires its author and a saved original image.';
  end if;
  insert into public.capper_ballots(message_id, policy_version, review)
  values(p_message_id, 'slip-v2', p_review) on conflict do nothing;
  return found;
end;
$$;

drop function public.open_capper_slip(text, text, text[], jsonb, timestamptz, timestamptz);
create function public.open_capper_slip(
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
  if p_daily_limit is not null and (
      select count(*) from public.capper_ballots
      where review ->> 'author_id' = p_author_id and opened_at is not null
        and (opened_at at time zone 'America/New_York')::date
            = (clock_timestamp() at time zone 'America/New_York')::date
    ) >= p_daily_limit then
    raise exception 'Daily review limit reached: each capper may open % review(s) per day, counting cancelled reviews.', p_daily_limit;
  end if;
  update public.capper_ballots set eligible_voter_ids = p_roster,
    review = jsonb_set(review, '{parsed}', p_parsed),
    event_starts_at = p_start, cutoff_at = p_cutoff, opened_at = clock_timestamp(),
    response_states = jsonb_build_object(p_author_id, jsonb_build_object('yes', true))
  where message_id = p_message_id;
end;
$$;

create or replace function public.respond_capper_slip(p_message_id text, p_voter_id text, p_action text)
returns text language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots; response jsonb;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version is distinct from 'slip-v2' then
    raise exception 'Unknown slip review.';
  end if;
  if b.state <> 'pending' or b.cutoff_at is null or clock_timestamp() >= b.cutoff_at then
    return 'closed';
  end if;
  if not (p_voter_id = any(b.eligible_voter_ids)) then return 'ineligible'; end if;
  if p_voter_id = b.review ->> 'author_id' then return 'author-confirmed'; end if;
  if p_action not in ('yes_add', 'yes_remove') then
    raise exception 'The tail policy accepts only Tail reactions; there is no down vote or pass.';
  end if;
  response := jsonb_build_object('yes', p_action = 'yes_add');
  update public.capper_ballots set response_states = jsonb_set(response_states, array[p_voter_id], response)
  where message_id = p_message_id;
  return 'saved';
end;
$$;

create or replace function public.finalize_capper_slip(p_message_id text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots; choices jsonb; counts jsonb; final_decision jsonb;
  y integer; size integer; legs_count integer; units integer; qualified boolean;
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
  update public.capper_ballots set decision = final_decision, votes = counts,
    state = case when qualified then 'processing' else 'rejected' end
  where message_id = p_message_id;
  return final_decision;
end;
$$;

create or replace function public.cancel_capper_slip(p_message_id text, p_author_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state <> 'pending' or b.policy_version is distinct from 'slip-v2'
     or b.review ->> 'author_id' is distinct from p_author_id
     or (b.cutoff_at is not null and clock_timestamp() >= b.cutoff_at) then
    return false;
  end if;
  update public.capper_ballots set state = 'rejected', error = 'Cancelled by author.',
    cancelled_at = clock_timestamp()
  where message_id = p_message_id;
  return true;
end;
$$;

revoke all on function
  public.open_capper_slip(text,text,text[],jsonb,timestamptz,timestamptz,integer)
  from public, anon, authenticated;
grant execute on function
  public.open_capper_slip(text,text,text[],jsonb,timestamptz,timestamptz,integer)
  to service_role;

-- Free Play fallback claims now accept tail-policy slips and enforce the 1u parlay ceiling.
create or replace function public.claim_capper_slip_free_play(
  p_message_id text, p_channel_id text, p_reason text
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version not in ('slip-v1', 'slip-v2')
     or not (
       (b.state = 'failed' and b.decision is null)
       or (b.state = 'rejected' and b.decision #>> '{outcome}' = 'rejected')
     )
     or b.review_message_id is null or b.cutoff_at is null or b.event_starts_at is null
     or b.eligible_voter_ids is null or cardinality(b.eligible_voter_ids) < 2
     or b.play_id is not null or b.official_message_id is not null
     or b.official_channel_id is not null
     or b.error ilike '%original slip was edited or deleted%'
     or b.review ? 'personal_free_play'
     or exists (select 1 from public.plays p where p.ballot_message_id = p_message_id) then
    return false;
  end if;
  if b.review is null or jsonb_typeof(b.review #> '{parsed,legs}') is distinct from 'array' then
    return false;
  end if;
  if jsonb_array_length(b.review #> '{parsed,legs}') not between 1 and 10 then
    return false;
  end if;
  if (case
       when coalesce(b.review #>> '{parsed,units}', '') ~ '^[0-9]+([.][0-9]+)?$'
         then (b.review #>> '{parsed,units}')::numeric <= 0
           or (b.review #>> '{parsed,units}')::numeric > 3
           or (jsonb_array_length(b.review #> '{parsed,legs}') >= 2
               and (b.review #>> '{parsed,units}')::numeric > 1)
       else true
     end) then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', jsonb_build_object(
    'state', 'sending', 'channel_id', p_channel_id,
    'reason', left(coalesce(p_reason, ''), 1000), 'claimed_at', clock_timestamp()
  ), true)
  where message_id = p_message_id;
  return found;
end;
$$;

create or replace function public.claim_capper_slip_units_request(
  p_message_id text, p_channel_id text, p_reason text
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version not in ('slip-v1', 'slip-v2')
     or not (
       (b.state = 'failed' and b.decision is null)
       or (b.state = 'rejected' and b.decision #>> '{outcome}' = 'rejected')
     )
     or b.review_message_id is null or b.cutoff_at is null or b.event_starts_at is null
     or b.eligible_voter_ids is null or cardinality(b.eligible_voter_ids) < 2
     or b.play_id is not null or b.official_message_id is not null
     or b.official_channel_id is not null
     or b.error ilike '%original slip was edited or deleted%'
     or b.review ? 'personal_free_play'
     or exists (select 1 from public.plays p where p.ballot_message_id = p_message_id) then
    return false;
  end if;
  if b.review is null or jsonb_typeof(b.review #> '{parsed,legs}') is distinct from 'array'
     or jsonb_array_length(b.review #> '{parsed,legs}') not between 1 and 10 then
    return false;
  end if;
  if (case
       when coalesce(b.review #>> '{parsed,units}', '') ~ '^[0-9]+([.][0-9]+)?$'
         then (b.review #>> '{parsed,units}')::numeric > 0
           and (b.review #>> '{parsed,units}')::numeric <= 3
           and (jsonb_array_length(b.review #> '{parsed,legs}') < 2
                or (b.review #>> '{parsed,units}')::numeric <= 1)
       else false
     end) then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', jsonb_build_object(
    'state', 'requesting_units', 'channel_id', p_channel_id,
    'reason', left(coalesce(p_reason, ''), 1000), 'claimed_at', clock_timestamp()
  ), true)
  where message_id = p_message_id;
  return found;
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
     or p_units <= 0 or p_units > 3 then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version not in ('slip-v1', 'slip-v2')
     or not (
       (b.state = 'failed' and b.decision is null)
       or (b.state = 'rejected' and b.decision #>> '{outcome}' = 'rejected')
     )
     or b.review_message_id is null or b.cutoff_at is null or b.event_starts_at is null
     or b.eligible_voter_ids is null or cardinality(b.eligible_voter_ids) < 2
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
  if jsonb_array_length(b.review #> '{parsed,legs}') >= 2 and p_units > 1 then
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
