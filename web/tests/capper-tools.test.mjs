import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('capper tools approval, scoped cache, revocation and atomic AI quotas', async () => {
  const db = new PGlite()
  const owner = '00000000-0000-0000-0000-000000000001', capper = '00000000-0000-0000-0000-000000000002'
  const rpc = async (name, args = []) => (await db.query(`select ${name}(${args.map((_, i) => `$${i + 1}`).join(',')}) as data`, args)).rows[0].data
  const load = async name => db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls; create schema auth;
      create table auth.users(id uuid primary key);
      create table auth.identities(user_id uuid,provider text,provider_id text);
      create table member_profiles(user_id uuid,age_verified_at timestamptz);
      create table capper_api_keys(discord_user_id text,revoked_at timestamptz);
      create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
      create function website_member_access() returns jsonb language sql as $$ select current_setting('test.access')::jsonb $$;
      create function website_can_edit_all_cappers() returns boolean language sql as $$ select auth.uid()='${owner}' $$;
      create function website_capper_ids() returns text[] language sql as $$ select case when current_setting('test.roster',true)='stale' then '{}'::text[] else array['111','222'] end $$;
      insert into auth.users values('${owner}'),('${capper}');
      insert into auth.identities values('${owner}','discord','111'),('${capper}','discord','222');
      insert into member_profiles values('${owner}',now()),('${capper}',now());
      select set_config('test.access','{"state":"active"}',false);
    `)
    await load('20260930260000_api_sports_nfl.sql')
    await load('20260930300000_api_sports_multi_event_cache.sql')
    await load('20261011170000_sports_model_history.sql')
    await load('20261003060000_player_game_stats.sql')
    await load('20261014120000_sport_model_projections.sql')
    await load('20261017020000_capper_research_workspace.sql')
    await load('20261017050000_member_stats_workspace.sql')
    await load('20261017070000_capper_research_tools.sql')
    await load('20261017080000_capper_research_ai_limits.sql')
    await db.exec(`insert into api_sports_nfl_games(game_id,league_id,season,kickoff_at,status_short,status_long,home_team_id,home_team_name,away_team_id,away_team_name)
      values(1,1,2026,'2026-10-20','NS','Not started',1,'Home',2,'Away');
      insert into sport_model_weather values('nfl','1',65,7,0,'forecast',now());
      insert into sport_model_injuries values('nfl','1','p1','Visible player','Out','Knee',current_date,now()),
      ('nfl','3','p2','Other team','Out','Knee',current_date,now()),
      ('nfl','1','p3','Old injury','Out','Knee',current_date-30,now());`)
    const uid = value => db.query("select set_config('request.jwt.claim.sub',$1,false)", [value])
    await uid(capper)
    assert.equal(await rpc('capper_tools_access'), false)
    await assert.rejects(rpc('capper_tools_game', ['nfl', '1']), /Approved capper/)
    await assert.rejects(rpc('manage_capper_tools', ['222', true]), /Owner/)
    await uid(owner)
    await rpc('manage_capper_tools', ['222', true])
    await assert.rejects(rpc('manage_capper_tools', ['999', true]), /current capper/)
    await uid(capper)
    assert.equal(await rpc('capper_tools_access'), true)
    const game = await rpc('capper_tools_game', ['nfl', '1'])
    assert.equal(game.weather.temperature_f, 65)
    assert.equal(game.injuries.length, 1)
    assert.equal(game.injuries[0].player_name, 'Visible player')
    assert.ok(!('projection' in game))
    await rpc('reserve_capper_tools_ai')
    await assert.rejects(rpc('reserve_capper_tools_ai'), /Wait 15 seconds/)
    await db.exec(`update capper_tools_ai_usage set requested_at=now()-interval '1 minute';
      insert into capper_tools_ai_usage(user_id,requested_at) select '${capper}',now()-interval '1 minute' from generate_series(1,23);`)
    assert.ok(await rpc('reserve_capper_tools_ai'), '25th action is no longer blocked')
    await db.exec(`update capper_tools_ai_usage set requested_at=now()-interval '1 minute';
      insert into capper_tools_ai_usage(user_id,requested_at) select '${capper}',now()-interval '1 minute' from generate_series(1,24);`)
    assert.ok(await rpc('reserve_capper_tools_ai'), '50th capper action is allowed')
    await db.exec("update capper_tools_ai_usage set requested_at=now()-interval '1 minute'")
    await assert.rejects(rpc('reserve_capper_tools_ai'), /50 per capper, 500 shared, UTC/)
    assert.equal((await db.query('select count(*)::int as count from capper_tools_ai_usage')).rows[0].count, 50)
    await db.exec(`delete from capper_tools_ai_usage; insert into capper_tools_ai_usage(user_id,requested_at)
      select '${owner}',now()-interval '1 minute' from generate_series(1,249);`)
    assert.ok(await rpc('reserve_capper_tools_ai'), '250th shared action is no longer blocked')
    await db.exec(`update capper_tools_ai_usage set requested_at=now()-interval '1 minute';
      insert into capper_tools_ai_usage(user_id,requested_at) select '${owner}',now()-interval '1 minute' from generate_series(1,249);`)
    assert.ok(await rpc('reserve_capper_tools_ai'), '500th shared action is allowed')
    await db.exec("update capper_tools_ai_usage set requested_at=now()-interval '1 minute'")
    await assert.rejects(rpc('reserve_capper_tools_ai'), /50 per capper, 500 shared, UTC/)
    assert.equal((await db.query('select count(*)::int as count from capper_tools_ai_usage')).rows[0].count, 500)
    await uid(owner); await rpc('manage_capper_tools', ['222', false]); await uid(capper)
    await db.exec("insert into capper_api_keys values('222',null)")
    assert.equal(await rpc('capper_tools_access'), false, 'explicit block overrides API credentials')
    await db.exec("delete from capper_tools_grants where discord_user_id='222'")
    assert.equal(await rpc('capper_tools_access'), true, 'active integration key qualifies')
    await db.exec("select set_config('test.roster','stale',false)")
    assert.equal(await rpc('capper_tools_access'), false)
    await db.exec("select set_config('test.roster','fresh',false); select set_config('test.access','{\"state\":\"age-required\"}',false)")
    assert.equal(await rpc('capper_tools_access'), false)
    for (const role of ['anon', 'authenticated']) {
      await db.exec(`set role ${role}`)
      await assert.rejects(db.query('select * from capper_tools_ai_usage'), /permission denied/)
      await assert.rejects(db.query('select * from capper_tools_grants'), /permission denied/)
      if (role === 'anon') await assert.rejects(rpc('capper_tools_access'), /permission denied/)
      await db.exec('reset role')
    }
  } finally { await db.close() }
})
