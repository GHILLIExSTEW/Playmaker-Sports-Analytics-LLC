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
    await load('20261003050000_api_request_budget.sql')
    await load('20261011193500_shared_api_daily_budget.sql')
    await load('20261017090000_capper_team_pulls.sql')
    await load('20261017110000_capper_model_projections.sql')
    await load('20261017120000_capper_pull_model_refresh.sql')
    await load('20261017130000_idempotent_worker_claims.sql')
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
    await rpc('manage_capper_tools', ['111', true])
    await assert.rejects(rpc('manage_capper_tools', ['999', true]), /current capper/)
    await uid(capper)
    assert.equal(await rpc('capper_tools_access'), true)
    const game = await rpc('capper_tools_game', ['nfl', '1'])
    assert.equal(game.weather.temperature_f, 65)
    assert.equal(game.injuries.length, 1)
    assert.equal(game.injuries[0].player_name, 'Visible player')
    assert.equal(game.projection, null)
    await db.exec(`update api_sports_nfl_games set kickoff_at=now()+interval '3 days' where game_id=1;
      insert into sport_model_projections
      (competition,game_id,start_at,status_code,home_id,home_name,away_id,away_name,home_score,away_score,home_probability,
        distribution,margin_sd,total_sd,home_sample,away_sample,league_sample,model_version,data_updated_at,generated_at,details)
      values('nfl','1',now()+interval '3 days','NS','1','Home','2','Away',27.4,21.2,.64,
        'normal',14,14,10,9,200,'ratings-v2',now()-interval '2 hours',now()-interval '1 hour','{"private_parameter":99}');`)
    const model = (await rpc('capper_tools_game', ['nfl','1'])).projection
    assert.equal(model.home_score, 27.4)
    assert.equal(model.home_probability, .64)
    assert.equal(model.model_version, 'ratings-v2')
    assert.ok(!('details' in model))
    assert.ok(!('market_edge' in model))
    for (const change of [
      "home_id='999'", "start_at=now()+interval '4 days'", "generated_at=now()+interval '4 days'",
      "data_updated_at=now()+interval '4 days'",
    ]) {
      await db.exec(`update sport_model_projections set ${change}`)
      assert.equal((await rpc('capper_tools_game', ['nfl','1'])).projection, null, change)
      await db.exec(`update sport_model_projections set home_id='1',start_at=(select kickoff_at from api_sports_nfl_games where game_id=1),
        generated_at=now()-interval '1 hour',data_updated_at=now()-interval '2 hours'`)
    }
    await db.exec("update api_sports_nfl_games set status_short='FT' where game_id=1")
    assert.equal((await rpc('capper_tools_game', ['nfl','1'])).projection, null, 'no pregame estimate for finished game')
    await db.exec("update api_sports_nfl_games set status_short='NS' where game_id=1")
    await db.exec("update api_sports_nfl_games set kickoff_at=now()-interval '1 hour' where game_id=1; update sport_model_projections set start_at=now()-interval '1 hour'")
    assert.equal((await rpc('capper_tools_game', ['nfl','1'])).projection, null, 'past matchups cannot leak postgame projections')
    await db.exec("update api_sports_nfl_games set kickoff_at=now()+interval '3 days' where game_id=1; update sport_model_projections set start_at=now()+interval '3 days'")
    await assert.rejects(rpc('reserve_capper_team_pull', ['nfl', '1', '999']), /Select a team/)
    const pull = await rpc('reserve_capper_team_pull', ['nfl', '1', '1'])
    assert.equal(pull.season, '2026')
    assert.equal(pull.product, 'american-football')
    assert.equal((await db.query('select system_requests from api_request_budget')).rows[0].system_requests, 2)
    await assert.rejects(rpc('reserve_capper_team_pull', ['nfl', '1', '1']), /Wait 15 seconds/)
    await db.exec("update capper_team_pulls set requested_at=now()-interval '1 minute'")
    await db.exec(`insert into capper_team_pulls(user_id,competition,team_id,season,requested_at)
      select '${capper}','nfl','1','2026',now()-interval '1 minute' from generate_series(1,48)`)
    assert.ok((await rpc('reserve_capper_team_pull', ['nfl', '1', '1'])).request_id, '50th data pull allowed')
    await db.exec("update capper_team_pulls set requested_at=now()-interval '1 minute'")
    await assert.rejects(rpc('reserve_capper_team_pull', ['nfl', '1', '1']), /50 per capper, 500 shared/)
    await db.exec(`delete from capper_team_pulls; insert into capper_team_pulls(user_id,competition,team_id,season,requested_at)
      select '${owner}','nfl','1','2026',now()-interval '1 minute' from generate_series(1,499)`)
    const finalPull = await rpc('reserve_capper_team_pull', ['nfl', '1', '1'])
    assert.ok(finalPull.request_id, '500th shared data pull allowed')
    await db.exec("update capper_team_pulls set requested_at=now()-interval '1 minute'")
    await assert.rejects(rpc('reserve_capper_team_pull', ['nfl', '1', '1']), /50 per capper, 500 shared/)
    const normalized = (await db.query('select * from api_sports_nfl_games')).rows[0]
    normalized.home_team_name = 'Fresh Home'
    await rpc('finish_capper_team_pull', [finalPull.request_id, [normalized], [], false])
    assert.equal((await rpc('capper_tools_game', ['nfl', '1'])).game.home_name, 'Fresh Home')
    assert.equal((await rpc('capper_tools_game', ['nfl', '1'])).injuries.length, 0)
    assert.equal(await rpc('finish_capper_team_pull', [finalPull.request_id, [], [], false]), false, 'write once')
    assert.equal((await rpc('capper_model_refresh_status', [finalPull.request_id])).state, 'pending')
    await uid(owner)
    await assert.rejects(rpc('capper_model_refresh_status', [finalPull.request_id]), /not found/)
    await uid(capper)
    const job = await rpc('claim_capper_model_refresh')
    assert.equal(job.competition, 'nfl')
    assert.equal(job.request_id, finalPull.request_id)
    assert.equal((await rpc('capper_model_refresh_status', [finalPull.request_id])).state, 'running')
    const otherPull = (await db.query('select id from capper_team_pulls where id<>$1 limit 1', [finalPull.request_id])).rows[0].id
    await assert.rejects(rpc('finish_capper_team_pull', [otherPull, null, [], false]), /Invalid team games/)
    assert.equal((await db.query('select count(*)::int as n from capper_model_refreshes')).rows[0].n, 1, 'failed persistence cannot enqueue')
    await rpc('finish_capper_team_pull', [otherPull, [], [], false])
    assert.equal(await rpc('claim_capper_model_refresh'), null, 'running job is not claimed twice')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, owner, 1]), false, 'claim token required')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, job.claim_token, 0]), true, 'zero projections is explicit, not fabricated')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, job.claim_token, 1]), false, 'completion is write once')
    assert.equal((await rpc('capper_model_refresh_status', [finalPull.request_id])).state, 'completed')
    const secondJob = await rpc('claim_capper_model_refresh')
    assert.equal(secondJob.request_id, otherPull, 'next same-league job waits for completion')
    assert.equal(await rpc('finish_capper_model_refresh', [otherPull, secondJob.claim_token, null]), true)
    assert.equal((await db.query('select state from capper_model_refreshes where request_id=$1', [otherPull])).rows[0].state, 'failed')
    await db.exec("update capper_model_refreshes set state='pending',requested_at=now()-interval '4 minutes'")
    assert.equal((await rpc('capper_model_refresh_status', [finalPull.request_id])).state, 'failed', 'offline worker times out explicitly')
    assert.equal(await rpc('claim_capper_model_refresh'), null, 'expired jobs are not retried')
    assert.equal((await db.query('select state from capper_model_refreshes')).rows[0].state, 'failed')
    await db.exec(`update capper_model_refreshes set state='pending',requested_at=now(),started_at=null,completed_at=null,claim_token=null,projections=null
      where request_id='${finalPull.request_id}'`)
    const token = '00000000-0000-0000-0000-0000000000aa', otherToken = '00000000-0000-0000-0000-0000000000bb'
    const claimed = await rpc('claim_capper_model_refresh', [token])
    assert.equal(claimed.request_id, finalPull.request_id)
    assert.equal(claimed.claim_token, token)
    assert.deepEqual(await rpc('claim_capper_model_refresh', [token]), claimed, 'replayed claim returns the same job')
    assert.equal(await rpc('claim_capper_model_refresh', [otherToken]), null, 'replay does not claim a second job')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, token, 3]), true)
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, token, 3]), true, 'replayed completion reports the saved result')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, token, 4]), false, 'a different result is still rejected')
    assert.equal(await rpc('finish_capper_model_refresh', [finalPull.request_id, otherToken, 3]), false)
    assert.equal(await rpc('claim_capper_model_refresh', [token]), null, 'a finished token is not reclaimed')
    const researchId = (await db.query(`insert into research_refresh_requests(user_id,discord_user_id,competition,game_id,kind)
      values($1,'222','nfl','1','odds') returning id`, [capper])).rows[0].id
    const claimResearch = async value => (await db.query('select * from claim_research_refresh($1)', [value])).rows
    assert.equal((await claimResearch(token))[0].id, researchId)
    assert.equal((await claimResearch(token))[0].id, researchId, 'replayed research claim returns the same request')
    assert.equal((await claimResearch(otherToken)).length, 0, 'replay does not claim a second request')
    assert.equal(await rpc('finish_research_refresh', [researchId, otherToken, 'completed', 'ok']), false)
    assert.equal(await rpc('finish_research_refresh', [researchId, token, 'completed', 'ok']), true)
    assert.equal(await rpc('finish_research_refresh', [researchId, token, 'completed', 'ok']), true, 'replayed finish reports the saved result')
    assert.equal(await rpc('finish_research_refresh', [researchId, token, 'failed', 'ok']), false)
    assert.equal((await claimResearch(token)).length, 0)
    await db.exec('delete from capper_team_pulls')
    const failedPull = await rpc('reserve_capper_team_pull', ['nfl','1','1'])
    await rpc('finish_capper_team_pull', [failedPull.request_id, [], null, true])
    assert.equal((await db.query('select count(*)::int as n from capper_model_refreshes')).rows[0].n, 0, 'failed provider pull does not queue a model')
    await db.exec('delete from capper_team_pulls')
    await db.exec("update api_request_budget set system_requests=7500")
    await assert.rejects(rpc('reserve_capper_team_pull', ['nfl', '1', '1']), /7500-request/)
    assert.equal((await db.query('select count(*)::int as n from capper_team_pulls')).rows[0].n, 0)
    await db.exec('update api_request_budget set system_requests=0')
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
    await assert.rejects(rpc('capper_tools_game', ['nfl','1']), /Approved capper/)
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
      await assert.rejects(db.query('select * from capper_team_pulls'), /permission denied/)
      await assert.rejects(db.query('select * from capper_model_refreshes'), /permission denied/)
      await assert.rejects(rpc('claim_capper_model_refresh'), /permission denied/)
      await assert.rejects(rpc('claim_capper_model_refresh', [owner]), /permission denied/)
      await assert.rejects(rpc('claim_research_refresh', [owner]), /permission denied/)
      await assert.rejects(rpc('finish_research_refresh', [owner, owner, 'failed', 'x']), /permission denied/)
      await assert.rejects(rpc('finish_capper_model_refresh', [finalPull.request_id, owner, 1]), /permission denied/)
      await assert.rejects(rpc('save_capper_team_pull', [finalPull.request_id, [], [], false]), /permission denied/)
      await assert.rejects(db.query('select * from sport_model_projections'), /permission denied/)
      await assert.rejects(rpc('finish_capper_team_pull', [finalPull.request_id, [], [], false]), /permission denied/)
      if (role === 'anon') await assert.rejects(rpc('capper_tools_access'), /permission denied/)
      await db.exec('reset role')
    }
  } finally { await db.close() }
})
