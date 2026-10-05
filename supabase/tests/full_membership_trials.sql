-- Run after the membership migrations in a disposable PostgreSQL database.
begin;

create function pg_temp.trial_snapshot(p_membership text, p_user text, p_discord text)
returns jsonb language sql as $$
  select pg_catalog.jsonb_build_object(
    'membership_id', p_membership, 'whop_user_id', p_user,
    'discord_user_id', p_discord, 'account_id', 'biz_trial_test',
    'plan_id', 'plan_full_trial', 'status', 'active', 'paid', false,
    'payment_id', null, 'trial_verified', true,
    'paid_from', now() - interval '24 hours',
    'paid_through', now() + interval '144 hours',
    'source_updated_at', now() - interval '24 hours',
    'verified_at', now() - interval '1 minute',
    'verification_reason', 'Test provider verification'
  );
$$;

create function pg_temp.expect_access(p_discord text, p_expected boolean)
returns void language plpgsql as $$
begin
  if public.discord_has_trial_access(
    p_discord, 'biz_trial_test', array['plan_full_trial']
  ) is distinct from p_expected then
    raise exception 'Unexpected trial eligibility for %; expected %', p_discord, p_expected;
  end if;
end;
$$;

do $$
declare
  original jsonb := pg_temp.trial_snapshot('mem_trial_first', 'user_trial_first', '99101');
  snapshot jsonb;
