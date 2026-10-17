-- Run after the BANG migrations in a disposable database; all fixtures roll back.
begin;

do $$
declare
  test_user bigint;
  test_sport bigint;
  test_play bigint;
  first_attempt uuid;
  second_attempt uuid;
  rejected boolean;
  old_revision uuid;
begin
  insert into public.users (discord_user_id, username, display_name, role, is_active)
  values ('99999999999999999991', 'bang_sql_test', 'BANG SQL Test', 'official', true)
  returning id into test_user;
  insert into public.sports (name, api_slug, is_active)
  values ('BANG SQL Test', 'bang_sql_test', true) returning id into test_sport;
  insert into public.plays (
    user_id, sport_id, units, legs, odds, status, tracking_scope, message_id
  ) values (test_user, test_sport, 2, 1, 109, 'win', 'official', '99999999999999999992')
  returning id into test_play;

  first_attempt := public.begin_play_bang(test_play, '101', false);
  if first_attempt is null or public.begin_play_bang(test_play, '101', false) is not null then
    raise exception 'Claim uniqueness failed';
  end if;
  rejected := false;
  begin
    perform public.recover_play_bang(test_play, '101', first_attempt, '42', null);
  exception when others then
    if sqlerrm not like '%still in flight%' then raise; end if;
    rejected := true;
  end;
  if not rejected then raise exception 'In-flight retry was allowed'; end if;

  if not public.fail_play_bang(test_play, '101', first_attempt, 'Connection lost', true) then
    raise exception 'Uncertain delivery was not recorded';
  end if;
  if not exists (
       select 1 from public.play_bang_notifications
       where play_id = test_play and state = 'uncertain' and last_error = 'Connection lost'
     ) then
    raise exception 'Uncertain delivery was not recorded';
  end if;
  second_attempt := public.recover_play_bang(test_play, '101', first_attempt, '42', null);
  if second_attempt = first_attempt then raise exception 'Recovery did not rotate the token'; end if;
  rejected := false;
  begin
    perform public.finish_play_bang(test_play, '101', first_attempt, '900', 'win');
  exception when others then
    if sqlerrm not like '%attempt changed%' then raise; end if;
    rejected := true;
  end;
  if not rejected then raise exception 'Stale attempt overwrote recovery'; end if;

  perform public.finish_play_bang(test_play, '101', second_attempt, '901', 'win');
  if public.fail_play_bang(test_play, '101', second_attempt, 'Late failure', true) then
    raise exception 'A late failure overwrote delivered state';
  end if;
  rejected := false;
  begin
    perform public.recover_play_bang(test_play, '101', second_attempt, '42', null);
  exception when others then
    if sqlerrm not like '%already tracked%' then raise; end if;
    rejected := true;
  end;
  if not rejected then raise exception 'Delivered message was allowed to resend'; end if;

  update public.plays set status = 'loss' where id = test_play;
  select revision into old_revision from public.play_bang_events where play_id = test_play;
  if old_revision is null then raise exception 'Result change did not queue a BANG event'; end if;
  update public.plays set units = 3 where id = test_play;
  if public.finish_play_bang_event(test_play, old_revision) then
    raise exception 'A stale acknowledgement discarded a newer result event';
  end if;
  select revision into old_revision from public.play_bang_events where play_id = test_play;
  if not public.finish_play_bang_event(test_play, old_revision) then
    raise exception 'Current result event could not be acknowledged';
  end if;
  if not exists (select 1 from public.stale_play_bangs(false) s where s.play_id = test_play)
     or exists (select 1 from public.stale_play_bangs(true) s where s.play_id = test_play) then
    raise exception 'Correction scan or testing isolation failed';
  end if;
  perform public.finish_play_bang(test_play, '101', second_attempt, '901', 'loss');
  if exists (select 1 from public.stale_play_bangs(false) s where s.play_id = test_play) then
    raise exception 'Completed correction still appeared stale';
  end if;

  update public.plays set tracking_scope = 'capper', status = 'win' where id = test_play;
  if not exists (select 1 from public.stale_play_bangs(false) s where s.play_id = test_play) then
    raise exception 'Misclassified legacy capper BANG was not selected for correction';
  end if;
  rejected := false;
  begin
    perform public.begin_play_bang(test_play, '202', false);
  exception when others then
    if sqlerrm not like '%official winning play%' then raise; end if;
    rejected := true;
  end;
  if not rejected then raise exception 'Capper win created a BANG'; end if;

  update public.plays set tracking_scope = 'official', status = 'open' where id = test_play;
  rejected := false;
  begin
    perform public.begin_play_bang(test_play, '202', false);
  exception when others then
    if sqlerrm not like '%official winning play%' then raise; end if;
    rejected := true;
  end;
  if not rejected then raise exception 'Open play created a BANG'; end if;

  update public.plays set status = 'win' where id = test_play;
  first_attempt := public.begin_play_bang(test_play, '303', true);
  update public.play_bang_notifications
  set updated_at = now() - interval '11 minutes'
  where play_id = test_play and channel_id = '303';
  second_attempt := public.recover_play_bang(test_play, '303', first_attempt, '42', '902');
  if not exists (
    select 1 from public.play_bang_notifications
    where play_id = test_play and channel_id = '303' and state = 'delivered'
      and message_id = '902' and reconciled_by = '42' and reconciled_at is not null
      and attempt_id = second_attempt and displayed_status is null
  ) then
    raise exception 'Link-existing recovery or audit tracking failed';
  end if;
  if not exists (select 1 from public.stale_play_bangs(true) s where s.play_id = test_play) then
    raise exception 'Linked message was not selected for synchronization';
  end if;
  if has_function_privilege('anon', 'public.begin_play_bang(bigint,text,boolean)', 'execute')
     or has_function_privilege('authenticated', 'public.recover_play_bang(bigint,text,uuid,text,text)', 'execute')
     or has_table_privilege('anon', 'public.play_bang_notifications', 'select')
     or has_table_privilege('anon', 'public.play_card_assets', 'select')
     or has_table_privilege('authenticated', 'public.play_card_assets', 'select')
     or has_table_privilege('anon', 'public.play_bang_events', 'select')
     or not has_function_privilege('service_role', 'public.recover_play_bang(bigint,text,uuid,text,text)', 'execute') then
    raise exception 'BANG privileges are incorrect';
  end if;
end;
$$;

rollback;
