import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('team and capper ledgers remain separate and ballot claims are durable', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  const load = async (path) => db.exec(await readFile(new URL(path, import.meta.url), 'utf8'))
  await db.exec('create role anon; create role authenticated; create role service_role bypassrls;')
  await load('../../src/database/schema.sql')
  await load('../../supabase/migrations/20260930240000_public_capper_avatars.sql')
  await db.exec(`
    create table public.play_insights (play_id bigint primary key, justification text);
    create function public.website_member_access() returns jsonb language sql as
      $$ select jsonb_build_object('state',current_setting('test.access',true)) $$;
    create function public.website_capper_ids() returns text[] language sql as $$ select array['111','222']::text[] $$;
    create function public.play_insight_author(p_play_id bigint) returns text language sql as $$ select null::text $$;
    create schema auth;
    create table auth.identities(user_id uuid, provider text, provider_id text);
    create function auth.uid() returns uuid language sql as $$ select null::uuid $$;
    create function public.website_current_picks_source(p_sport text, p_capper text)
    returns table (id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
      selection text, analysis text, odds integer, units numeric)
    language sql as $$
      select p.id,p.created_at,'NFL',u.display_name,null::text,p.team_name,p.play_text,p.odds,p.units
      from public.plays p join public.users u on u.id=p.user_id
      where p.message_id is not null and p.status='open' and p.settled_at is null
    $$;
    create function public.member_current_picks(p_sport text default null, p_capper text default null)
    returns table (id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
      selection text, analysis text, odds integer, units numeric)
    language sql as $$ select * from public.website_current_picks_source(p_sport,p_capper) $$;
    revoke all on function public.member_current_picks(text,text) from public;
    grant execute on function public.member_current_picks(text,text) to authenticated;
    insert into public.sports(id,api_slug,name) values(1,'nfl','NFL');
    insert into public.users(id,discord_user_id,display_name) values(1,'111','Ace'),(2,'900','Playmaker Picks Team');
    insert into public.plays(id,user_id,sport_id,units,legs,odds,status,message_id,settled_at)
    values(1,1,1,1,1,100,'win','101',now());
  `)
  await load('../../supabase/migrations/20261014233000_separate_capper_ballots.sql')
  await load('../../supabase/migrations/20261014235000_ballot_source_images.sql')
  assert.equal((await db.query('select tracking_scope from public.plays where id=1')).rows[0].tracking_scope, 'official')
  await db.exec(`
    insert into public.plays(id,user_id,sport_id,units,legs,odds,status,message_id,settled_at,tracking_scope,stars)
    values (2,1,1,2,1,100,'win','102',now(),'capper',null),
      (3,2,1,2,1,100,'win','103',now(),'official',4),
      (4,1,1,2,1,100,'win','104',now(),'test',null),
      (5,1,1,2,1,100,'open','105',null,'capper',null),
      (6,2,1,2,1,100,'open','106',null,'official',4);
    insert into public.play_insights values(5,'Capper reasoning');
    select set_config('test.access','active',false);
  `)
  const results = (await db.query('select * from public.public_settled_results()')).rows
  assert.equal(results.length, 3)
  assert.deepEqual(results.filter((r) => r.tracking_scope === 'official').map((r) => r.capper), [
    'Official Playmaker Picks', 'Official Playmaker Picks',
  ])
  assert.equal(results.filter((r) => r.tracking_scope === 'capper')[0].capper, 'Ace')
  const current = (await db.query('select * from public.member_current_picks()')).rows
  assert.equal(current.length, 2)
  assert.equal(current.find((r) => r.id === 6).capper, 'Official Playmaker Picks')
  assert.equal(current.find((r) => r.id === 5).analysis, 'Capper reasoning')
  assert.equal(current.find((r) => r.id === 6).analysis, '')
  assert.deepEqual((await db.query("select id from public.member_current_picks(null,'Ace')")).rows, [{ id: 5 }])
  await db.exec("select set_config('test.access','membership-required',false)")
  await assert.rejects(db.query('select * from public.member_current_picks()'), /Verified active membership required/)
  await assert.rejects(db.exec('update public.plays set units=5 where id=3'), /team_ballot_rating/)
  await assert.rejects(db.exec("update public.plays set stars=3 where id=2"), /team_ballot_rating/)
  await db.exec("insert into public.capper_ballots(message_id) values('500')")
  assert.equal((await db.query("select public.claim_capper_ballot('500') as claimed")).rows[0].claimed, true)
  assert.equal((await db.query("select public.claim_capper_ballot('500') as claimed")).rows[0].claimed, false)
  assert.equal((await db.query("select state from public.capper_ballots where message_id='500'")).rows[0].state, 'processing')
  await db.exec('set role anon')
  await assert.rejects(db.query('select * from public.capper_ballots'), /permission denied/)
  await assert.rejects(db.query("select public.claim_capper_ballot('500')"), /permission denied/)
  await assert.rejects(db.query('select * from public.member_current_picks()'), /permission denied/)
  assert.equal((await db.query('select * from public.public_settled_results()')).rows.length, 3)
  await db.exec('reset role')
  await load('../../supabase/migrations/20261015000000_tracking_display_reset.sql')
  await db.exec(`
    update public.plays set created_at='2026-10-14T03:59:59Z' where id in (1,5);
    update public.plays set created_at='2026-10-14T04:00:00Z' where id in (2,3,4,6);
    insert into public.plays(id,user_id,sport_id,units,legs,odds,status,message_id,settled_at,created_at,tracking_scope)
    values (7,1,1,10,2,235125,'win','107',now(),'2026-10-14T04:00:00Z','capper');
    select set_config('test.access','active',false);
  `)
  assert.equal((await db.query('select count(*)::int as count from public.plays')).rows[0].count, 7)
  assert.deepEqual((await db.query('select id from public.display_plays order by id')).rows.map((r) => r.id), [2,3,4,6,7])
  assert.equal((await db.query('select * from public.public_settled_results()')).rows.length, 3)
  assert.deepEqual((await db.query('select id from public.member_current_picks()')).rows, [{ id: 6 }])
  await load('../../supabase/migrations/20261025000000_capper_units_limit.sql')
  assert.equal((await db.query('select * from public.public_settled_results()')).rows.length, 2)
  await assert.rejects(db.exec(`
    insert into public.plays(id,user_id,sport_id,units,legs,odds,status,message_id,tracking_scope)
    values (8,1,1,10,2,100,'open','108','capper')
  `), /plays_capper_units_limit/)
  await db.exec('set role anon')
  await assert.rejects(db.query('select * from public.archived_settled_results()'), /permission denied/)
  await assert.rejects(db.query('select * from public.display_plays'), /permission denied/)
  assert.equal((await db.query('select * from public.public_settled_results()')).rows.length, 2)
  await db.exec('reset role')
  await db.exec(`
    create function public.public_capper_directory()
    returns table(name text, avatar_url text) language sql as $$ select null::text,null::text where false $$;
    insert into public.users(id,discord_user_id,display_name) values(3,'222','Bee');
    alter table public.plays add column team_approval jsonb;
    alter table public.capper_ballots add column author_id text;
    insert into public.plays(id,user_id,sport_id,units,legs,odds,status,message_id,created_at,settled_at,tracking_scope,stars,ballot_message_id,team_approval)
    values
      (8,2,1,2,1,100,'win','108','2026-10-15T04:00:00Z','2026-10-16T04:00:00Z','official',4,'508',
        '{"voter_choices":{"111":"yes","222":"yes","900":"no"}}'),
      (9,2,1,2,1,100,'open','109','2026-10-15T04:00:00Z',null,'official',4,'509',
        '{"voter_choices":{"111":"no","222":"yes"}}'),
      (10,2,1,2,1,100,'loss','110','2026-10-15T04:00:00Z','2026-10-17T04:00:00Z','official',4,'510',
        '{"voter_choices":{"111":"yes","222":"yes","900":"no"}}');
    insert into public.capper_ballots(message_id,author_id) values('508','111'),('509','111'),('510','111');
  `)
  await load('../../supabase/migrations/20261109010000_team_play_author_records.sql')
  await assert.rejects(db.query('select team_approval from public.display_plays'), /team_approval/)
  await load('../../supabase/migrations/20261109020000_refresh_display_plays_team_approval.sql')
  assert.equal((await db.query('select team_approval from public.display_plays where id=8')).rows.length, 1)
  const personalTeamRecords = (await db.query('select * from public.public_capper_settled_results()')).rows
  assert.equal(personalTeamRecords.length, 4)
  assert.deepEqual(personalTeamRecords.map((row) => row.capper).sort(), ['Ace', 'Ace', 'Bee', 'Bee'])
  assert.equal(personalTeamRecords.filter((row) => row.status === 'win').length, 2)
  assert.equal(personalTeamRecords.filter((row) => row.status === 'loss').length, 2)
  assert.ok(personalTeamRecords.every((row) => Number(row.units) === 2 && row.capper_record))
  await load('../../supabase/migrations/20261112190000_capper_tail_record_unit_cap.sql')
  const cappedTeamRecords = (await db.query('select * from public.public_capper_settled_results()')).rows
  assert.equal(cappedTeamRecords.length, 4)
  assert.ok(cappedTeamRecords.every((row) => row.capper_record))
  // Ace authored ballots 508/510 and keeps the 2u stake; Bee tailed and is capped at 1u.
  assert.deepEqual(
    cappedTeamRecords.map((row) => [row.capper, row.status, Number(row.units), Number(row.net_units)]).sort(),
    [['Ace', 'loss', 2, -2], ['Ace', 'win', 2, 2], ['Bee', 'loss', 1, -1], ['Bee', 'win', 1, 1]],
  )
  assert.deepEqual((await db.query("select id,capper from public.member_current_picks(null,'Bee')")).rows, [
    { id: 9, capper: 'Bee' },
  ])
  assert.deepEqual((await db.query('select name from public.public_capper_directory() order by name')).rows, [
    { name: 'Ace' }, { name: 'Bee' },
  ])
  assert.deepEqual((await db.query('select capper_name from public.public_favorite_cappers() order by capper_name')).rows, [
    { capper_name: 'Ace' }, { capper_name: 'Bee' },
  ])
  assert.equal((await db.query('select * from public.public_settled_results()')).rows.length, 4)
  await load('../../supabase/migrations/20261113190000_public_play_writeup_history.sql')
  await db.exec("insert into public.play_insights values(8,'Team writeup'),(2,'Ace personal writeup')")
  const writeupLedger = (await db.query('select * from public.public_settled_results()')).rows
  assert.equal(writeupLedger.length, 4)
  assert.ok(writeupLedger.every((row) => typeof row.analysis === 'string'))
  assert.deepEqual(
    writeupLedger.filter((row) => row.analysis !== '').map((row) => [row.capper, row.analysis]).sort(),
    [['Ace', 'Ace personal writeup'], ['Official Playmaker Picks', 'Team writeup']],
  )
  // Every tailing capper's personal record row carries the same play writeup.
  const writeupCapperLedger = (await db.query('select * from public.public_capper_settled_results()')).rows
  assert.deepEqual(
    writeupCapperLedger.filter((row) => row.analysis !== '').map((row) => [row.capper, row.analysis]).sort(),
    [['Ace', 'Team writeup'], ['Bee', 'Team writeup']],
  )
  await db.exec('set role anon')
  assert.equal((await db.query('select * from public.public_settled_results()')).rows
    .filter((row) => row.analysis !== '').length, 2)
  await db.exec('reset role')
})
