begin;

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
     or b.review_message_id is null
     or b.review ->> 'author_id' is null
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

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', jsonb_build_object(
    'state', 'sending', 'channel_id', p_channel_id,
    'reason', left(coalesce(p_reason, ''), 1000), 'claimed_at', clock_timestamp()
  ), true)
  where message_id = p_message_id;
  return found;
end;
$$;

commit;
