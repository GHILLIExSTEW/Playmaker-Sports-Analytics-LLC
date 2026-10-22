begin;

create function public.play_insight_author(p_play_id bigint)
returns text language sql stable security definer set search_path = ''
as $$
  select case when p.tracking_scope = 'official' then
    case when p.team_approval ->> 'policy_version' = 'slip-v1'
      then p.team_approval ->> 'author_id' end
    else u.discord_user_id end
  from public.plays p join public.users u on u.id = p.user_id
  where p.id = p_play_id;
$$;
revoke all on function public.play_insight_author(bigint) from public, anon, authenticated;
grant execute on function public.play_insight_author(bigint) to service_role;

create or replace function public.capper_insight_context(p_play_id bigint default null)
returns table (play_id bigint, discord_user_id text, selection text, justification text, prompt_message_id text)
language sql stable security definer set search_path = ''
as $$
  select p.id, public.play_insight_author(p.id),
    coalesce(
      (select string_agg(l.selection, ' / ' order by l.leg_number) from public.play_legs l where l.play_id = p.id),
      nullif(p.team_name, ''), 'Official play'
    ),
    coalesce(i.justification, ''), i.prompt_message_id
  from public.plays p
  left join public.play_insights i on i.play_id = p.id
  where p.status = 'open' and p.settled_at is null and p.message_id is not null
    and not exists (select 1 from public.plays older where older.message_id = p.message_id and older.id < p.id)
    and public.play_insight_author(p.id) = any(public.website_capper_ids())
    and (p_play_id is null and i.prompt_message_id is null and coalesce(i.justification, '') = ''
         or p.id = p_play_id)
  order by p.created_at, p.id
  limit 50;
$$;

create or replace function public.save_capper_insight(p_play_id bigint, p_discord_user_id text, p_justification text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare author_id text;
begin
  select public.play_insight_author(p.id) into author_id from public.plays p
  where p.id = p_play_id and p.status = 'open' and p.settled_at is null
    and p.message_id is not null
    and not exists (select 1 from public.plays older where older.message_id = p.message_id and older.id < p.id)
  for update of p;
  if author_id is null or author_id is distinct from p_discord_user_id
     or not (author_id = any(public.website_capper_ids())) then
    raise exception 'Only the original current OPERATOR author of an open published pick can submit insight.'
      using errcode = '42501';
  end if;
  if p_justification is null or btrim(p_justification) = '' or char_length(p_justification) > 2000 then
    raise exception 'Insight must contain 1 to 2000 characters.';
  end if;
  insert into public.play_insights(play_id, justification, updated_at)
  values(p_play_id, btrim(p_justification), now())
  on conflict(play_id) do update set justification = excluded.justification, updated_at = excluded.updated_at;
  return true;
end;
$$;

create or replace function public.save_my_pick_insight(p_play_id bigint, p_justification text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare discord_id text; is_slip boolean;
begin
  select p.tracking_scope = 'official' and p.team_approval ->> 'policy_version' = 'slip-v1'
  into is_slip from public.plays p where p.id = p_play_id;
  if is_slip then
    if public.website_member_access() ->> 'state' is distinct from 'active' then
      raise exception 'Verified active original capper required.' using errcode = '42501';
    end if;
    select provider_id into strict discord_id from auth.identities
    where user_id = auth.uid() and provider = 'discord';
  elsif public.website_can_edit_all_cappers() then
    -- Preserve existing Owner editing for personal plays, never impersonate a team slip's author.
    discord_id := public.play_insight_author(p_play_id);
  else
    if public.website_member_access() ->> 'state' is distinct from 'active' or public.owned_capper_page() is null then
      raise exception 'Verified active OPERATOR page owner or current Owner role required.' using errcode = '42501';
    end if;
    select provider_id into strict discord_id from auth.identities
    where user_id = auth.uid() and provider = 'discord';
  end if;
  return public.save_capper_insight(p_play_id, discord_id, p_justification);
end;
$$;

drop function public.member_current_picks(text,text);
create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric,
  approval_yes integer, eligible_cappers integer,
  approval_policy text, approval_no integer, approval_pass integer,
  can_edit_insight boolean
)
language sql stable security definer set search_path = ''
as $$
  select source.*,
    (p.team_approval ->> 'yes')::integer, (p.team_approval ->> 'roster_size')::integer,
    p.team_approval ->> 'policy_version',
    (p.team_approval -> 'counts' ->> 'no')::integer,
    (p.team_approval -> 'counts' ->> 'pass')::integer,
    p.tracking_scope = 'official' and p.team_approval ->> 'policy_version' = 'slip-v1'
      and public.play_insight_author(p.id) = any(public.website_capper_ids())
      and exists(select 1 from auth.identities i where i.user_id = auth.uid()
        and i.provider = 'discord' and i.provider_id = public.play_insight_author(p.id))
  from public.archived_current_picks(p_sport, p_capper) source
  join public.plays p on p.id = source.id
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
  order by source.created_at desc, source.id desc;
$$;
revoke all on function public.member_current_picks(text,text) from public, anon, authenticated;
grant execute on function public.member_current_picks(text,text) to authenticated;
revoke all on function public.capper_insight_context(bigint),
  public.save_capper_insight(bigint,text,text) from public, anon, authenticated;
grant execute on function public.capper_insight_context(bigint),
  public.save_capper_insight(bigint,text,text) to service_role;
revoke all on function public.save_my_pick_insight(bigint,text) from public, anon, authenticated;
grant execute on function public.save_my_pick_insight(bigint,text) to authenticated;

commit;
