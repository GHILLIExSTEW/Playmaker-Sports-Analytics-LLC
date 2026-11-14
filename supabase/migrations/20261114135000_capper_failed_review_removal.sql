begin;

create function public.delete_failed_capper_slip(p_message_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$' then
    return false;
  end if;

  select * into b
  from public.capper_ballots
  where message_id = p_message_id
  for update;
  if not found
     or b.state is distinct from 'failed'
     or (
       b.policy_version is distinct from 'slip-v1'
       and b.policy_version is distinct from 'slip-v2'
     )
     or b.play_id is not null
     or b.official_message_id is not null
     or b.official_channel_id is not null
     or b.decision #>> '{outcome}' in ('processing', 'published')
     or b.review ? 'personal_free_play'
     or exists (
       select 1 from public.plays p where p.ballot_message_id = p_message_id
     )
     or exists (
       select 1 from public.capper_review_records r
       where r.message_id = p_message_id and r.record_message_id = 'sending'
     ) then
    return false;
  end if;

  delete from public.capper_review_records where message_id = p_message_id;
  delete from public.capper_ballots where message_id = p_message_id;
  return found;
end;
$$;

revoke all on function public.delete_failed_capper_slip(text) from public, anon, authenticated;
grant execute on function public.delete_failed_capper_slip(text) to service_role;

commit;
