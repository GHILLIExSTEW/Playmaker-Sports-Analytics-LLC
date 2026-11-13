begin;

create or replace function public.member_personal_stats_plays(p_discord_user_id text)
returns setof public.plays
language sql
stable
security definer
set search_path = ''
as $$
  with relevant_messages as (
    select distinct p.message_id
    from public.display_plays p
    where p.message_id is not null
      and (
        p.user_id = (
          select u.id from public.users u
          where u.discord_user_id = p_discord_user_id
          order by u.id limit 1
        )
        or p.team_approval -> 'voter_choices' ->> p_discord_user_id = 'yes'
        or exists (
          select 1 from public.play_tails t
          where t.play_id = p.id and t.discord_user_id = p_discord_user_id
        )
      )
  ),
  published as (
    select distinct on (p.message_id) p.*
    from public.display_plays p
    join relevant_messages r using (message_id)
    where p.message_id is not null
    order by p.message_id, p.id
  )
  select p.*
  from published p
  where p.status in ('win', 'loss', 'void', 'partial');
$$;

revoke all on function public.member_personal_stats_plays(text) from public, anon, authenticated;
grant execute on function public.member_personal_stats_plays(text) to service_role;

commit;
