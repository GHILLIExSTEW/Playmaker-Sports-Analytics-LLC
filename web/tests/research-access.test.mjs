import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('research RPCs deny non-team roles and cannot bypass internal implementations', async () => {
  const db = new PGlite()
  try {
    const load = async (filename) => db.exec(await readFile(new URL(`../../supabase/migrations/${filename}`, import.meta.url), 'utf8'))
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema auth;
      create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
      create function public.website_member_access() returns jsonb language sql as $$ select current_setting('test.access')::jsonb $$;
      select set_config('test.uid', '00000000-0000-0000-0000-000000000001', false);
    `)
    await load('20260930260000_api_sports_nfl.sql')
    await load('20260930300000_api_sports_multi_event_cache.sql')
    await load('20261011170000_sports_model_history.sql')
    await load('20261011180000_nflverse_team_efficiency.sql')
    await db.exec(`
      insert into public.api_sports_nfl_games (game_id,league_id,season,kickoff_at,status_short,status_long,home_team_name,away_team_name)
        values (1,1,2026,now(),'FT','Finished','Home','Away');
      insert into public.api_sports_events (sport_slug,event_id,league_id,season,event_name,start_at)
        values ('hockey','1','57','2026','Test game',now());
      insert into public.api_sports_nfl_teams (team_id,name,code) values (1,'Home','BAL');
      insert into public.nflverse_team_game_efficiency values (2026,'test',current_date,'BAL',1,1,1,1,1,1,1,1,now());
      alter default privileges grant execute on functions to anon, authenticated;
    `)
    const rpcs = ['public_nfl_model_games()', "public_sport_model_events('hockey','57')", 'public_nfl_team_efficiency()']
    const original = await Promise.all(rpcs.map(async (rpc) => (await db.query(`select * from public.${rpc}`)).rows))
    await load('20261011214000_operator_research_access.sql')
    const call = async (rpc, role = 'authenticated') => {
      await db.exec(`set role ${role}`)
      try { return (await db.query(`select * from public.${rpc}`)).rows }
      finally { await db.exec('reset role') }
    }
    for (const access of [
      { state: 'signed-out' }, { state: 'age-required' }, { state: 'discord-required' },
      { state: 'membership-required' }, { state: 'launch-pending' },
      ...['paid', 'trial', 'operator', 'owner'].map((kind) => ({ state: 'active', kind })),
    ]) {
      await db.query("select set_config('test.access', $1, false)", [JSON.stringify(access)])
      for (const [index, rpc] of rpcs.entries()) {
        if (access.kind === 'operator' || access.kind === 'owner') assert.deepEqual(await call(rpc), original[index])
        else await assert.rejects(call(rpc), /OPERATOR or Owner/)
      }
    }
    await db.exec("select set_config('test.uid', '', false)")
    for (const rpc of rpcs) {
      await assert.rejects(call(rpc), /OPERATOR or Owner/)
      await assert.rejects(call(rpc, 'anon'), /permission denied/)
      for (const role of ['anon', 'authenticated']) await assert.rejects(call(`internal_${rpc}`, role), /permission denied/)
    }
    await assert.rejects(call('require_operator_research_access()'), /permission denied/)
    assert.equal((await call('public_nfl_games()', 'anon')).length, 1)
    assert.equal((await call("public_sport_events('hockey')", 'anon')).length, 1)
    for (const table of ['api_sports_model_backfill_state', 'nflverse_team_game_efficiency']) {
      for (const role of ['anon', 'authenticated']) {
        await db.exec(`set role ${role}`)
        try { await assert.rejects(db.query(`select * from public.${table}`), /permission denied/) }
        finally { await db.exec('reset role') }
      }
    }
  } finally { await db.close() }
})
