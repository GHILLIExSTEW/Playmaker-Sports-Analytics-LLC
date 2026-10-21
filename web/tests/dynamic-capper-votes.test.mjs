import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

async function setup(t) {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.plays(id bigint primary key, tracking_scope text, units numeric, stars integer);
    create table public.capper_ballots(
      message_id text primary key, state text not null default 'pending', error text, votes jsonb
    );
    create table public.capper_ballot_scan(singleton boolean primary key);
    insert into public.capper_ballot_scan values(true);
    insert into public.capper_ballots(message_id) values('100');
    insert into public.plays values(1, 'official', 1, 3);
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261021000000_dynamic_capper_votes.sql', import.meta.url), 'utf8'))
  await db.exec(`
    insert into public.capper_ballots(message_id,eligible_voter_ids,policy_version)
      values('500',array['42','43','44'],'roster-v1');
    insert into public.capper_chat_votes(
      source_message_id,ballot_message_id,source_channel_id,voter_id,choice,source_text,prompt_message_id,prompted_at
    ) values('501','500','100','42','yes','Agreed','900',now() - interval '10 minutes');
  `)
  return db
}

async function verify(db, actor, reject = false) {
  return (await db.query('select public.verify_capper_chat_vote($1,$2,$3) as state', ['501', actor, reject])).rows[0].state
}

test('only author or two distinct peers after escalation can verify a statement', async (t) => {
  const db = await setup(t)
  await assert.rejects(verify(db, '999'), /Only eligible/)
  await assert.rejects(verify(db, '43'), /10-minute escalation/)
  await db.exec("update public.capper_chat_votes set escalated_at=now(), escalation_message_id='901'")
  assert.equal(await verify(db, '43'), 'awaiting-second-capper')
  assert.equal(await verify(db, '43'), 'awaiting-second-capper')
  assert.deepEqual((await db.query('select approver_ids from public.capper_chat_votes')).rows[0].approver_ids, ['43'])
  await assert.rejects(verify(db, '43', true), /Only the statement author/)
  assert.equal(await verify(db, '44'), 'confirmed')
  assert.deepEqual((await db.query('select state,confirmed_by from public.capper_chat_votes')).rows[0], {
    state: 'confirmed', confirmed_by: 'two-peers',
  })
  assert.equal(await verify(db, '42', true), 'rejected')
  await assert.rejects(verify(db, '44'), /no longer available/)
})

test('author can confirm before escalation, while invalid sources and closed ballots cannot be verified', async (t) => {
  const db = await setup(t)
  assert.equal(await verify(db, '42'), 'confirmed')
  await db.exec("update public.capper_chat_votes set state='invalid'")
  await assert.rejects(verify(db, '42'), /no longer available/)
  await db.exec("update public.capper_ballots set state='published' where message_id='500'")
  await assert.rejects(verify(db, '42'), /no longer accepting/)
})

test('migration quarantines legacy pending ballots and preserves historical plays', async (t) => {
  const db = await setup(t)
  assert.equal((await db.query("select state from public.capper_ballots where message_id='100'")).rows[0].state, 'failed')
  assert.deepEqual((await db.query('select id, units::float, stars, team_approval from public.plays')).rows, [
    { id: 1, units: 1, stars: 3, team_approval: null },
  ])
  assert.equal((await db.query(`
    select has_table_privilege('authenticated','public.capper_chat_votes','select') as allowed
  `)).rows[0].allowed, false)
  assert.equal((await db.query(`
    select has_function_privilege('authenticated','public.verify_capper_chat_vote(text,text,boolean)','execute') as allowed
  `)).rows[0].allowed, false)
})

test('publication claims enforce every approved sizing entry and are idempotent', async (t) => {
  const db = await setup(t)
  for (const [size, yes, units] of [[3,2,2],[3,3,3],[4,3,2],[4,4,3],[5,3,1],[5,4,2],[5,5,3]]) {
    const ids = Array.from({ length: size }, (_, i) => String(42 + i))
    await db.query("update public.capper_ballots set state='pending', eligible_voter_ids=$1, decision=null where message_id='500'", [ids])
    const decision = { policy_version: 'roster-v1', roster_size: size, yes, units, stars: units + 2, counts: { yes, no: 0, pass: 0 } }
    await assert.rejects(db.query('select public.claim_rated_capper_ballot($1,$2)', ['500', { ...decision, units: 9 }]), /stake/)
    await assert.rejects(db.query('select public.claim_rated_capper_ballot($1,$2)', ['500', { ...decision, yes: 1 }]), /Invalid dynamic/)
    assert.equal((await db.query('select public.claim_rated_capper_ballot($1,$2) as claimed', ['500', decision])).rows[0].claimed, true)
    assert.equal((await db.query('select public.claim_rated_capper_ballot($1,$2) as claimed', ['500', decision])).rows[0].claimed, false)
    await assert.rejects(verify(db, '42'), /no longer accepting/)
    await db.exec("update public.capper_ballots set state='pending' where message_id='500'")
    await assert.rejects(db.query('select public.claim_rated_capper_ballot($1,$2)', ['500', { ...decision, note: 'changed' }]), /Saved ballot decision differs/)
    await db.query('update public.plays set units=$1,stars=$2,team_approval=$3 where id=1', [units, units + 2, decision])
    await assert.rejects(db.exec('update public.plays set units=9 where id=1'), /saved_team_approval_stake/)
  }
})

