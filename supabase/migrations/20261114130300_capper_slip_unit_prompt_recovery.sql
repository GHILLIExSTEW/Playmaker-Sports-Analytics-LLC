begin;

-- Invalidate the old button while replacing its saved message ID; interrupted sends require staff reconciliation.
create function public.claim_capper_slip_units_prompt_reissue(
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
  if not found or b.state not in ('failed', 'rejected')
     or b.review ->> 'author_id' is null
     or b.review #>> '{personal_free_play,state}' is distinct from 'awaiting_units'
     or b.review #>> '{personal_free_play,channel_id}' is distinct from p_channel_id
     or b.review #>> '{personal_free_play,prompt_channel_id}' is distinct from p_prompt_channel_id
     or b.review #>> '{personal_free_play,prompt_message_id}' is distinct from p_prompt_message_id
     or b.play_id is not null or b.official_message_id is not null or b.official_channel_id is not null
     or exists (select 1 from public.plays p where p.ballot_message_id = p_message_id) then
    return false;
  end if;

  update public.capper_ballots
  set review = jsonb_set(
    jsonb_set(review, '{personal_free_play,state}', '"reissuing_units"'::jsonb, true),
    '{personal_free_play,reissue_started_at}', to_jsonb(clock_timestamp()), true
  )
  where message_id = p_message_id;
  return found;
end;
$$;

create function public.complete_capper_slip_units_prompt_reissue(
  p_message_id text, p_channel_id text,
  p_old_prompt_channel_id text, p_old_prompt_message_id text,
  p_new_prompt_channel_id text, p_new_prompt_message_id text
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  b public.capper_ballots;
  delivery jsonb;
begin
  if coalesce(p_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_old_prompt_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_old_prompt_message_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_new_prompt_channel_id, '') !~ '^[0-9]{1,20}$'
     or coalesce(p_new_prompt_message_id, '') !~ '^[0-9]{1,20}$'
     or p_new_prompt_channel_id is distinct from p_old_prompt_channel_id
     or p_new_prompt_message_id = p_old_prompt_message_id then
    return false;
  end if;

  select * into b from public.capper_ballots where message_id = p_message_id for update;
  if not found or b.state not in ('failed', 'rejected')
     or b.review #>> '{personal_free_play,state}' is distinct from 'reissuing_units'
     or b.review #>> '{personal_free_play,channel_id}' is distinct from p_channel_id
     or b.review #>> '{personal_free_play,prompt_channel_id}' is distinct from p_old_prompt_channel_id
     or b.review #>> '{personal_free_play,prompt_message_id}' is distinct from p_old_prompt_message_id
     or b.play_id is not null or b.official_message_id is not null or b.official_channel_id is not null
     or exists (select 1 from public.plays p where p.ballot_message_id = p_message_id) then
    return false;
  end if;

  delivery := b.review #> '{personal_free_play}';
  delivery := jsonb_set(delivery, '{state}', '"awaiting_units"'::jsonb, true);
  delivery := jsonb_set(delivery, '{prompt_channel_id}', to_jsonb(p_new_prompt_channel_id), true);
  delivery := jsonb_set(delivery, '{prompt_message_id}', to_jsonb(p_new_prompt_message_id), true);
  delivery := (delivery - 'reissue_started_at') || jsonb_build_object('asked_at', clock_timestamp());

  update public.capper_ballots
  set review = jsonb_set(review, '{personal_free_play}', delivery, true)
  where message_id = p_message_id;
  return found;
end;
$$;

create function public.capper_slip_unit_prompt_reissues()
returns table(message_id text, prompt_channel_id text, prompt_message_id text)
language sql stable security definer set search_path = ''
as $$
  select b.message_id,
         b.review #>> '{personal_free_play,prompt_channel_id}',
         b.review #>> '{personal_free_play,prompt_message_id}'
  from public.capper_ballots b
  where b.state in ('failed', 'rejected')
    and b.review #>> '{personal_free_play,state}' = 'reissuing_units';
$$;

revoke all on function public.claim_capper_slip_units_prompt_reissue(text,text,text,text),
  public.complete_capper_slip_units_prompt_reissue(text,text,text,text,text,text),
  public.capper_slip_unit_prompt_reissues() from public, anon, authenticated;
grant execute on function public.claim_capper_slip_units_prompt_reissue(text,text,text,text),
  public.complete_capper_slip_units_prompt_reissue(text,text,text,text,text,text),
  public.capper_slip_unit_prompt_reissues() to service_role;

commit;
