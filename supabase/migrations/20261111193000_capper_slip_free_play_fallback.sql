begin;

create function public.claim_capper_slip_free_play(
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
  if not found or b.policy_version is distinct from 'slip-v1'
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

create function public.claim_capper_slip_units_request(
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
  if not found or b.policy_version is distinct from 'slip-v1'
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

create function public.complete_capper_slip_units_request(
  p_message_id text, p_channel_id text, p_prompt_channel_id text, p_prompt_message_id text
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_prompt_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_prompt_message_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found
     or (b.state is distinct from 'failed' and b.state is distinct from 'rejected')
     or b.review #>> '{personal_free_play,state}' is distinct from 'requesting_units'
     or b.review #>> '{personal_free_play,channel_id}' is distinct from p_channel_id then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', jsonb_build_object(
    'state', 'awaiting_units', 'channel_id', p_channel_id,
    'prompt_channel_id', p_prompt_channel_id, 'prompt_message_id', p_prompt_message_id,
    'reason', b.review #>> '{personal_free_play,reason}', 'asked_at', clock_timestamp()
  ), true)
  where message_id = p_message_id;
  return found;
end;
$$;

create function public.claim_capper_slip_free_play_units(
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
  if not found or b.policy_version is distinct from 'slip-v1'
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

create function public.complete_capper_slip_free_play(
  p_message_id text, p_channel_id text, p_discord_message_id text, p_play_id text
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_discord_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_play_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or (b.state is distinct from 'failed' and b.state is distinct from 'rejected')
     or b.review #>> '{personal_free_play,state}' is distinct from 'sending'
     or b.review #>> '{personal_free_play,channel_id}' is distinct from p_channel_id then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', jsonb_build_object(
    'state', 'sent', 'channel_id', p_channel_id,
    'message_id', p_discord_message_id, 'play_id', p_play_id, 'sent_at', clock_timestamp()
  ), true)
  where message_id = p_message_id;
  return found;
end;
$$;

create function public.capper_slip_unit_prompts()
returns table(message_id text, ballot_state text, prompt_channel_id text, prompt_message_id text)
language sql stable security definer set search_path = ''
as $$
  select b.message_id, b.state,
         b.review #>> '{personal_free_play,prompt_channel_id}',
         b.review #>> '{personal_free_play,prompt_message_id}'
  from public.capper_ballots b
  where b.state in ('failed', 'rejected')
    and b.review #>> '{personal_free_play,state}' = 'awaiting_units';
$$;

revoke all on function public.claim_capper_slip_free_play(text,text,text),
  public.claim_capper_slip_units_request(text,text,text),
  public.complete_capper_slip_units_request(text,text,text,text),
  public.claim_capper_slip_free_play_units(text,text,text,text,text,numeric),
  public.complete_capper_slip_free_play(text,text,text,text),
  public.capper_slip_unit_prompts() from public, anon, authenticated;
grant execute on function public.claim_capper_slip_free_play(text,text,text),
  public.claim_capper_slip_units_request(text,text,text),
  public.complete_capper_slip_units_request(text,text,text,text),
  public.claim_capper_slip_free_play_units(text,text,text,text,text,numeric),
  public.complete_capper_slip_free_play(text,text,text,text),
  public.capper_slip_unit_prompts() to service_role;

commit;
