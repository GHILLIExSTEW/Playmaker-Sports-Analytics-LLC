-- Run after the capper slip review, daily quota, and per-capper override migrations in a disposable database.
begin;

do $$
declare
  v_author_id text := '91000000000000000999';
  v_override_author_id text := '91000000000000000998';
  v_first_id text := '91000000000000000011';
  v_now timestamptz := clock_timestamp();
  v_message_id text;
  v_parsed jsonb := jsonb_build_object(
    'units', 1,
    'legs', jsonb_build_array(jsonb_build_object('selection', 'Test selection', 'odds', -110))
  );
  i integer;
begin
  for i in 1..5 loop
    v_message_id := (91000000000000000010::numeric + i)::text;
    perform public.register_capper_slip(v_message_id, jsonb_build_object(
      'author_id', v_author_id,
      'slip_base64', 'c2xpcA==',
      'parsed', v_parsed
    ));
    update public.capper_ballots
    set review_message_id = (91000000000000000100::numeric + i)::text
    where message_id = v_message_id;
  end loop;

  perform public.open_capper_slip(
    v_first_id, v_author_id, array[v_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
  if not public.cancel_capper_slip(v_first_id, v_author_id) then
    raise exception 'The test review could not be cancelled';
  end if;

  v_message_id := '91000000000000000012';
  perform public.open_capper_slip(
    v_message_id, v_author_id, array[v_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
  if not public.mark_capper_slip_deleted(v_message_id) then
    raise exception 'The test review could not be marked deleted';
  end if;

  v_message_id := '91000000000000000013';
  perform public.open_capper_slip(
    v_message_id, v_author_id, array[v_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
  v_message_id := '91000000000000000014';
  perform public.open_capper_slip(
    v_message_id, v_author_id, array[v_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );

  v_message_id := '91000000000000000015';
  begin
    perform public.open_capper_slip(
      v_message_id, v_author_id, array[v_author_id, '43'], v_parsed,
      v_now + interval '3 hours', v_now + interval '2 hours', 2
    );
    raise exception 'A third unpublished review was allowed';
  exception when others then
    if sqlerrm not like 'Daily review limit reached:%' then
      raise;
    end if;
  end;

  for i in 1..2 loop
    v_message_id := (91000000000000000020::numeric + i)::text;
    perform public.register_capper_slip(v_message_id, jsonb_build_object(
      'author_id', v_override_author_id,
      'slip_base64', 'c2xpcA==',
      'parsed', v_parsed
    ));
    update public.capper_ballots
    set review_message_id = (91000000000000000110::numeric + i)::text
    where message_id = v_message_id;
  end loop;
  if not public.set_capper_daily_review_limit(v_override_author_id, 1) then
    raise exception 'The per-capper limit was not saved';
  end if;
  perform public.open_capper_slip(
    '91000000000000000021', v_override_author_id, array[v_override_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
  begin
    perform public.open_capper_slip(
      '91000000000000000022', v_override_author_id, array[v_override_author_id, '43'], v_parsed,
      v_now + interval '3 hours', v_now + interval '2 hours', 2
    );
    raise exception 'The per-capper limit of one was not enforced';
  exception when others then
    if sqlerrm not like 'Daily review limit reached:%' then
      raise;
    end if;
  end;
  if not public.set_capper_daily_review_limit(v_override_author_id, 0) then
    raise exception 'The per-capper limit could not be cleared';
  end if;
  perform public.open_capper_slip(
    '91000000000000000022', v_override_author_id, array[v_override_author_id, '43'], v_parsed,
    v_now + interval '3 hours', v_now + interval '2 hours', 2
  );
end;
$$;

rollback;