begin
  if not public.record_whop_trial_membership(original) then
    raise exception 'First trial snapshot was not saved';
  end if;
  perform pg_temp.expect_access('99101', true);
  if public.discord_has_paid_access('99101') then
    raise exception 'A free trial was incorrectly treated as paid';
  end if;
  if public.discord_has_trial_access('99101', 'biz_wrong', array['plan_full_trial'])
     or public.discord_has_trial_access('99101', 'biz_trial_test', array['plan_wrong']) then
    raise exception 'Trial leaked across seller or plan boundaries';
  end if;
  if public.record_whop_trial_membership(original) then
    raise exception 'Duplicate snapshot was accepted as a new verification';
  end if;
  if (select count(*) from public.membership_trial_claims
      where account_id = 'biz_trial_test') <> 1 then
    raise exception 'Duplicate claims recorded';
  end if;
  if not exists (
    select 1 from public.trial_discord_member_ids('biz_trial_test', array['plan_full_trial'])
    where discord_user_id = '99101'
  ) then
    raise exception 'Trial identity missing from role synchronization';
  end if;

  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_repeat', 'user_trial_first', '99101')
  );
  if (select trial_eligible from public.whop_trial_memberships
      where membership_id = 'mem_trial_repeat') then
    raise exception 'Repeat membership restarted a trial';
  end if;
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_other_user', 'user_trial_other', '99101')
  );
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_other_discord', 'user_trial_first', '99102')
  );
  perform pg_temp.expect_access('99102', false);
  if exists (
    select 1 from public.whop_trial_memberships
    where membership_id in ('mem_trial_other_user', 'mem_trial_other_discord')
      and trial_eligible
  ) then
    raise exception 'Reusing either identity bypassed the claim';
  end if;

  snapshot := original || pg_catalog.jsonb_build_object(
    'verified_at', now(), 'source_updated_at', now(), 'status', 'canceled'
  );
  perform public.record_whop_trial_membership(snapshot);
  perform pg_temp.expect_access('99101', false);
  if not exists (select 1 from public.membership_trial_claims
                 where membership_id = 'mem_trial_first') then
    raise exception 'Cancellation erased the consumed claim';
  end if;

  snapshot := original || pg_catalog.jsonb_build_object(
    'verified_at', now() + interval '1 second', 'source_updated_at', now(),
    'paid_from', now(), 'paid_through', now() + interval '168 hours'
  );
  perform public.record_whop_trial_membership(snapshot);
  perform pg_temp.expect_access('99101', false);
  snapshot := original || pg_catalog.jsonb_build_object(
    'verified_at', now() + interval '2 seconds', 'source_updated_at', now(),
    'discord_user_id', '99103'
  );
  perform public.record_whop_trial_membership(snapshot);
  perform pg_temp.expect_access('99103', false);

  -- Restore the original, still-unexpired window; re-sync is not a new trial.
  perform public.record_whop_trial_membership(original || pg_catalog.jsonb_build_object(
    'verified_at', now() + interval '3 seconds', 'source_updated_at', now()
  ));
  perform pg_temp.expect_access('99101', true);
  if public.record_whop_trial_membership(original) then
    raise exception 'An older source update overwrote current entitlement';
  end if;

  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_stale', 'user_trial_stale', '99104')
      || pg_catalog.jsonb_build_object('verified_at', now() - interval '15 minutes')
  );
  perform pg_temp.expect_access('99104', false);
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_expired', 'user_trial_expired', '99105')
      || pg_catalog.jsonb_build_object(
        'paid_from', now() - interval '168 hours', 'paid_through', now()
      )
  );
  perform pg_temp.expect_access('99105', false);
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_unverified', 'user_trial_unverified', '99106')
      || pg_catalog.jsonb_build_object('trial_verified', false, 'trial_eligible', true)
  );
  perform pg_temp.expect_access('99106', false);
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_completed', 'user_trial_completed', '99107')
      || pg_catalog.jsonb_build_object('status', 'completed')
  );
  perform pg_temp.expect_access('99107', true);

  begin
    perform public.record_whop_trial_membership(
      pg_temp.trial_snapshot('mem_trial_fake_payment', 'user_fake', '99108')
        || '{"paid": true, "payment_id": "pay_fake"}'::jsonb
    );
    raise exception 'Fake paid trial was accepted';
  exception when raise_exception then
    if sqlerrm <> 'Trial snapshots must not claim payment' then raise; end if;
  end;
  begin
    perform public.record_whop_trial_membership(
      pg_temp.trial_snapshot('mem_trial_long', 'user_long', '99109')
        || pg_catalog.jsonb_build_object('paid_through', now() + interval '168 hours')
    );
    raise exception 'Eight-day trial was accepted';
  exception when check_violation then null;
  end;

  -- A refunded former customer still has historical paid evidence.
  insert into public.whop_membership_audit (membership_id, snapshot)
  values ('mem_old_paid', pg_catalog.jsonb_build_object(
    'account_id', 'biz_trial_test', 'whop_user_id', 'user_old_paid',
    'discord_user_id', '99110', 'paid', true,
    'paid_from', now() - interval '30 days'
  ));
  perform public.record_whop_trial_membership(
    pg_temp.trial_snapshot('mem_trial_former_paid', 'user_old_paid', '99110')
  );
  perform pg_temp.expect_access('99110', false);

  -- Trial expiry/cancellation must not remove a separately purchased pass.
  perform public.record_whop_membership(pg_catalog.jsonb_build_object(
    'membership_id', 'mem_trial_to_paid', 'whop_user_id', 'user_trial_first',
    'discord_user_id', '99101', 'account_id', 'biz_trial_test',
    'plan_id', 'plan_highroller_paid', 'status', 'completed',
    'paid_from', now() - interval '30 minutes',
    'paid_through', now() + interval '720 hours',
    'paid', true, 'payment_id', 'pay_trial_to_paid', 'cancel_at_period_end', false,
    'source_updated_at', now(), 'verified_at', now(),
    'verification_reason', 'Test verified paid pass'
  ));
  perform public.record_whop_trial_membership(original || pg_catalog.jsonb_build_object(
    'verified_at', now() + interval '4 seconds', 'source_updated_at', now(), 'status', 'expired'
  ));
  perform pg_temp.expect_access('99101', false);
  if not public.discord_has_paid_access('99101')
     or not public.discord_has_paid_plan_access(
       '99101', 'biz_trial_test', array['plan_highroller_paid']
     ) then
    raise exception 'Trial expiration removed overlapping paid access';
  end if;

  if pg_catalog.has_table_privilege('anon', 'public.membership_trial_claims', 'SELECT')
     or pg_catalog.has_table_privilege('authenticated', 'public.whop_trial_memberships', 'INSERT')
     or pg_catalog.has_function_privilege('anon', 'public.discord_has_trial_access(text,text,text[])', 'EXECUTE')
     or pg_catalog.has_table_privilege('service_role', 'public.membership_trial_claims', 'DELETE') then
    raise exception 'Unexpected public access or removable trial claims';
  end if;
  if not exists (select 1 from public.whop_trial_membership_audit
                 where membership_id = 'mem_trial_first') then
    raise exception 'Trial changes were not audited';
  end if;
end;
$$;

set local role service_role;
select public.record_whop_trial_membership(
  pg_temp.trial_snapshot('mem_trial_service_role', 'user_trial_service_role', '99111')
);
do $$
begin
  if not public.discord_has_trial_access(
    '99111', 'biz_trial_test', array['plan_full_trial']
  ) then
    raise exception 'Service role could not record and read a valid trial';
  end if;
end;
$$;
reset role;

rollback;
