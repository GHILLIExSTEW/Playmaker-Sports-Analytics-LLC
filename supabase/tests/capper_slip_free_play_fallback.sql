-- Run after the slip-review and Free Play fallback migrations in a disposable database.
begin;

do $$
declare
  v_message_id text := '91000000000000000001';
  v_reviewed_message_id text := '91000000000000000002';
  v_free_play_message_id text := '91000000000000000003';
  v_prompt_channel_id text := '1328130555568455710';
  v_prompt_message_id text := '91000000000000000007';
  claimed boolean;
begin
  perform public.register_capper_slip(v_message_id, jsonb_build_object(
    'author_id', '42',
    'slip_base64', 'c2xpcA==',
    'parsed', jsonb_build_object(
      'units', 1,
      'legs', jsonb_build_array(
        jsonb_build_object('selection', 'Test selection', 'odds', -110)
      )
    )
  ));
  update public.capper_ballots
  set review_message_id = v_reviewed_message_id,
      event_starts_at = now() + interval '2 hours',
      cutoff_at = now() + interval '1 hour',
      eligible_voter_ids = array['42', '43'],
      response_states = '{"42":{"yes":true}}'::jsonb,
      state = 'failed',
      error = 'saved review thread unavailable'
  where capper_ballots.message_id = v_message_id;

  claimed := public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'thread unavailable');
  if not claimed then raise exception 'Eligible technical failure could not claim Free Play'; end if;
  if public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'duplicate') then
    raise exception 'A second Free Play claim was allowed';
  end if;
  if not public.complete_capper_slip_free_play(
    v_message_id, '1513186440278704178', v_free_play_message_id, '475'
  ) then
    raise exception 'Free Play delivery marker was not completed';
  end if;
  if public.complete_capper_slip_free_play(
    v_message_id, '1513186440278704178', v_free_play_message_id, '475'
  ) then
    raise exception 'A completed Free Play marker was completed twice';
  end if;
  if (select review #>> '{personal_free_play,state}' from public.capper_ballots
      where capper_ballots.message_id = v_message_id) <> 'sent' then
    raise exception 'Free Play delivery state was not saved';
  end if;
  if (select review #>> '{personal_free_play,play_id}' from public.capper_ballots
      where capper_ballots.message_id = v_message_id) <> '475' then
    raise exception 'Tracked Free Play ID was not saved';
  end if;

  v_message_id := '91000000000000000004';
  perform public.register_capper_slip(v_message_id, jsonb_build_object(
    'author_id', '42',
    'slip_base64', 'c2xpcA==',
    'parsed', jsonb_build_object(
      'units', 1,
      'legs', jsonb_build_array(
        jsonb_build_object('selection', 'Approved selection', 'odds', -110)
      )
    )
  ));
  update public.capper_ballots
  set review_message_id = v_reviewed_message_id,
      event_starts_at = now() + interval '2 hours',
      cutoff_at = now() + interval '1 hour',
      eligible_voter_ids = array['42', '43'],
      state = 'failed',
      decision = '{"outcome":"processing"}'::jsonb,
      error = 'official publication failed'
  where capper_ballots.message_id = v_message_id;
  if public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'official error') then
    raise exception 'An approved decision was incorrectly downgraded to a Free Play';
  end if;

  v_message_id := '91000000000000000005';
  perform public.register_capper_slip(v_message_id, jsonb_build_object(
    'author_id', '42',
    'slip_base64', 'c2xpcA==',
    'parsed', jsonb_build_object('legs', jsonb_build_array(
      jsonb_build_object('selection', 'Missing units selection', 'odds', -110)
    ))
  ));
  update public.capper_ballots
  set review_message_id = v_reviewed_message_id,
      event_starts_at = now() + interval '2 hours',
      cutoff_at = now() + interval '1 hour',
      eligible_voter_ids = array['42', '43'],
      state = 'rejected',
      decision = '{"outcome":"rejected"}'::jsonb
  where capper_ballots.message_id = v_message_id;
  if public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'missing units') then
    raise exception 'A Free Play without capper-supplied units was claimed';
  end if;
  if not public.claim_capper_slip_units_request(v_message_id, '1513186440278704178', 'missing units') then
    raise exception 'An unqualified ballot without units could not request them';
  end if;
  if public.claim_capper_slip_units_request(v_message_id, '1513186440278704178', 'duplicate prompt') then
    raise exception 'A duplicate unit prompt claim was allowed';
  end if;
  if not public.complete_capper_slip_units_request(
    v_message_id, '1513186440278704178', v_prompt_channel_id, v_prompt_message_id
  ) then
    raise exception 'The unit prompt message was not saved';
  end if;
  if not exists (
    select 1 from public.capper_slip_unit_prompts()
    where message_id = v_message_id
      and prompt_channel_id = v_prompt_channel_id
      and prompt_message_id = v_prompt_message_id
  ) then
    raise exception 'The saved unit prompt was not available for button restoration';
  end if;
  if public.claim_capper_slip_free_play_units(
    v_message_id, '1513186440278704178', '99', v_prompt_channel_id, v_prompt_message_id, 1.5
  ) then
    raise exception 'A non-author was allowed to choose Free Play units';
  end if;
  if public.claim_capper_slip_free_play_units(
    v_message_id, '1513186440278704178', '42', v_prompt_channel_id, v_prompt_message_id, 3.5
  ) then
    raise exception 'A unit stake over 3u was accepted';
  end if;
  if not public.claim_capper_slip_free_play_units(
    v_message_id, '1513186440278704178', '42', v_prompt_channel_id, v_prompt_message_id, 1.5
  ) then
    raise exception 'The author could not set units from the saved prompt';
  end if;
  if (select review #>> '{parsed,units}' from public.capper_ballots
      where capper_ballots.message_id = v_message_id) <> '1.5' then
    raise exception 'The author-supplied unit stake was not saved';
  end if;
  if not public.complete_capper_slip_free_play(
    v_message_id, '1513186440278704178', v_free_play_message_id, '476'
  ) then
    raise exception 'The author-selected regular Free Play was not completed';
  end if;

  v_message_id := '91000000000000000008';
  perform public.register_capper_slip(v_message_id, jsonb_build_object(
    'author_id', '42',
    'slip_base64', 'c2xpcA==',
    'parsed', jsonb_build_object(
      'units', 4,
      'legs', jsonb_build_array(
        jsonb_build_object('selection', 'Oversized stake selection', 'odds', -110)
      )
    )
  ));
  update public.capper_ballots
  set review_message_id = v_reviewed_message_id,
      event_starts_at = now() + interval '2 hours',
      cutoff_at = now() + interval '1 hour',
      eligible_voter_ids = array['42', '43'],
      state = 'rejected',
      decision = '{"outcome":"rejected"}'::jsonb
  where capper_ballots.message_id = v_message_id;
  if public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'units exceed maximum') then
    raise exception 'A stake over 3u was posted as a regular Free Play';
  end if;
  if not public.claim_capper_slip_units_request(v_message_id, '1513186440278704178', 'units exceed maximum') then
    raise exception 'An invalid stake could not request author-selected units';
  end if;

  v_message_id := '91000000000000000006';
  perform public.register_capper_slip(v_message_id, jsonb_build_object(
    'author_id', '42',
    'slip_base64', 'c2xpcA==',
    'parsed', jsonb_build_object(
      'units', 1,
      'legs', jsonb_build_array(
        jsonb_build_object('selection', 'Rejected selection', 'odds', -110)
      )
    )
  ));
  update public.capper_ballots
  set review_message_id = v_reviewed_message_id,
      event_starts_at = now() + interval '2 hours',
      cutoff_at = now() + interval '1 hour',
      eligible_voter_ids = array['42', '43'],
      state = 'rejected',
      decision = '{"outcome":"rejected"}'::jsonb
  where capper_ballots.message_id = v_message_id;
  if not public.claim_capper_slip_free_play(v_message_id, '1513186440278704178', 'not enough votes') then
    raise exception 'An unqualified ballot with capper units could not claim a regular Free Play';
  end if;
end;
$$;

rollback;
