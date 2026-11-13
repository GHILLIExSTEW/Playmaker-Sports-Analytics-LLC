-- Run after the display-play and personal-stats migrations in a disposable database.
begin;

do $$
declare
  member_user bigint;
  other_user bigint;
  test_sport bigint;
  duplicate_first bigint;
  duplicate_later bigint;
  team_play bigint;
  tailed_play bigint;
  unrelated_play bigint;
  returned_ids bigint[];
begin
  insert into public.users (discord_user_id, username, display_name, role, is_active)
  values ('99999999999999999981', 'personal_stats_member', 'Personal Stats Member', 'official', true)
  returning id into member_user;
  insert into public.users (discord_user_id, username, display_name, role, is_active)
  values ('99999999999999999982', 'personal_stats_other', 'Personal Stats Other', 'official', true)
  returning id into other_user;
  insert into public.sports (name, api_slug, is_active)
  values ('Personal Stats Test', 'personal_stats_test', true)
  returning id into test_sport;

  insert into public.plays (
    user_id, sport_id, units, legs, odds, status, tracking_scope, message_id, settled_at
  ) values (other_user, test_sport, 1, 1, 100, 'win', 'capper', 'personal-stats-duplicate', now())
  returning id into duplicate_first;
  insert into public.plays (
    user_id, sport_id, units, legs, odds, status, tracking_scope, message_id, settled_at
  ) values (member_user, test_sport, 1, 1, 100, 'win', 'capper', 'personal-stats-duplicate', now())
  returning id into duplicate_later;

  insert into public.plays (
    user_id, sport_id, units, stars, legs, odds, status, tracking_scope, message_id, settled_at, team_approval
  ) values (
    other_user, test_sport, 1, 3, 1, 100, 'loss', 'official', 'personal-stats-team', now(),
    '{"units":1,"stars":3,"roster_size":3,"yes":2,"policy_version":"slip-v1","author_id":"99999999999999999982","counts":{"yes":2,"no":0,"pass":0},"voter_choices":{"99999999999999999981":"yes","99999999999999999982":"yes"}}'::jsonb
  ) returning id into team_play;

  insert into public.plays (
    user_id, sport_id, units, legs, odds, status, tracking_scope, message_id, settled_at
  ) values (other_user, test_sport, 1, 1, 100, 'win', 'official', 'personal-stats-tail', now())
  returning id into tailed_play;
  insert into public.play_tails (play_id, discord_user_id)
  values (tailed_play, '99999999999999999981');

  insert into public.plays (
    user_id, sport_id, units, legs, odds, status, tracking_scope, message_id, settled_at
  ) values (other_user, test_sport, 1, 1, 100, 'win', 'official', 'personal-stats-unrelated', now())
  returning id into unrelated_play;

  select array_agg(p.id order by p.id) into returned_ids
  from public.member_personal_stats_plays('99999999999999999981') p;
  if returned_ids is distinct from array[duplicate_first, team_play, tailed_play] then
    raise exception 'Scoped personal history was incorrect: %', returned_ids;
  end if;
  if duplicate_first = duplicate_later or unrelated_play = any(returned_ids) then
    raise exception 'Duplicate or unrelated fixture assumptions were invalid';
  end if;
  if not has_function_privilege(
    'service_role', 'public.member_personal_stats_plays(text)', 'execute'
  ) or has_function_privilege(
    'anon', 'public.member_personal_stats_plays(text)', 'execute'
  ) then
    raise exception 'Personal-history RPC privileges are incorrect';
  end if;
end;
$$;

rollback;
