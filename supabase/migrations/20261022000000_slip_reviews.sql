begin;

alter table public.capper_ballots
  drop constraint capper_ballot_roster,
  add constraint capper_ballot_roster check (
    eligible_voter_ids is null or (
      cardinality(eligible_voter_ids) >= 2
      and array_position(eligible_voter_ids, null) is null
      and (policy_version = 'slip-v1' or cardinality(eligible_voter_ids) between 3 and 5)
    )
  ),
  add column review jsonb,
  add column review_message_id text,
  add column event_starts_at timestamptz,
  add column cutoff_at timestamptz,
  add column response_states jsonb not null default '{}',
  add column reminder_sent boolean not null default false,
  add constraint slip_review_deadline check (
    cutoff_at is null or event_starts_at is not null and cutoff_at < event_starts_at
  );

alter table public.plays add constraint slip_approval_stake check (
  team_approval ->> 'policy_version' is distinct from 'slip-v1' or (
    (team_approval ->> 'yes')::integer >= 2
    and (team_approval ->> 'yes')::integer = (team_approval -> 'counts' ->> 'yes')::integer
    and (team_approval -> 'counts' ->> 'no')::integer >= 0
    and (team_approval -> 'counts' ->> 'pass')::integer >= 0
    and (team_approval ->> 'yes')::integer > (team_approval -> 'counts' ->> 'no')::integer
    and (team_approval ->> 'roster_size')::integer >=
      (team_approval ->> 'yes')::integer + (team_approval -> 'counts' ->> 'no')::integer
      + (team_approval -> 'counts' ->> 'pass')::integer
    and team_approval -> 'voter_choices' ->> (team_approval ->> 'author_id') = 'yes'
    and units = least((team_approval ->> 'yes')::integer - 1, 3)
    and stars = units + 2
  ) is true
);

-- Never reinterpret old ballots or historical official stakes under the new policy.
update public.capper_ballots set state = 'failed',
  error = 'Slip review cutover: reconcile this native ballot; submit a fresh slip for the new workflow.'
where state in ('pending', 'processing');
update public.capper_ballot_scan set started_at = now(), last_message_id = null;