test('rejected or changed chat verifications cannot be claimed for publication', async (t) => {
  const db = await setup(t)
  const decision = {
    policy_version: 'roster-v1', roster_size: 3, yes: 2, units: 2, stars: 4,
    counts: { yes: 2, no: 0, pass: 0 }, voter_choices: { '42': 'yes', '43': 'yes' },
    chat_voter_ids: ['42'],
  }
  await assert.rejects(db.query('select public.claim_rated_capper_ballot($1,$2)', ['500', decision]), /chat vote changed/)
  await verify(db, '42')
  await verify(db, '42', true)
  await assert.rejects(db.query('select public.claim_rated_capper_ballot($1,$2)', ['500', decision]), /chat vote changed/)
})

test('website feed exposes only approval totals and preserves the existing access gate', async (t) => {
  const db = await setup(t)
  await db.exec(`
    create table public.tracking_display_settings(singleton boolean, starts_at timestamptz);
    insert into public.tracking_display_settings values(true, '2026-10-14T00:00:00Z');
    create function public.archived_current_picks(text, text)
    returns table(id bigint,created_at timestamptz,sport text,capper text,avatar_url text,
      selection text,analysis text,odds integer,units numeric)
    language plpgsql stable security definer set search_path='' as $$
    begin
      if current_setting('test.member_access',true) is distinct from 'active' then
        raise exception 'Verified active membership required.';
      end if;
      return query select 1::bigint,'2026-10-21T00:00:00Z'::timestamptz,'NBA'::text,
        'Official Playmaker Picks'::text,null::text,'BOS/DET O219.5'::text,''::text,-110,2::numeric;
    end;
    $$;
    revoke all on function public.archived_current_picks(text,text) from public;
    create function public.member_current_picks(text,text) returns void language sql as $$ select $$;
    update public.plays set units=2,stars=4,team_approval=
      '{"yes":2,"roster_size":3,"units":2,"stars":4,"policy_version":"roster-v1","voter_choices":{"42":"yes","43":"yes"}}';
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261021020000_current_pick_team_approval.sql', import.meta.url), 'utf8'))
  await db.exec('set role authenticated')
  await assert.rejects(db.query('select * from public.member_current_picks()'), /Verified active membership/)
  await db.query("select set_config('test.member_access','active',false)")
  const row = (await db.query('select * from public.member_current_picks()')).rows[0]
  assert.equal(row.approval_yes, 2)
  assert.equal(row.eligible_cappers, 3)
  assert.equal(row.units, '2')
  assert.equal('voter_choices' in row, false)
  await db.exec('reset role; set role anon')
  await assert.rejects(db.query('select * from public.member_current_picks()'), /permission denied/)
})

test('generated website guide matches the maintained HTML and documents both verification paths', async (t) => {
  const db = await setup(t)
  await db.exec('create function public.require_operator_research_access() returns void language sql as $$ select $$')
  await db.exec(await readFile(new URL('../../supabase/migrations/20261021010000_dynamic_capper_voting_guide.sql', import.meta.url), 'utf8'))
  await db.exec(await readFile(new URL('../../supabase/migrations/20261021030000_live_membership_guide.sql', import.meta.url), 'utf8'))
  await db.exec(await readFile(new URL('../../supabase/migrations/20261021050000_subscription_options_guide.sql', import.meta.url), 'utf8'))
  const html = await readFile(new URL('../../Playmaker_Picks_User_Guide.html', import.meta.url), 'utf8')
  const saved = (await db.query('select public.operator_user_guide() as html')).rows[0].html
  assert.equal(saved, html.split('<main>')[1].split('</main>')[0])
  assert.match(saved, /Two other distinct eligible cappers MUST each approve/)
  assert.match(saved, /10 minutes without confirmation/)
  assert.match(saved, /3 cappers: 2 Yes = 2u; 3 Yes = 3u/)
})
