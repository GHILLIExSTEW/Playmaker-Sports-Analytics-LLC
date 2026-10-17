import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('research workspace authorization, caches, cooldowns, claims and performance', async () => {
  const db = new PGlite()
  const user = '00000000-0000-0000-0000-000000000001'
  const other = '00000000-0000-0000-0000-000000000002'
  const load = async name => db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  const rpc = async (sql, role = 'authenticated') => {
    await db.exec(`set role ${role}`)
    try { return (await db.query(sql)).rows } finally { await db.exec('reset role') }
  }
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema auth;
      create table auth.users(id uuid primary key);
      create table auth.identities(user_id uuid, provider text, provider_id text);
      create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
      create function public.website_member_access() returns jsonb language sql as $$ select current_setting('test.access')::jsonb $$;
      create function public.require_operator_research_access() returns void language plpgsql as $$
      begin
        if auth.uid() is null or public.website_member_access()->>'state' is distinct from 'active'
          or coalesce(public.website_member_access()->>'kind','') not in ('owner','operator','research')
        then raise exception 'Research access required'; end if;
      end $$;
      insert into auth.users values ('${user}'),('${other}');
      insert into auth.identities values ('${user}','discord','123'),('${other}','discord','456');
      select set_config('request.jwt.claim.sub','${user}',false);
      select set_config('test.access','{"state":"active","kind":"operator"}',false);
    `)
    await load('20260930260000_api_sports_nfl.sql')
    await load('20260930300000_api_sports_multi_event_cache.sql')
    await load('20261011170000_sports_model_history.sql')
    await load('20261003060000_player_game_stats.sql')
    await load('20261014120000_sport_model_projections.sql')
    await db.exec(`
      create table public.sports(id bigint, name text);
      create table public.users(id bigint, display_name text);
      create table public.plays(id bigint, user_id bigint, sport_id bigint, message_id text, created_at timestamptz,
        settled_at timestamptz, tracking_scope text, status text, units numeric, odds integer, legs integer);
      create table public.play_legs(play_id bigint, leg_number integer, details jsonb);
      create view public.display_plays as select * from public.plays where created_at >= now()-interval '30 days';
      insert into public.sports values (1,'NFL'); insert into public.users values (1,'Capper');
      insert into public.plays values
        (1,1,1,'one',now(),now(),'capper','win',2,150,2),
        (2,1,1,'two',now(),now(),'capper','loss',1,-110,1),
        (3,1,1,'three',now(),now(),'official','win',100,100,1),
        (4,1,1,'four',now(),now(),'capper','open',10,100,1),
        (5,1,1,'five',now()-interval '31 days',now(),'capper','win',100,100,1),
        (6,1,1,'one',now(),now(),'capper','win',2,150,2),
        (7,1,1,'seven',now(),now(),'capper','void',5,100,1),
        (8,1,1,'eight',now(),now(),'capper','partial',2,-200,1);
      insert into public.play_legs values (1,1,'{"market":"spread"}'),(1,2,'{"market":"total"}'),
        (2,1,'{"market":"moneyline"}');
      insert into public.api_sports_nfl_games (game_id,league_id,season,kickoff_at,status_short,status_long,home_team_id,home_team_name,away_team_id,away_team_name,home_score,away_score)
        values (1,1,2026,now()+interval '1 day','NS','Not started',1,'Home',2,'Away',null,null),
        (2,1,2026,now()-interval '1 day','FT','Finished',1,'Home',2,'Away',10,7);
      insert into public.api_sports_events (sport_slug,event_id,league_id,season,event_name,start_at,home_id,home_name,away_id,away_name,status_code)
        values ('hockey','10','57','2026','Test',now(),'1','Home','2','Away','NS'),
        ('basketball','11','116','2026','College',now(),'1','Home','2','Away','NS'),
        ('basketball','12','8','2026','Wrong league',now(),'1','Home','2','Away','NS');
      alter default privileges grant execute on functions to anon, authenticated;
    `)
    await load('20261017020000_capper_research_workspace.sql')
    await load('20261017030000_research_game_search.sql')
    for (const state of [{ state: 'signed-out' }, { state: 'age-required' }, { state: 'active', kind: 'paid' }, { state: 'active', kind: 'trial' }]) {
      await db.query("select set_config('test.access',$1,false)", [JSON.stringify(state)])
      for (const sql of [
        "select operator_research_workspace('nfl','1')", 'select operator_capper_performance()',
        "select request_research_refresh('nfl','1','game')",
        "select search_research_games('nfl')",
      ]) await assert.rejects(rpc(sql), /Research access required/)
    }
    await db.exec(`select set_config('test.access','{"state":"active","kind":"research"}',false)`)
    const data = (await rpc("select operator_research_workspace('nfl','1') as data"))[0].data
    assert.equal(data.games.length, 2)
    assert.equal(data.history.length, 1)
    assert.equal(data.games.find(g => g.game_id === '1').home_score, null)
    assert.deepEqual(data.players, [])
    assert.equal(data.odds, null)
    const search = async args => (await rpc(`select search_research_games(${args}) as data`))[0].data
    assert.equal((await search("'nfl','home away'")).total, 2)
    assert.equal((await search("'nfl','HOME'")).games.length, 2)
    assert.equal((await search("'nfl','1'")).games[0].game_id, '1')
    assert.equal((await search("'nfl','FT'")).games[0].game_id, '2')
    assert.equal((await search("'nfl','%'")).total, 0)
    assert.equal((await search("'nfl','missing'")).total, 0)
    assert.equal((await search("'nfl','',now(),now()+interval '2 days'")).total, 1)
    await assert.rejects(search("'nfl','',now(),now()-interval '1 day'"), /Start date/)
    await assert.rejects(search("'nfl','',null,null,-1"), /Invalid search offset/)
    await assert.rejects(search("'bad'"), /Unsupported/)
    await assert.rejects(rpc("select search_research_games('nfl')", 'anon'), /permission denied/)
    await db.exec(`insert into public.api_sports_nfl_games
      (game_id,league_id,season,kickoff_at,status_short,status_long,home_team_id,home_team_name,away_team_id,away_team_name)
      select id,1,2025,'2025-01-01'::timestamptz + id * interval '1 minute','FT','Finished',1,'Historical',2,'Opponent'
      from generate_series(100,324) id`)
    const first = await search("'nfl','historical'")
    const second = await search("'nfl','historical',null,null,20")
    assert.equal(first.total, 225)
    assert.equal(first.games.length, 20)
    assert.equal(second.games.length, 20)
    assert.equal(first.games.some(g => second.games.some(other => other.game_id === g.game_id)), false)
    assert.equal((await search("'nfl','324'")).games[0].game_id, '324')
    await db.exec('delete from public.api_sports_nfl_games where game_id >= 100')
    const college = (await rpc("select operator_research_workspace('basketball:116','11') as data"))[0].data
    assert.equal(college.games.length, 1)
    await assert.rejects(rpc("select operator_research_workspace('basketball:8',null)"), /Unsupported/)
    await assert.rejects(rpc("select operator_research_workspace('nfl','10')"), /Select a cached game/)
    await assert.rejects(rpc("select request_research_refresh('hockey:57','10','players')"), /no supported/)
    await assert.rejects(rpc("select request_research_refresh('nfl','1','bad')"), /Unsupported/)
    const request = (await rpc("select request_research_refresh('nfl','1','game') as id"))[0].id
    await assert.rejects(rpc("select request_research_refresh('nfl','2','game')"), /per user per minute/)
    await db.exec(`select set_config('request.jwt.claim.sub','${other}',false)`)
    await assert.rejects(rpc("select request_research_refresh('nfl','1','game')"), /five-minute cooldown/)
    const hidden = (await rpc("select operator_research_workspace('nfl',null) as data"))[0].data
    assert.deepEqual(hidden.refreshes, [])
    await assert.rejects(rpc('select * from research_refresh_requests'), /permission denied/)
    await assert.rejects(rpc('select * from research_odds'), /permission denied/)
    await assert.rejects(rpc('select * from research_games'), /permission denied/)
    for (const role of ['anon','authenticated']) {
      await assert.rejects(rpc('select * from claim_research_refresh()', role), /permission denied/)
      await assert.rejects(rpc(`select research_refresh_authorized('${user}','123')`, role), /permission denied/)
    }
    for (const sql of ["select operator_research_workspace('nfl',null)", 'select operator_capper_performance()', "select request_research_refresh('nfl','1','game')"]) {
      await assert.rejects(rpc(sql, 'anon'), /permission denied/)
    }
    const claims = await rpc('select * from claim_research_refresh()', 'service_role')
    assert.equal(claims.length, 1); assert.equal(claims[0].id, request); assert.equal(claims[0].state, 'running')
    assert.equal((await rpc('select * from claim_research_refresh()', 'service_role')).length, 0)
    await db.exec(`update research_refresh_requests set updated_at=now()-interval '11 minutes' where id='${request}'`)
    await rpc('select * from claim_research_refresh()', 'service_role')
    assert.equal((await db.query(`select state from research_refresh_requests where id='${request}'`)).rows[0].state, 'failed')
    await db.exec("update research_refresh_requests set requested_at=now()-interval '6 minutes'")
    await rpc("select request_research_refresh('nfl','1','game')")
    await db.exec("update research_refresh_requests set requested_at=now()-interval '16 minutes'")
    assert.equal((await rpc('select * from claim_research_refresh()', 'service_role')).length, 0)
    const performance = (await rpc('select operator_capper_performance() as data'))[0].data
    assert.equal(performance.length, 4)
    assert.equal(performance.find(row => row.market === 'Parlay').net_units, 3)
    assert.equal(performance.find(row => row.market === 'moneyline').net_units, -1)
    assert.equal(performance.find(row => row.status === 'void').net_units, 0)
    assert.equal(performance.find(row => row.status === 'partial').net_units, 0.5)
    assert.equal((await rpc(`select research_refresh_authorized('${user}','wrong') as allowed`, 'service_role'))[0].allowed, false)
    assert.equal((await rpc(`select research_refresh_authorized('${user}','123') as allowed`, 'service_role'))[0].allowed, true)
  } finally { await db.close() }
})
