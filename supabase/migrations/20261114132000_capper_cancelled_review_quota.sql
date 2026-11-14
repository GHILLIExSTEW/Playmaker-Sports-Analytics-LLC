begin;

alter table public.capper_ballots
  add column review_deleted_at timestamptz;

update public.capper_ballots b
set review_deleted_at = r.source_deleted_at
from public.capper_review_records r
where r.message_id = b.message_id
  and r.source_deleted_at is not null
  and b.review_deleted_at is null;

create or replace function public.mark_capper_slip_deleted(p_message_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  update public.capper_ballots
  set review_deleted_at = coalesce(review_deleted_at, clock_timestamp())
  where message_id = p_message_id
    and policy_version in ('slip-v1', 'slip-v2')
    and review_deleted_at is null;
  return found;
end;
$$;

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
  if p_daily_limit is not null and (
      select count(*) from public.capper_ballots opened
      where opened.review ->> 'author_id' = p_author_id and opened.opened_at is not null
        and (opened.opened_at at time zone 'America/New_York')::date
            = (clock_timestamp() at time zone 'America/New_York')::date
        and opened.cancelled_at is null
        and (
          opened.review_deleted_at is null
          or opened.state = 'processing'
          or opened.decision #>> '{outcome}' = 'processing'
          or opened.play_id is not null
          or opened.official_message_id is not null
          or opened.official_channel_id is not null
          or exists (
            select 1 from public.plays p where p.ballot_message_id = opened.message_id
          )
        )
    ) >= p_daily_limit then
    raise exception 'Daily review limit reached: each capper may open % review(s) per day, excluding cancelled and deleted unpublished reviews.', p_daily_limit;
  end if;
  update public.capper_ballots set eligible_voter_ids = p_roster,
    review = jsonb_set(review, '{parsed}', p_parsed),
    event_starts_at = p_start, cutoff_at = p_cutoff, opened_at = clock_timestamp(),
    response_states = jsonb_build_object(p_author_id, jsonb_build_object('yes', true))
  where message_id = p_message_id;
end;
$$;

revoke all on function public.mark_capper_slip_deleted(text) from public, anon, authenticated;
grant execute on function public.mark_capper_slip_deleted(text) to service_role;

commit;