create function public.register_capper_slip(p_message_id text, p_review jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if jsonb_typeof(p_review) is distinct from 'object'
     or coalesce(p_review ->> 'author_id', '') !~ '^[0-9]{1,20}$'
     or coalesce(p_review ->> 'slip_base64', '') = '' then
    raise exception 'A slip requires its author and a saved original image.';
  end if;
  insert into public.capper_ballots(message_id, policy_version, review)
  values(p_message_id, 'slip-v1', p_review) on conflict do nothing;
  return found;
end;
$$;

create function public.open_capper_slip(
  p_message_id text, p_author_id text, p_roster text[], p_parsed jsonb,
  p_start timestamptz, p_cutoff timestamptz
)
returns void language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version is distinct from 'slip-v1' or b.state <> 'pending'
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
     or jsonb_array_length(p_parsed -> 'legs') not between 1 and 10 then
    raise exception 'Confirm all selections and odds before opening review.';
  end if;
  update public.capper_ballots set eligible_voter_ids = p_roster,
    review = jsonb_set(review, '{parsed}', p_parsed),
    event_starts_at = p_start, cutoff_at = p_cutoff,
    response_states = jsonb_build_object(p_author_id, jsonb_build_object('yes', true))
  where message_id = p_message_id;
end;
$$;

create function public.respond_capper_slip(p_message_id text, p_voter_id text, p_action text)
returns text language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots; response jsonb; key text;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.policy_version is distinct from 'slip-v1' then
    raise exception 'Unknown slip review.';
  end if;
  if b.state <> 'pending' or b.cutoff_at is null or clock_timestamp() >= b.cutoff_at then
    return 'closed';
  end if;
  if not (p_voter_id = any(b.eligible_voter_ids)) then return 'ineligible'; end if;
  if p_voter_id = b.review ->> 'author_id' then return 'author-confirmed'; end if;
  response := coalesce(b.response_states -> p_voter_id, '{}'::jsonb);
  if p_action = 'pass' then
    response := '{"pass":true}'::jsonb;
  elsif p_action in ('yes_add', 'yes_remove', 'no_add', 'no_remove') then
    key := split_part(p_action, '_', 1);
    response := jsonb_set(response, array[key], to_jsonb(split_part(p_action, '_', 2) = 'add'));
    if split_part(p_action, '_', 2) = 'add' then response := response - 'pass'; end if;
  else
    raise exception 'Unknown slip response action.';
  end if;
  update public.capper_ballots set response_states = jsonb_set(response_states, array[p_voter_id], response)
  where message_id = p_message_id;
  return case when response @> '{"yes":true,"no":true}' then 'conflict' else 'saved' end;
end;
$$;

create function public.finalize_capper_slip(p_message_id text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots; choices jsonb; counts jsonb; final_decision jsonb;
  y integer; n integer; passed integer; size integer; units integer; qualified boolean;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state <> 'pending' or b.policy_version is distinct from 'slip-v1'
     or b.cutoff_at is null then return null; end if;
  if clock_timestamp() >= b.event_starts_at then
    update public.capper_ballots set state = 'failed',
      error = 'Event already started; no late official publication is permitted.'
    where message_id = p_message_id;
    return jsonb_build_object('outcome', 'expired');
  end if;
  select coalesce(jsonb_object_agg(voter, choice) filter(where choice is not null), '{}'::jsonb)
  into choices from (
    select voter, case
      when response @> '{"yes":true,"no":true}' then null
      when response @> '{"pass":true}' then 'pass'
      when response @> '{"yes":true}' then 'yes'
      when response @> '{"no":true}' then 'no'
    end choice
    from unnest(b.eligible_voter_ids) voter
    cross join lateral (select b.response_states -> voter as response) r
  ) votes;
  size := cardinality(b.eligible_voter_ids);
  if clock_timestamp() < b.cutoff_at and (select count(*) from jsonb_each(choices)) < size then
    return null;
  end if;
  if choices ->> (b.review ->> 'author_id') is distinct from 'yes' then
    raise exception 'The author must support the confirmed slip.';
  end if;
  select count(*) filter(where value = 'yes'), count(*) filter(where value = 'no'),
    count(*) filter(where value = 'pass') into y, n, passed from jsonb_each_text(choices);
  counts := jsonb_build_object('yes', y, 'no', n, 'pass', passed);
  qualified := y >= 2 and y > n;
  units := case when qualified then least(y - 1, 3) else null end;
  final_decision := jsonb_build_object(
    'policy_version', 'slip-v1', 'author_id', b.review ->> 'author_id',
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

create function public.cancel_capper_slip(p_message_id text, p_author_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare b public.capper_ballots;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state <> 'pending' or b.policy_version is distinct from 'slip-v1'
     or b.review ->> 'author_id' is distinct from p_author_id
     or (b.cutoff_at is not null and clock_timestamp() >= b.cutoff_at) then
    return false;
  end if;
  update public.capper_ballots set state = 'rejected', error = 'Cancelled by author.'
  where message_id = p_message_id;
  return true;
end;
$$;

create function public.fail_capper_slip(p_message_id text, p_reason text)
returns void language plpgsql security definer set search_path = ''
as $$
begin
  update public.capper_ballots set state = 'failed', error = p_reason
  where message_id = p_message_id and state in ('pending', 'processing');
end;
$$;

revoke all on function public.register_capper_slip(text,jsonb),
  public.open_capper_slip(text,text,text[],jsonb,timestamptz,timestamptz),
  public.respond_capper_slip(text,text,text),
  public.finalize_capper_slip(text),
  public.cancel_capper_slip(text,text), public.fail_capper_slip(text,text) from public, anon, authenticated;
grant execute on function public.register_capper_slip(text,jsonb),
  public.open_capper_slip(text,text,text[],jsonb,timestamptz,timestamptz),
  public.respond_capper_slip(text,text,text),
  public.finalize_capper_slip(text),
  public.cancel_capper_slip(text,text), public.fail_capper_slip(text,text) to service_role;

commit;
