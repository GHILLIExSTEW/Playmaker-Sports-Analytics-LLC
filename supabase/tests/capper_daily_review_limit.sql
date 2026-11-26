-- Run after all slip-review migrations in a disposable database.
begin;

do $$
declare
  v_author_id text := '91000000000000000999';
  v_override_author_id text := '91000000000000000998';
  v_removable_id text := '91000000000000000023';
  v_pending_id text := '91000000000000000024';
  v_now timestamptz := clock_timestamp();
  v_message_id text;
  v_result jsonb;
  v_snowflake text := (floor(extract(epoch from clock_timestamp()) * 1000 - 1420070400000) * 4194304)::text;
  v_parsed jsonb := jsonb_build_object(
    'units', 1,
    'legs', jsonb_build_array(jsonb_build_object('selection', 'Test selection', 'odds', -110))
  );
  i integer;
begin
  perform public.set_capper_daily_review_limit(v_author_id, 1);
  for i in 1..6 loop
    v_message_id := (91000000000000000010::numeric + i)::text;
    perform public.register_capper_slip(v_message_id, jsonb_build_object(
      'author_id', v_author_id,
      'slip_base64', 'c2xpcA==',
      'parsed', v_parsed
    ));
    update public.capper_ballots
    set review_message_id = (91000000000000000100::numeric + i)::text
    where message_id = v_message_id;
    perform public.open_capper_slip(
      v_message_id, v_author_id, array[v_author_id, '43'], v_parsed,
      v_now + interval '3 hours', v_now + interval '2 hours', 1
    );
  end loop;

  for i in 1..4 loop
    v_message_id := (91000000000000000010::numeric + i)::text;
    perform public.respond_capper_slip(v_message_id, '43', 'yes_add');
    v_result := public.finalize_capper_slip(v_message_id);
    if i < 4 and v_result ->> 'outcome' is distinct from 'processing' then
      raise exception 'The first three qualifying reviews should be team plays';
    elsif i = 4 and v_result is not null then
      raise exception 'A review must wait for unresolved team publications';
    end if;
  end loop;
  update public.capper_ballots
  set state = 'published', official_message_id = v_snowflake
  where message_id in ('91000000000000000011', '91000000000000000012', '91000000000000000013');
  v_result := public.finalize_capper_slip('91000000000000000014');
  if v_result ->> 'outcome' is distinct from 'rejected'
     or v_result ->> 'team_daily_limit' is distinct from 'true'
     or v_result ->> 'units' is distinct from '2' then
    raise exception 'Fourth qualifying review must become a Free Play';
  end if;
  if (select review #>> '{parsed,units}' from public.capper_ballots
      where message_id = '91000000000000000014') is distinct from '1' then
    raise exception 'The Free Play stake was not capped at 1u';
  end if;
  if not public.claim_capper_slip_free_play(
    '91000000000000000014', '1513186440278704178', 'daily team play limit reached'
  ) then
    raise exception 'The over-limit Free Play could not be claimed';
  end if;
  update public.capper_ballots
  set eligible_voter_ids = array[v_author_id, '43', '44'],
      response_states = jsonb_build_object(
        v_author_id, jsonb_build_object('yes', true),
        '43', jsonb_build_object('yes', true), '44', jsonb_build_object('yes', true)
      )
  where message_id = '91000000000000000015';
  v_result := public.finalize_capper_slip('91000000000000000015');
  if v_result ->> 'team_daily_limit' is distinct from 'true'
     or v_result ->> 'units' is distinct from '3'
     or (select review #>> '{parsed,units}' from public.capper_ballots
         where message_id = '91000000000000000015') is distinct from '1' then
    raise exception 'A three-tail overflow review must be capped at 1u';
  end if;
  update public.capper_ballots
  set review = jsonb_set(review, '{parsed,legs}', jsonb_build_array(
    jsonb_build_object('selection', 'First leg', 'odds', -110),
    jsonb_build_object('selection', 'Second leg', 'odds', -110)
  ))
  where message_id = '91000000000000000016';
  perform public.respond_capper_slip('91000000000000000016', '43', 'yes_add');
  v_result := public.finalize_capper_slip('91000000000000000016');
  if v_result ->> 'team_daily_limit' is distinct from 'true'
     or v_result ->> 'units' is distinct from '1' then
    raise exception 'An overflow parlay must retain the earned 1u';
  end if;

  perform public.register_capper_slip('91000000000000000019', jsonb_build_object(
    'author_id', v_override_author_id, 'slip_base64', 'c2xpcA==', 'parsed', v_parsed
  ));
  update public.capper_ballots set review_message_id = '91000000000000000116'
  where message_id = '91000000000000000019';
  perform public.open_capper_slip(
    '91000000000000000019', v_override_author_id, array[v_override_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 1
  );
  perform public.respond_capper_slip('91000000000000000019', '43', 'yes_add');
  v_result := public.finalize_capper_slip('91000000000000000019');
  if v_result ->> 'outcome' is distinct from 'processing' then
    raise exception 'Another capper must have their own team-play allowance';
  end if;

  update public.capper_ballots
  set official_message_id = (
    floor(extract(epoch from (clock_timestamp() - interval '1 day')) * 1000 - 1420070400000) * 4194304
  )::text
  where message_id = '91000000000000000011';
  perform public.register_capper_slip('91000000000000000017', jsonb_build_object(
    'author_id', v_author_id, 'slip_base64', 'c2xpcA==', 'parsed', v_parsed
  ));
  update public.capper_ballots set review_message_id = '91000000000000000117'
  where message_id = '91000000000000000017';
  perform public.open_capper_slip(
    '91000000000000000017', v_author_id, array[v_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
  perform public.respond_capper_slip('91000000000000000017', '43', 'yes_add');
  v_result := public.finalize_capper_slip('91000000000000000017');
  if v_result ->> 'outcome' is distinct from 'processing' then
    raise exception 'A team play from the previous Eastern day must not count';
  end if;

  perform public.register_capper_slip(v_removable_id, jsonb_build_object(
    'author_id', v_override_author_id,
    'slip_base64', 'c2xpcA==',
    'parsed', v_parsed
  ));
  update public.capper_ballots
  set review_message_id = '91000000000000000123', state = 'failed', error = 'Test failure'
  where message_id = v_removable_id;
  insert into public.capper_review_record_days(record_date, forum_channel_id, thread_id)
  values ('2099-01-01', '44', '45')
  on conflict (record_date) do nothing;
  insert into public.capper_review_records(message_id, record_date, record_message_id)
  values (v_removable_id, '2099-01-01', '46');
  if not public.delete_failed_capper_slip(v_removable_id) then
    raise exception 'An unpublished failed review could not be removed';
  end if;
  if exists (select 1 from public.capper_ballots where message_id = v_removable_id)
     or exists (select 1 from public.capper_review_records where message_id = v_removable_id) then
    raise exception 'Failed review removal left database records behind';
  end if;

  perform public.register_capper_slip(v_pending_id, jsonb_build_object(
    'author_id', v_override_author_id,
    'slip_base64', 'c2xpcA==',
    'parsed', v_parsed
  ));
  if public.delete_failed_capper_slip(v_pending_id) then
    raise exception 'A pending review was incorrectly removable';
  end if;
end;
$$;

rollback;
