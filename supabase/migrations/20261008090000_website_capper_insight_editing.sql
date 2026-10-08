begin;

create function public.save_my_pick_insight(p_play_id bigint, p_justification text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
  discord_id text;
begin
  if public.website_member_access() ->> 'state' <> 'active'
     or public.owned_capper_page() is null then
    raise exception 'Verified active OPERATOR page owner required.' using errcode = '42501';
  end if;
  select provider_id into strict discord_id from auth.identities
  where user_id = auth.uid() and provider = 'discord';
  return public.save_capper_insight(p_play_id, discord_id, p_justification);
end;
$$;
revoke all on function public.save_my_pick_insight(bigint, text) from public, anon, authenticated;
grant execute on function public.save_my_pick_insight(bigint, text) to authenticated;

commit;
