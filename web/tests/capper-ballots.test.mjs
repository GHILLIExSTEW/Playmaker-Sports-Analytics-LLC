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
    create function public.website_capper_ids() returns text[] language sql as $$ select array['111']::text[] $$;
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
})
