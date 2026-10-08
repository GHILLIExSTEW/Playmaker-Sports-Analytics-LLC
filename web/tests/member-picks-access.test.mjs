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
    create role service_role bypassrls;
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
  await t.test('closed-launch preview authorizes only the verified owner with a current grant', async () => {
    await load('../../supabase/migrations/20261008020000_owner_website_preview.sql')
    await db.exec('update public.website_membership_config set enabled=false')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111'])", 'service_role')
    assert.equal((await access()).kind, 'owner')
    assert.deepEqual((await call('select * from public.member_current_picks()')).map((row) => row.id), [1])
    assert.equal((await db.query('select enabled from public.website_membership_config')).rows[0].enabled, false)
    await assert.rejects(call('select public.website_member_access_standard()'), /permission denied/)
    for (const mutation of [
      "update auth.identities set provider_id='123456789'",
      "delete from auth.identities",
      "update public.member_profiles set age_verified_at=null",
      "update public.owner_membership_grants set revoked_at=now()",
      "update public.owner_membership_grants set account_id='wrong-seller'",
      "update public.owner_membership_grants set starts_at=now()+interval '1 hour'",
      "update public.owner_membership_grants set starts_at=now()-interval '2 days',expires_at=now()-interval '1 minute'",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(mutation)
        assert.notEqual((await access()).state, 'active', mutation)
        await denied()
      } finally { await db.exec('rollback'); inTransaction = false }
    }
    await db.exec("update auth.identities set provider_id='123456789'")
    assert.equal((await access()).state, 'launch-pending')
    await denied()
    await db.exec('update public.whop_memberships set paid=false')
    assert.equal((await access()).state, 'launch-pending')
    await denied()
    assert.equal((await call('select public.website_member_access() as access', 'anon', '')).at(0).access.state, 'signed-out')
  })
  await t.test('verified OPERATORs can view picks while launch is closed; removal and stale roster deny', async () => {
    await load('../../supabase/migrations/20261008030000_operator_website_access.sql')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','123456789'])", 'service_role')
    assert.equal((await access()).kind, 'operator')
    assert.deepEqual((await call('select * from public.member_current_picks()')).map((row) => row.id), [1])
    assert.equal((await db.query('select enabled from public.website_membership_config')).rows[0].enabled, false)
    await assert.rejects(call('select public.website_member_access_owner_preview()'), /permission denied/)
    for (const mutation of [
      "update public.member_profiles set age_verified_at=null",
      "delete from auth.identities",
      "update auth.identities set provider_id='999999'",
      "update public.website_capper_roster set discord_user_ids=array['111']",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(mutation)
        assert.notEqual((await access()).state, 'active')
        await denied()
      } finally { await db.exec('rollback'); inTransaction = false }
    }
    await db.exec("update public.whop_trial_memberships set status='revoked'")
    await db.exec('update public.website_membership_config set enabled=true')
    assert.equal((await access()).kind, 'operator')
    await db.exec("update public.website_capper_roster set verified_at=now()-interval '16 minutes'")
    await assert.rejects(access(), /unavailable or stale/)
    await assert.rejects(call('select * from public.member_current_picks()'), /unavailable or stale/)
  })
  await t.test('OPERATOR team HIGHROLLER includes stats/vault and member reconciliation without payment; removal preserves independent entitlements', async () => {
    await load('../../supabase/migrations/20261008040000_operator_highroller_benefits.sql')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['123456789'])", 'service_role')
    await db.exec("update public.whop_trial_memberships set status='revoked'; update public.website_membership_config set enabled=false")
    const vault = async () => (await call("select public.discord_has_paid_access('123456789') as allowed", 'service_role'))[0].allowed
    const stats = async (seller = 'biz_rCNwfXRlnl0bFU') => (await call(`select public.discord_has_paid_plan_access('123456789','${seller}',array['historical-paid']) as allowed`, 'service_role'))[0].allowed
    assert.equal(await vault(), true)
    assert.equal(await stats(), true)
    assert.equal(await stats('wrong-seller'), false)
    assert.ok((await call('select * from public.paid_discord_member_ids()', 'service_role')).some((row) => row.discord_user_id === '123456789'))
    assert.equal((await access()).kind, 'operator')
    assert.equal((await access()).expires_at, null)
    assert.equal((await db.query("select paid from public.whop_memberships where discord_user_id='123456789'")).rows[0].paid, false)
    await assert.rejects(call("select * from public.team_highroller_ids('biz_rCNwfXRlnl0bFU')"), /permission denied/)
    for (const mutation of [
      "update public.website_capper_roster set discord_user_ids=array[]::text[]",
      "update public.website_capper_roster set verified_at=now()-interval '16 minutes'",
      "update public.website_capper_roster set role_id='999'",
    ]) {
      await db.exec('begin')
      inTransaction = true
      try {
        await db.exec(mutation)
        assert.equal(await vault(), false)
        assert.equal(await stats(), false)
        assert.ok(!(await call('select * from public.paid_discord_member_ids()', 'service_role')).some((row) => row.discord_user_id === '123456789'))
      } finally { await db.exec('rollback'); inTransaction = false }
    }
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array[]::text[])", 'service_role')
    await db.exec('update public.whop_memberships set paid=true')
    assert.equal(await vault(), true)
    assert.equal(await stats(), true)
    await db.exec("update public.whop_memberships set paid=false; update public.whop_trial_memberships set status='active'")
    assert.equal((await call("select public.discord_has_trial_access('123456789','biz_rCNwfXRlnl0bFU',array['plan_R7H8Sx7MEKzh0']) as allowed", 'service_role'))[0].allowed, true)
  })
  await t.test('capper insight is author-only, private, durable, and never extracted slip text', async () => {
    await load('../../supabase/migrations/20261008050000_capper_insights.sql')
    await db.exec("update public.plays set status='open',settled_at=null where id=2")
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','222','123456789'])", 'service_role')
    const feed = async () => call('select * from public.member_current_picks()')
    assert.deepEqual((await feed()).map((row) => row.analysis), ['', ''])
    assert.deepEqual((await call('select * from public.capper_insight_context()', 'service_role')).map((row) => row.play_id), [1, 2])
    for (const role of ['anon', 'authenticated']) {
      await assert.rejects(call('select * from public.play_insights', role), /permission denied/)
      await assert.rejects(call('select * from public.capper_insight_context()', role), /permission denied/)
      await assert.rejects(call("select public.save_capper_insight(1,'111','Stolen reasoning')", role), /permission denied/)
      await assert.rejects(call("select public.mark_capper_insight_prompt(1,'999')", role), /permission denied/)
    }
    await assert.rejects(call("select public.save_capper_insight(1,'222','Wrong author')", 'service_role'), /original current OPERATOR/)
    for (const id of [3, 4, 5, 6]) {
      await assert.rejects(call(`select public.save_capper_insight(${id},'111','Hidden or closed pick')`, 'service_role'), /original current OPERATOR/)
    }
    await assert.rejects(call("select public.save_capper_insight(1,'111','   ')", 'service_role'), /1 to 2000/)
    await assert.rejects(call("select public.save_capper_insight(1,'111',repeat('x',2001))", 'service_role'), /1 to 2000/)
    await call("select public.mark_capper_insight_prompt(1,'987654321')", 'service_role')
    assert.deepEqual((await call('select * from public.capper_insight_context()', 'service_role')).map((row) => row.play_id), [2])
    await call("select public.save_capper_insight(1,'111','  Author matchup reasoning  ')", 'service_role')
    assert.equal((await feed()).find((row) => row.id === 1).analysis, 'Author matchup reasoning')
    assert.equal((await call('select * from public.capper_insight_context(1)', 'service_role'))[0].prompt_message_id, '987654321')
    await call("select public.save_capper_insight(1,'111','Updated reasoning')", 'service_role')
    assert.equal((await feed()).find((row) => row.id === 1).analysis, 'Updated reasoning')
    assert.equal((await db.query('select play_text from public.plays where id=1')).rows[0].play_text, 'First analysis')
    await db.exec("update public.plays set status='win',settled_at=now() where id=1")
    await assert.rejects(call("select public.save_capper_insight(1,'111','Late update')", 'service_role'), /original current OPERATOR/)
    const publicResults = await call('select * from public.public_settled_results()', 'anon', '')
    assert.ok(!JSON.stringify(publicResults).includes('Updated reasoning'))
    await db.exec("update public.plays set status='open',settled_at=null where id=1")
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['222','123456789'])", 'service_role')
    await assert.rejects(call("select public.save_capper_insight(1,'111','Removed role')", 'service_role'), /original current OPERATOR/)
    await db.exec("update public.website_capper_roster set verified_at=now()-interval '16 minutes'")
    await assert.rejects(call("select public.save_capper_insight(2,'222','Stale roster')", 'service_role'), /unavailable or stale/)
  })
  await t.test('BANG claims are private and atomic per winning play and destination', async () => {
    await load('../../supabase/migrations/20261008060000_bang_notifications.sql')
    await assert.rejects(call("select public.claim_play_bang(1,'101')", 'service_role'), /published winning/)
    await db.exec("update public.plays set status='win',settled_at=now() where id=1")
    for (const role of ['anon', 'authenticated']) {
      await assert.rejects(call("select public.claim_play_bang(1,'101')", role), /permission denied/)
      await assert.rejects(call('select * from public.play_bang_notifications', role), /permission denied/)
    }
    const claim = async (channel) => (await call(`select public.claim_play_bang(1,'${channel}') as claimed`, 'service_role'))[0].claimed
    assert.equal(await claim('101'), true)
    assert.equal(await claim('101'), false)
    assert.equal(await claim('202'), true)
    await call("select public.complete_play_bang(1,'101','999')", 'service_role')
    assert.equal(await claim('101'), false)
    assert.equal((await db.query("select message_id from public.play_bang_notifications where channel_id='101'")).rows[0].message_id, '999')
    await assert.rejects(call("select public.complete_play_bang(1,'303','999')", 'service_role'), /not claimed/)
  })
  await t.test('only immutable Discord OPERATOR page owners can customize their own public settings', async () => {
    await load('../../supabase/migrations/20261008070000_capper_page_settings.sql')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','222'])", 'service_role')
    const profile = async () => (await call("select public.capper_page_profile('First Capper') as profile", 'anon', ''))[0].profile
    const saveSql = `select public.save_capper_page('#123abc','My public bio','https://images.example/avatar.png','{"website":"https://example.com","x":"https://x.com/capper"}') as profile`
    assert.equal((await profile()).bio, '')
    await assert.rejects(call(saveSql), /original OPERATOR page owner/)
    await assert.rejects(call(saveSql, 'anon', ''), /permission denied/)
    await assert.rejects(call('select * from public.capper_page_settings'), /permission denied/)
    await db.exec("update auth.identities set provider_id='111'")
    assert.equal((await call('select public.owned_capper_page() as name'))[0].name, 'First Capper')
    const saved = (await call(saveSql))[0].profile
    assert.equal(saved.bio, 'My public bio')
    assert.equal(saved.accent_color, '#123abc')
    assert.equal((await profile()).avatar_url, 'https://images.example/avatar.png')
    assert.equal((await call('select * from public.public_capper_directory()', 'anon', '')).find((row) => row.name === 'First Capper').avatar_url, 'https://images.example/avatar.png')
    assert.equal((await db.query('select count(*)::integer as count from public.capper_page_settings')).rows[0].count, 1)
    assert.equal((await db.query('select user_id from public.capper_page_settings')).rows[0].user_id, 1)
    assert.equal((await call("select public.capper_page_profile('New Capper') as profile", 'anon', ''))[0].profile.bio, '')
    for (const args of [
      "'red','','','{}'", "'#123abc',repeat('x',2001),'','{}'",
      "'#123abc','','http://example.com/image.png','{}'",
      "'#123abc','','https://user:pass@example.com/image','{}'",
      "'#123abc','','','{\"website\":\"javascript:alert(1)\"}'",
      "'#123abc','','','{\"website\":123}'", "'#123abc','','','{\"unknown\":\"https://example.com\"}'",
      "'#123abc','','','[]'",
    ]) {
      await assert.rejects(call(`select public.save_capper_page(${args})`), /Invalid page settings|Social links/)
    }
    await db.exec(`update auth.users set raw_user_meta_data='{"provider_id":"111"}'; update auth.identities set provider_id='999'`)
    await assert.rejects(call(saveSql), /original OPERATOR page owner/)
    await db.exec("update auth.identities set provider_id='111'; update public.website_capper_roster set discord_user_ids=array['222']")
    await assert.rejects(call(saveSql), /original OPERATOR page owner/)
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','222'])", 'service_role')
    assert.equal((await profile()).bio, 'My public bio')
    await db.exec("update public.website_capper_roster set verified_at=now()-interval '16 minutes'")
    await assert.rejects(call(saveSql), /unavailable or stale/)
  })
  await t.test('storage policies restrict avatar uploads/deletes to the owner folder without touching brand assets', async () => {
    await db.exec(`
      create schema storage;
      create table storage.buckets (id text primary key, public boolean);
      create table storage.objects (id bigint generated always as identity, bucket_id text, name text);
      alter table storage.objects enable row level security;
      grant usage on schema storage to authenticated, anon;
      grant select, insert, delete, update on storage.objects to authenticated;
      grant select on storage.objects to anon;
      grant usage on sequence storage.objects_id_seq to authenticated;
      insert into storage.buckets values ('website-assets', true);
    `)
    await load('../../supabase/migrations/20261008080000_capper_avatar_uploads.sql')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','222'])", 'service_role')
    const ownPath = `capper-avatars/${memberId}/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.webp`
    const otherPath = 'capper-avatars/22222222-2222-2222-2222-222222222222/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.webp'
    await call(`insert into storage.objects (bucket_id,name) values ('website-assets','${ownPath}') returning name`)
    assert.equal((await call('select * from storage.objects')).length, 1)
    assert.equal((await call('select * from storage.objects', 'anon', '')).length, 0)
    for (const [bucket, path] of [['website-assets', otherPath], ['website-assets', 'brand/logo.webp'], ['Media', ownPath], ['website-assets', ownPath.replace('.webp', '.svg')]]) {
      await assert.rejects(call(`insert into storage.objects (bucket_id,name) values ('${bucket}','${path}')`), /row-level security/)
    }
    assert.equal((await call("update storage.objects set name='brand/logo.webp' returning name")).length, 0)
    await db.exec(`insert into storage.objects (bucket_id,name) values ('website-assets','${otherPath}'),('website-assets','brand/logo.webp')`)
    assert.equal((await call("delete from storage.objects where name='brand/logo.webp' returning name")).length, 0)
    assert.equal((await call('delete from storage.objects returning name')).length, 1)
    assert.equal((await db.query('select count(*)::integer as count from storage.objects')).rows[0].count, 2)
    await db.exec("create policy broad_legacy_insert on storage.objects for insert to authenticated with check (bucket_id='website-assets')")
    await assert.rejects(call(`insert into storage.objects (bucket_id,name) values ('website-assets','${otherPath}')`), /row-level security/)
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['222'])", 'service_role')
    await assert.rejects(call(`insert into storage.objects (bucket_id,name) values ('website-assets','${ownPath}')`), /row-level security/)
  })
  await t.test('website insight editing derives author identity and shares Discord insight storage', async () => {
    await load('../../supabase/migrations/20261008090000_website_capper_insight_editing.sql')
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['111','222'])", 'service_role')
    await db.exec("update public.plays set status='open',settled_at=null where id=1")
    await call("select public.save_my_pick_insight(1,'Website author reasoning')")
    assert.equal((await call('select * from public.member_current_picks()')).find((row) => row.id === 1).analysis, 'Website author reasoning')
    assert.equal((await call('select * from public.capper_insight_context(1)', 'service_role'))[0].justification, 'Website author reasoning')
    await assert.rejects(call("select public.save_my_pick_insight(2,'Other author')"), /original current OPERATOR/)
    await assert.rejects(call("select public.save_my_pick_insight(1,'Anonymous')", 'anon', ''), /permission denied/)
    await db.exec("update auth.identities set provider_id='222'")
    await assert.rejects(call("select public.save_my_pick_insight(1,'Wrong owner')"), /original current OPERATOR/)
    await db.exec("update auth.identities set provider_id='111'; update public.plays set status='win',settled_at=now() where id=1")
    await assert.rejects(call("select public.save_my_pick_insight(1,'Closed pick')"), /original current OPERATOR/)
    await call("select public.sync_website_capper_roster('123','1328120848992960543',array['222'])", 'service_role')
    await assert.rejects(call("select public.save_my_pick_insight(1,'Removed role')"), /active OPERATOR/)
  })
})
}
