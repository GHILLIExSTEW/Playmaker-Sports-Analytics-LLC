import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

for (const hasAvatarMigration of [false, true]) {
test(`website member access and current picks enforce server authorization (${hasAvatarMigration ? 'existing' : 'missing'} avatar column)`, async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  const load = async (path) => db.exec(await readFile(new URL(path, import.meta.url), 'utf8'))
  await db.exec(`
    create role anon;
    create role authenticated;
    create role service_role;
    create schema auth;
    create table auth.users (id uuid primary key, raw_user_meta_data jsonb default '{}');
    create table auth.identities (user_id uuid references auth.users(id), provider text, provider_id text);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
    $$;
    grant usage on schema auth to anon, authenticated;
    grant execute on function auth.uid() to anon, authenticated;
  `)
  await load('../../src/database/schema.sql')
  await load(hasAvatarMigration
    ? '../../supabase/migrations/20260930240000_public_capper_avatars.sql'
    : '../../supabase/migrations/20260930230000_public_settled_results.sql')
  await load('../../supabase/migrations/20260930270000_member_accounts_favorites.sql')
  await load('../../supabase/migrations/20260930280000_member_profile_controls.sql')
  await load('../../supabase/migrations/20261003010000_whop_membership_access.sql')
  await load('../../supabase/migrations/20261003040000_owner_highroller_access.sql')
  await load('../../supabase/migrations/20261005000000_full_membership_trials.sql')
  // Model Supabase's broad defaults so the migration must actually restrict them.
  await db.exec('grant all on public.plays, public.play_legs, public.play_versions, public.play_draft_legs, public.settlements, public.users to anon, authenticated')
  await db.exec('grant all on public.plays, public.play_legs, public.users to service_role')
  assert.equal((await db.query("select count(*)::integer as count from information_schema.columns where table_schema='public' and table_name='users' and column_name='public_avatar_url'")).rows[0].count, hasAvatarMigration ? 1 : 0)
  await load('../../supabase/migrations/20261008000000_website_member_picks.sql')
  assert.equal((await db.query("select data_type from information_schema.columns where table_schema='public' and table_name='users' and column_name='public_avatar_url'")).rows[0].data_type, 'text')

  const memberId = '11111111-1111-1111-1111-111111111111'
  await db.exec(`
    insert into auth.users values ('${memberId}', '{"provider_id":"761388542965448767","sub":"761388542965448767"}');
    insert into public.member_profiles (user_id,age_verified_at,display_name,public_handle)
      values ('${memberId}', now(), 'Saved name', 'saved-name');
    insert into auth.identities values ('${memberId}', 'discord', '123456789');
    insert into public.sports (id,api_slug,name) values (1,'nfl','NFL'),(2,'nhl','NHL');
    insert into public.users (id,discord_user_id,display_name) values
      (1,'111','First Capper'),(2,'222','New Capper');
    insert into public.plays (id,user_id,sport_id,units,legs,odds,status,message_id,team_name,play_text,created_at,settled_at) values
      (1,1,1,1,2,110,'open','published-1','Fallback','First analysis',now()-interval '1 hour',null),
      (2,2,2,2,1,-120,'open','published-2','New pick','Second analysis',now(),null),
      (3,1,1,1,1,100,'open',null,'UNPUBLISHED SECRET','Secret draft',now(),null),
      (4,1,1,1,1,100,'win','settled','Settled pick','Settled analysis',now(),now()),
      (5,1,1,1,1,100,'open','published-1','DUPLICATE SECRET','Duplicate',now(),null),
      (6,1,1,1,1,100,'open','inconsistent','INCONSISTENT SECRET','Inconsistent',now(),now());
    insert into public.play_legs (play_id,leg_number,selection,odds) values
      (1,1,'Leg A',110),(1,2,'Leg B',-120);
    insert into public.whop_memberships values (
      'paid-test','buyer','123456789','biz_rCNwfXRlnl0bFU','plan_cdPyKCHjSQeG2',
      'completed',now()-interval '1 day',now()+interval '29 days','payment-test',
      true,false,now(),now(),'Verified test'
    );
  `)
  let inTransaction = false
  async function call(sql, role = 'authenticated', id = memberId) {
    await db.query("select set_config('request.jwt.claim.sub',$1,false)", [id])
    if (inTransaction) await db.exec('savepoint rpc_call')
    await db.exec(`set role ${role}`)
    try { return (await db.query(sql)).rows }
    catch (error) {
      if (inTransaction) await db.exec('rollback to savepoint rpc_call')
      throw error
    }
    finally { await db.exec('reset role') }
  }
  const access = async () => (await call('select public.website_member_access() as access'))[0].access
  const denied = async () => assert.rejects(call('select * from public.member_current_picks()'), /Verified active membership required/)

  await t.test('closed launch gate denies even paid members; anonymous cannot execute feed', async () => {
    assert.equal((await access()).state, 'launch-pending')
    await denied()
    assert.equal((await call('select public.website_member_access() as access', 'anon', ''))[0].access.state, 'signed-out')
    await assert.rejects(call('select * from public.member_current_picks()', 'anon', ''), /permission denied/)
    await db.exec('update public.website_membership_config set enabled = true')
  })
  await t.test('paid access, filtering, leg order and publication exclusions', async () => {
    assert.equal((await access()).kind, 'paid')
    const rows = await call('select * from public.member_current_picks()')
    assert.deepEqual(rows.map((row) => row.id), [2, 1])
    assert.equal(rows[1].selection, 'Leg A / Leg B')
    assert.equal(rows[1].analysis, 'First analysis')
    assert.deepEqual(Object.keys(rows[0]).sort(), ['id','created_at','sport','capper','avatar_url','selection','analysis','odds','units'].sort())
    assert.equal((await call("select * from public.member_current_picks('NFL','First Capper')")).length, 1)
    assert.equal((await call("select * from public.member_current_picks('NFL','New Capper')")).length, 0)
  })
  await t.test('public directory includes new capper without leaking selections; public ledger stays settled', async () => {
    const directory = await call('select * from public.public_capper_directory()', 'anon', '')
    assert.deepEqual(directory.map((row) => row.name), ['First Capper', 'New Capper'])
    assert.deepEqual(Object.keys(directory[0]).sort(), ['avatar_url', 'name'])
    const rows = await call('select * from public.public_settled_results()', 'anon', '')
    assert.equal(rows.length, 1)
    assert.equal(rows[0].selection, 'Settled pick')
    for (const table of ['plays','play_legs','play_versions','play_draft_legs','settlements','users','website_membership_config','whop_memberships','whop_trial_memberships','membership_trial_claims']) {
      await assert.rejects(call(`select * from public.${table}`, 'anon', ''), /permission denied/)
      await assert.rejects(call(`select * from public.${table}`), /permission denied/)
    }
  })
  await t.test('age and immutable Discord identity are mandatory, despite forged user metadata', async () => {
    await db.exec(`update public.member_profiles set age_verified_at=null where user_id='${memberId}'`)
    assert.equal((await access()).state, 'age-required')
    await denied()
    await db.exec(`update public.member_profiles set age_verified_at=now(); delete from auth.identities`)
    assert.equal((await access()).state, 'discord-required')
    await denied()
    await db.exec(`insert into auth.identities values ('${memberId}', 'discord', '999')`)
    assert.equal((await access()).state, 'membership-required')
    await denied()
    await db.exec("update auth.identities set provider_id='123456789'")
  })
  await t.test('stale/future snapshots, wrong seller/plan, nonpayment, expiry and revocation deny', async () => {
    for (const mutation of [
      "verified_at=now()-interval '16 minutes'",
      "verified_at=now()+interval '6 minutes'",
      "account_id='wrong-seller'",
      "plan_id='wrong-plan'",
      "paid=false",
      "paid_through=now()-interval '1 minute'",
      "paid_from=now()+interval '1 minute'",
      "status='revoked'",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(`update public.whop_memberships set ${mutation}`)
        assert.equal((await access()).state, 'membership-required', mutation)
        await denied()
      } finally { await db.exec('rollback'); inTransaction = false }
    }
    assert.equal((await access()).state, 'active')
  })
  await t.test('eligible trial claims authorize; invalid/expired/revoked trial does not', async () => {
    await db.exec(`
      update public.whop_memberships set paid=false;
      insert into public.whop_trial_memberships values (
        'trial-test','trial-buyer','123456789','biz_rCNwfXRlnl0bFU','plan_R7H8Sx7MEKzh0',
        'active',now()-interval '1 day',now()+interval '6 days',true,true,now(),now(),'Trial test'
      );
      insert into public.membership_trial_claims
        (account_id,whop_user_id,discord_user_id,membership_id,starts_at,expires_at,eligible)
      select account_id,whop_user_id,discord_user_id,membership_id,paid_from,paid_through,true
      from public.whop_trial_memberships;
    `)
    assert.equal((await access()).kind, 'trial')
    assert.equal((await call('select * from public.member_current_picks()')).length, 2)
    for (const mutation of [
      'update public.membership_trial_claims set eligible=false',
      'update public.whop_trial_memberships set trial_eligible=false',
      'update public.whop_trial_memberships set trial_verified=false',
      "update public.whop_trial_memberships set status='revoked'",
      "update public.whop_trial_memberships set verified_at=now()-interval '16 minutes'",
      "update public.whop_trial_memberships set plan_id='wrong-trial'",
      "update public.membership_trial_claims set whop_user_id='wrong-buyer'",
      "update public.whop_trial_memberships set paid_from=now()-interval '8 days',paid_through=now()-interval '1 day'",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(mutation)
        assert.equal((await access()).state, 'membership-required', mutation)
        await denied()
      } finally { await db.exec('rollback'); inTransaction = false }
    }
  })
  await t.test('paid/trial overlap prefers paid; historical allowlisted passes remain eligible', async () => {
    await db.exec("update public.whop_memberships set paid=true,plan_id='historical-paid'")
    assert.equal((await access()).kind, 'trial')
    await db.exec("update public.website_membership_config set paid_plan_ids=array_append(paid_plan_ids,'historical-paid')")
    assert.equal((await access()).kind, 'paid')
  })
  await t.test('owner grant is seller-scoped and respects revocation/expiry', async () => {
    await db.exec("update auth.identities set provider_id='761388542965448767'")
    assert.equal((await access()).kind, 'owner')
    assert.equal((await access()).expires_at, null)
    for (const mutation of [
      "account_id='wrong-seller'", 'revoked_at=now()', "starts_at=now()-interval '2 days',expires_at=now()-interval '1 minute'",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(`update public.owner_membership_grants set ${mutation}`)
        assert.equal((await access()).state, 'membership-required')
        await denied()
      } finally { await db.exec('rollback'); inTransaction = false }
    }
  })
  await t.test('service-role publishing/settlement remains available and moves picks to the public ledger', async () => {
    assert.equal((await call('select * from public.plays', 'service_role')).length, 6)
    await call("update public.plays set status='win',settled_at=now() where id=2 returning id", 'service_role')
    assert.deepEqual((await call('select * from public.member_current_picks()')).map((row) => row.id), [1])
    assert.equal((await call('select * from public.public_settled_results()', 'anon', '')).length, 2)
  })
  await t.test('only synchronized OPERATOR identities are website cappers, not stored roles or historical authors', async () => {
    await load('../../supabase/migrations/20261008010000_operator_capper_roster.sql')
    await assert.rejects(call('select * from public.public_capper_directory()', 'anon', ''), /unavailable or stale/)
    await assert.rejects(call("select public.sync_website_capper_roster('123','1328120848992960543',array['111'])"), /permission denied/)
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111'])", 'service_role')
    await db.exec("update public.users set role='official' where id=2")
    assert.deepEqual((await call('select * from public.public_capper_directory()', 'anon', '')).map((row) => row.name), ['First Capper'])
    assert.deepEqual((await call('select * from public.public_favorite_cappers()', 'anon', '')).map((row) => row.capper_name), ['First Capper'])
    assert.deepEqual((await call('select * from public.member_current_picks()')).map((row) => row.id), [1])
    assert.equal((await call('select * from public.public_settled_results()', 'anon', '')).length, 2)
    await assert.rejects(call('select * from public.website_current_picks_source()'), /permission denied/)
    await assert.rejects(call('select * from public.website_capper_roster'), /permission denied/)
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array[]::text[])", 'service_role')
    assert.equal((await call('select * from public.public_capper_directory()', 'anon', '')).length, 0)
    assert.equal((await call('select * from public.member_current_picks()')).length, 0)
    await db.exec("update public.website_capper_roster set verified_at=now()-interval '16 minutes'")
    await assert.rejects(call('select * from public.public_capper_directory()', 'anon', ''), /unavailable or stale/)
    await assert.rejects(call('select * from public.member_current_picks()'), /unavailable or stale/)
  })
})
}
