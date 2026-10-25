alter table public.member_bets
  drop constraint member_bets_status_check,
  add constraint member_bets_status_check
    check (status in ('processing', 'draft', 'open', 'review', 'win', 'loss', 'void', 'rejected')),
  add column submitted_at timestamptz,
  add column challenge_events jsonb not null default '[]'::jsonb
    check (jsonb_typeof(challenge_events) = 'array'),
  add column challenge_eligibility text not null default 'excluded'
    check (challenge_eligibility in ('excluded', 'pending', 'eligible', 'rejected'));

-- Historical tickets have no verified Discord submission timestamp and stay excluded.
create function public.enforce_member_challenge() returns trigger
language plpgsql set search_path = ''
as $$
declare
  event jsonb;
  starts_at timestamptz;
  earliest timestamptz;
  verified_count integer := 0;
  leg_count integer;
begin
  if tg_op = 'UPDATE' then
    if new.submitted_at is distinct from old.submitted_at then
      raise exception 'The submission timestamp is immutable';
    end if;
    if old.confirmed_at is not null and new.confirmed_at is distinct from old.confirmed_at then
      raise exception 'The confirmation timestamp is immutable';
    end if;
    if old.confirmed_at is null and new.confirmed_at is not null then
      new.confirmed_at := clock_timestamp();
    end if;
  end if;
  if new.submitted_at is null then
    new.challenge_eligibility := 'excluded';
    return new;
  end if;
  if new.status = 'rejected' then
    new.challenge_eligibility := 'rejected';
    return new;
  end if;
  if new.odds < 100 then
    new.status := 'rejected';
    new.challenge_eligibility := 'rejected';
    new.review_reason := 'Only positive American ticket odds (+100 or higher) qualify. Negative-odds tickets are removed.';
    return new;
  end if;
  new.challenge_eligibility := 'pending';
  if new.confirmed_at is null then
    return new;
  end if;
  leg_count := jsonb_array_length(new.details->'legs');
  if coalesce(leg_count, 0) < 1 or leg_count > 10
     or jsonb_array_length(new.challenge_events) <> leg_count then
    raise exception 'Each ticket leg requires an event verification slot';
  end if;
  for event in select value from jsonb_array_elements(new.challenge_events) loop
    if event is not null and event <> 'null'::jsonb then
      if coalesce(event->>'start_at', '') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' then
        raise exception 'Event starts require an explicit timezone';
      end if;
      starts_at := (event->>'start_at')::timestamptz;
      if not isfinite(starts_at) then
        raise exception 'Event starts must be finite';
      end if;
      earliest := least(earliest, starts_at);
      verified_count := verified_count + 1;
    end if;
  end loop;
  if earliest is not null
     and (new.submitted_at >= earliest or new.confirmed_at >= earliest) then
    new.status := 'rejected';
    new.challenge_eligibility := 'rejected';
    new.review_reason := 'Upload and confirmation must both happen strictly before the earliest leg starts.';
  elsif new.odds >= 100 and verified_count = leg_count then
    new.challenge_eligibility := 'eligible';
  else
    new.status := 'review';
    new.review_reason := 'A moderator must verify every leg start time before this ticket can count.';
  end if;
  return new;
end;
$$;

create trigger member_challenge_eligibility
before insert or update on public.member_bets
for each row execute function public.enforce_member_challenge();

create table public.member_challenge_audit (
  id bigint generated always as identity primary key,
  bet_id bigint not null references public.member_bets(id),
  actor_id text not null,
  event_starts timestamptz[] not null,
  evidence text not null check (length(btrim(evidence)) > 0),
  eligibility text not null check (eligibility in ('eligible', 'rejected')),
  created_at timestamptz not null default now()
);
alter table public.member_challenge_audit enable row level security;
revoke all on public.member_challenge_audit from public, anon, authenticated;
grant all on public.member_challenge_audit to service_role;
grant usage, select on sequence public.member_challenge_audit_id_seq to service_role;

create function public.verify_member_challenge(
  p_bet_id bigint, p_actor_id text, p_starts timestamptz[], p_evidence text
) returns boolean
language plpgsql set search_path = ''
as $$
declare
  ticket public.member_bets;
  events jsonb := '[]'::jsonb;
  event jsonb;
  i integer;
