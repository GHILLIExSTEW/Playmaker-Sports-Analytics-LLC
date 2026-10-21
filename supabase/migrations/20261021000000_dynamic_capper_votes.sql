begin;

alter table public.capper_ballots
  add column eligible_voter_ids text[],
  add column policy_version text,
  add column decision jsonb,
  add constraint capper_ballot_roster check (
    eligible_voter_ids is null or (
      cardinality(eligible_voter_ids) between 3 and 5
      and array_position(eligible_voter_ids, null) is null
    )
  );
alter table public.capper_ballot_scan
  add column policy_started_at timestamptz not null default now();
alter table public.plays add column team_approval jsonb;
alter table public.plays add constraint saved_team_approval_stake check (
  team_approval is null or (
    tracking_scope = 'official'
    and team_approval ?& array['units', 'stars', 'roster_size', 'yes', 'policy_version']
    and units = (team_approval ->> 'units')::numeric
    and stars = (team_approval ->> 'stars')::integer
  )
);

-- No historical eligibility can be inferred safely during the policy transition.
update public.capper_ballots
set state = 'failed',
    error = 'Dynamic voting policy transition: reconcile this legacy ballot and repost with a fresh roster.'
where state = 'pending';

create table public.capper_chat_votes (
  source_message_id text primary key check (source_message_id ~ '^[0-9]{1,20}$'),
  ballot_message_id text not null references public.capper_ballots(message_id),
  source_channel_id text not null check (source_channel_id ~ '^[0-9]{1,20}$'),
  voter_id text not null check (voter_id ~ '^[0-9]{1,20}$'),
  choice text not null check (choice in ('yes', 'no', 'pass')),
  source_text text not null,
  source_edited_at timestamptz,
  state text not null default 'pending' check (state in ('pending', 'confirmed', 'rejected', 'invalid')),
  prompt_message_id text,
  prompted_at timestamptz,
  escalation_message_id text,
  escalated_at timestamptz,
  approver_ids text[] not null default '{}',
  confirmed_by text,
  created_at timestamptz not null default now(),
  unique (ballot_message_id, voter_id)
);
alter table public.capper_chat_votes enable row level security;
revoke all on public.capper_chat_votes from public, anon, authenticated;
grant all on public.capper_chat_votes to service_role;

create function public.verify_capper_chat_vote(p_source_id text, p_actor_id text, p_reject boolean)
returns text language plpgsql security definer set search_path = ''
as $$
declare
  v public.capper_chat_votes;
  b public.capper_ballots;
begin
  -- The same lock order is used by the publication claim.
  select cb.* into b from public.capper_ballots cb
  join public.capper_chat_votes cv on cv.ballot_message_id = cb.message_id
  where cv.source_message_id = p_source_id for update of cb;
  if not found or b.state <> 'pending' then
    raise exception 'This ballot is no longer accepting confirmations.';
  end if;
  select * into strict v from public.capper_chat_votes where source_message_id = p_source_id for update;
  if not (p_actor_id = any(b.eligible_voter_ids)) or b.eligible_voter_ids is null then
    raise exception 'Only eligible ballot cappers may verify a statement.';
  end if;
  if v.state in ('invalid', 'rejected') then
    raise exception 'This inference is no longer available. Cast a native poll vote.';
  end if;
  if p_reject then
    if p_actor_id <> v.voter_id then
      raise exception 'Only the statement author may reject their inferred vote.';
    end if;
    update public.capper_chat_votes set state = 'rejected', confirmed_by = p_actor_id
    where source_message_id = p_source_id;
    return 'rejected';
  end if;
  if p_actor_id = v.voter_id then
    update public.capper_chat_votes set state = 'confirmed', confirmed_by = p_actor_id
    where source_message_id = p_source_id;
    return 'confirmed';
  end if;
  if v.state = 'confirmed' then return 'confirmed'; end if;
  if v.escalated_at is null then
    raise exception 'Wait for the 10-minute escalation before verifying another capper''s statement.';
  end if;
  if not (p_actor_id = any(v.approver_ids)) then
    v.approver_ids := array_append(v.approver_ids, p_actor_id);
  end if;
  update public.capper_chat_votes
  set approver_ids = v.approver_ids,
      state = case when cardinality(v.approver_ids) >= 2 then 'confirmed' else state end,
      confirmed_by = case when cardinality(v.approver_ids) >= 2 then 'two-peers' else confirmed_by end
  where source_message_id = p_source_id;
  return case when cardinality(v.approver_ids) >= 2 then 'confirmed' else 'awaiting-second-capper' end;
end;
$$;
revoke all on function public.verify_capper_chat_vote(text, text, boolean) from public, anon, authenticated;
grant execute on function public.verify_capper_chat_vote(text, text, boolean) to service_role;

create function public.claim_rated_capper_ballot(p_message_id text, p_decision jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots;
  n integer;
  y integer;
  u integer;
begin
  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state <> 'pending' then return false; end if;
  n := cardinality(b.eligible_voter_ids);
  y := (p_decision ->> 'yes')::integer;
  if n is null or n not between 3 and 5 or b.policy_version <> 'roster-v1'
     or (p_decision ->> 'roster_size')::integer is distinct from n
     or p_decision ->> 'policy_version' is distinct from b.policy_version
     or y is null or y <= n / 2 or y > n then
    raise exception 'Invalid dynamic ballot decision.';
  end if;
  u := case when n = 3 then y else y - n + 3 end;
  if (p_decision ->> 'units')::integer is distinct from u
     or (p_decision ->> 'stars')::integer is distinct from u + 2 then
    raise exception 'Ballot stake does not match the approved roster scale.';
  end if;
  if b.decision is not null and b.decision <> p_decision then
    raise exception 'Saved ballot decision differs. Staff reconciliation is required.';
  end if;
  if exists (
    select 1 from jsonb_array_elements_text(coalesce(p_decision -> 'chat_voter_ids', '[]'::jsonb)) voter(id)
    left join public.capper_chat_votes cv on cv.ballot_message_id = p_message_id and cv.voter_id = voter.id
    where cv.state is distinct from 'confirmed'
      or cv.choice is distinct from (p_decision -> 'voter_choices' ->> voter.id)
  ) then
    raise exception 'A chat vote changed before publication. Reconcile the ballot.';
  end if;
  update public.capper_ballots
  set state = 'processing', decision = p_decision, votes = p_decision -> 'counts'
  where message_id = p_message_id;
  return true;
end;
$$;
revoke all on function public.claim_rated_capper_ballot(text, jsonb) from public, anon, authenticated;
grant execute on function public.claim_rated_capper_ballot(text, jsonb) to service_role;

commit;
