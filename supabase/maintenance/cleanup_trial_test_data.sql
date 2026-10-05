-- Run this entire DO statement as the database owner in Supabase SQL Editor.
-- Default: read-only preview. Review the NOTICE row counts in the output.
-- To delete the reviewed fixtures, change apply_cleanup to true and rerun.
do $cleanup$
declare
  apply_cleanup boolean := false;
  item record;
  matched_count bigint;
  fixture_filter text := $filter$
    exists (
      select 1 from (values
        ('mem_trial_first', 'user_trial_first', '99101', 'plan_full_trial'),
        ('mem_trial_repeat', 'user_trial_first', '99101', 'plan_full_trial'),
        ('mem_trial_other_user', 'user_trial_other', '99101', 'plan_full_trial'),
        ('mem_trial_other_discord', 'user_trial_first', '99102', 'plan_full_trial'),
        ('mem_trial_stale', 'user_trial_stale', '99104', 'plan_full_trial'),
        ('mem_trial_expired', 'user_trial_expired', '99105', 'plan_full_trial'),
        ('mem_trial_unverified', 'user_trial_unverified', '99106', 'plan_full_trial'),
        ('mem_trial_completed', 'user_trial_completed', '99107', 'plan_full_trial'),
        ('mem_trial_former_paid', 'user_old_paid', '99110', 'plan_full_trial'),
        ('mem_trial_service_role', 'user_trial_service_role', '99111', 'plan_full_trial'),
        ('mem_trial_to_paid', 'user_trial_first', '99101', 'plan_highroller_paid'),
        ('mem_old_paid', 'user_old_paid', '99110', null)
      ) as target(membership_id, whop_user_id, discord_user_id, plan_id)
      where target.membership_id = fixture ->> 'membership_id'
        and fixture ->> 'account_id' = 'biz_trial_test'
        and fixture ->> 'whop_user_id' = target.whop_user_id
        and (
          fixture ->> 'discord_user_id' = target.discord_user_id
          or (target.membership_id = 'mem_trial_first'
              and fixture ->> 'discord_user_id' = '99103')
        )
        and (target.plan_id is null or fixture ->> 'plan_id' = target.plan_id)
    )
  $filter$;
begin
  perform pg_catalog.set_config('lock_timeout', '5s', true);
  for item in
    select * from (values
      ('whop_trial_membership_audit', 'snapshot || pg_catalog.jsonb_build_object(''membership_id'', membership_id)'),
      ('whop_membership_audit', 'snapshot || pg_catalog.jsonb_build_object(''membership_id'', membership_id)'),
      ('whop_trial_memberships', 'pg_catalog.to_jsonb(record)'),
      ('whop_memberships', 'pg_catalog.to_jsonb(record)'),
      ('membership_trial_claims', 'pg_catalog.to_jsonb(record) || pg_catalog.jsonb_build_object(''plan_id'', ''plan_full_trial'')')
    ) as objects(table_name, fixture_expression)
  loop
    if pg_catalog.to_regclass('public.' || item.table_name) is null then
      raise notice '%: table not installed; skipped explicitly', item.table_name;
      continue;
    end if;
    if apply_cleanup then
      execute pg_catalog.format(
        'with removed as (
           delete from public.%I as record
           where record.ctid in (
             select record.ctid from public.%I as record
             cross join lateral (select %s as fixture) as source
             where %s
           )
           returning 1
         ) select count(*) from removed',
        item.table_name, item.table_name, item.fixture_expression, fixture_filter
      ) into matched_count;
      raise notice '%: deleted % exact synthetic fixture rows', item.table_name, matched_count;
    else
      execute pg_catalog.format(
        'select count(*) from public.%I as record
         cross join lateral (select %s as fixture) as source
         where %s',
        item.table_name, item.fixture_expression, fixture_filter
      ) into matched_count;
      raise notice '%: preview matched % rows; nothing deleted', item.table_name, matched_count;
    end if;
  end loop;
  if not apply_cleanup then
    raise notice 'READ-ONLY PREVIEW complete. Set apply_cleanup to true only after reviewing counts and confirming a backup.';
  end if;
end;
$cleanup$;