begin
  select * into ticket from public.member_bets where id = p_bet_id for update;
  if not found or ticket.status not in ('open', 'review')
     or ticket.confirmed_at is null or ticket.challenge_eligibility <> 'pending' then
    return false;
  end if;
  if length(btrim(coalesce(p_actor_id, ''))) = 0
     or length(btrim(coalesce(p_evidence, ''))) = 0
     or coalesce(cardinality(p_starts), 0) <> jsonb_array_length(ticket.details->'legs') then
    raise exception 'Supply evidence and a start time for every leg, in ticket order';
  end if;
  for i in 1..cardinality(p_starts) loop
    if p_starts[i] is null or not isfinite(p_starts[i]) then
      raise exception 'Every leg needs a finite start time';
    end if;
    event := ticket.challenge_events->(i - 1);
    if event <> 'null'::jsonb and (event->>'start_at')::timestamptz is distinct from p_starts[i] then
      raise exception 'A verified API start time cannot be overridden';
    end if;
    events := events || jsonb_build_array(
      coalesce(nullif(event, 'null'::jsonb), jsonb_build_object('start_at', p_starts[i]))
    );
  end loop;
  update public.member_bets set challenge_events = events,
    review_reason = 'Start times verified; moderator settlement required.',
    card_dirty = true, updated_at = now()
    where id = p_bet_id returning * into ticket;
  insert into public.member_challenge_audit (bet_id, actor_id, event_starts, evidence, eligibility)
    values (p_bet_id, p_actor_id, p_starts, btrim(p_evidence), ticket.challenge_eligibility);
  return true;
end;
$$;

create or replace function public.settle_member_bet(
  p_bet_id bigint, p_actor_id text, p_result text, p_verification text, p_reason text
) returns boolean
language plpgsql set search_path = ''
as $$
begin
  if p_result not in ('win', 'loss', 'void')
     or p_verification not in ('API-verified', 'Moderator-settled')
     or length(btrim(coalesce(p_reason, ''))) = 0 then
    raise exception 'Invalid member bet settlement';
  end if;
  update public.member_bets
  set status = p_result, verification = p_verification,
      settled_at = now(), updated_at = now(), card_dirty = true
  where id = p_bet_id and status in ('open', 'review') and confirmed_at is not null
    and (submitted_at is null or challenge_eligibility = 'eligible');
  if not found then
    return false;
  end if;
  insert into public.member_bet_audit (bet_id, actor_id, result, verification, reason)
    values (p_bet_id, p_actor_id, p_result, p_verification, p_reason);
  return true;
end;
$$;

create index member_bets_challenge_month on public.member_bets (guild_id, channel_id, submitted_at)
  where challenge_eligibility in ('eligible', 'pending');

create function public.member_challenge_standings(p_guild_id text, p_channel_id text, p_month date)
returns table (owner_id text, wins bigint, losses bigint, unresolved bigint, "position" bigint)
language plpgsql stable set search_path = ''
as $$
begin
  if p_month is null or extract(day from p_month) <> 1 then
    raise exception 'Choose the first day of a calendar month';
  end if;
  return query
    with totals as (
      select b.owner_id,
        count(*) filter (where b.challenge_eligibility = 'eligible' and b.status = 'win') as wins,
        count(*) filter (where b.challenge_eligibility = 'eligible' and b.status = 'loss') as losses,
        count(*) filter (where b.status in ('processing', 'draft', 'open', 'review')) as unresolved
      from public.member_bets b
      where b.guild_id = p_guild_id and b.channel_id = p_channel_id
        and b.submitted_at >= (p_month::timestamp at time zone 'America/New_York')
        and b.submitted_at < ((p_month + interval '1 month') at time zone 'America/New_York')
        and b.challenge_eligibility in ('eligible', 'pending')
      group by b.owner_id
    )
    select t.owner_id, t.wins, t.losses, t.unresolved,
      dense_rank() over (order by t.wins desc)
    from totals t order by t.wins desc, t.owner_id;
end;
$$;

revoke all on function public.enforce_member_challenge() from public, anon, authenticated;
revoke all on function public.verify_member_challenge(bigint, text, timestamptz[], text) from public, anon, authenticated;
revoke all on function public.member_challenge_standings(text, text, date) from public, anon, authenticated;
grant execute on function public.verify_member_challenge(bigint, text, timestamptz[], text) to service_role;
grant execute on function public.member_challenge_standings(text, text, date) to service_role;
