import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('member stats: cached search, pregame workspace, member access and isolated RPC-only favorites', async () => {
  const db = new PGlite()
  const user = '00000000-0000-0000-0000-000000000001'
  const other = '00000000-0000-0000-0000-000000000002'
  const load = async name => db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  const rpc = async (sql, params = [], role = 'authenticated') => {
    await db.exec(`set role ${role}`)
    try { return (await db.query(sql, params)).rows } finally { await db.exec('reset role') }
  }
  const call = async (sql, params = [], role = 'authenticated') => (await rpc(`select ${sql} as data`, params, role))[0].data
  const access = async value => db.query("select set_config('test.access',$1,false)", [JSON.stringify(value)])
  const uid = async value => db.query("select set_config('request.jwt.claim.sub',$1,false)", [value])
  const save = (competition, kind, id, label = 'Untrusted UI label', saved = true) =>
    call('save_member_stats_favorite($1,$2,$3,$4,$5)', [competition, kind, id, label, saved])
  const endpoints = [
    "member_stats_games('nfl')", "member_stats_game('nfl','1')", 'member_stats_watchlist()',
    "save_member_stats_favorite('nfl','team','1','Home',true)",
  ]
  const researchDefinitions = () => db.query(`
    select p.proname, pg_get_functiondef(p.oid) as definition, p.proacl::text as acl
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname in
      ('search_research_games','operator_research_workspace','request_research_refresh',
       'operator_capper_performance','claim_research_refresh','research_refresh_authorized','require_operator_research_access')
    order by p.proname
  `)
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema auth;
      create table auth.users(id uuid primary key);
      create table auth.identities(user_id uuid, provider text, provider_id text);
      create function auth.uid() returns uuid language sql as $$
        select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
      $$;
      create function public.website_member_access() returns jsonb language sql as $$
        select current_setting('test.access')::jsonb
      $$;
      create function public.require_operator_research_access() returns void language plpgsql as $$
      begin
        if auth.uid() is null or public.website_member_access()->>'state' is distinct from 'active'
          or coalesce(public.website_member_access()->>'kind','') not in ('owner','operator','research')
          then raise exception 'Research access required'; end if;
      end $$;
      revoke all on function public.require_operator_research_access() from public, anon, authenticated;
      insert into auth.users values ('${user}'),('${other}');
    `)
    await load('20260930260000_api_sports_nfl.sql')
    await load('20260930300000_api_sports_multi_event_cache.sql')
    await load('20261011170000_sports_model_history.sql')
    await load('20261003060000_player_game_stats.sql')
    await load('20261014120000_sport_model_projections.sql')
    await load('20261017020000_capper_research_workspace.sql')
    await load('20261017030000_research_game_search.sql')
    const originalResearch = (await researchDefinitions()).rows
    await db.exec(`
      insert into public.api_sports_nfl_games
        (game_id,league_id,season,kickoff_at,status_short,status_long,home_team_id,home_team_name,away_team_id,away_team_name,home_score,away_score)
      values
        (1,1,2026,'2026-10-17 12:00Z','NS','Not started',1,'Home',2,'Away',null,null),
        (2,1,2026,'2026-10-18 12:00Z','FT','Finished',1,'Home',2,'Away',30,20),
        (3,1,2026,'2026-10-16 12:00Z','LIVE','Live',1,'Home',2,'Away',3,2),
        (4,1,2026,'2026-10-16 12:00Z','FT','Finished',90,'Unrelated',91,'Opponent',3,2),
        (5,1,2026,'2026-10-17 12:00Z','FT','Finished',2,'Away',1,'Home',3,2);
      insert into public.api_sports_nfl_games
        (game_id,league_id,season,kickoff_at,status_short,status_long,home_team_id,home_team_name,away_team_id,away_team_name)
      select id,1,2025,'2025-01-01'::timestamptz + id * interval '1 minute',
        (array['FT','AOT','AP','COMPLETED','FINISHED'])[1 + id % 5],'Finished',
        case when id % 2 = 0 then 1 else 2 end, 'Historical Home',1000 + id,'Opponent ' || id
      from generate_series(100,324) id;
      insert into public.api_sports_events
        (sport_slug,event_id,league_id,season,event_name,start_at,home_id,home_name,away_id,away_name,status_code)
      values ('basketball','10','12','2026','NBA','2026-10-16 12:00Z','1','NBA Home','2','NBA Away','FT'),
        ('basketball','11','116','2026','College','2026-10-16 12:00Z','3','College Home','4','College Away','FT'),
        ('basketball','12','8','2026','Unsupported','2026-10-16 12:00Z','5','Wrong','6','League','FT'),
        ('hockey','20','57','2026','NHL','2026-10-16 12:00Z','1','NHL Home','2','NHL Away','FT'),
        ('baseball','21','1','2026','MLB','2026-10-16 12:00Z','1','MLB Home','2','MLB Away','FT'),
        ('ncaa','22','2','2026','NCAA','2026-10-16 12:00Z','1','NCAA Home','2','NCAA Away','FT');
      insert into public.api_sports_player_game_stats values
        ('nfl',1,'[{"team":{"id":1,"name":"Home"},"groups":[{"name":"Passing","players":[{"player":{"id":42,"name":"Cached QB"},"statistics":[{"name":"yards","value":"250"}]}]}]}]','2026-10-17 10:00Z'),
        ('nfl',2,'[{"game":{"id":2},"team":{"id":1},"player":{"id":99,"name":"Future player"},"statistics":{"yards":5}}]','2026-10-18 10:00Z'),
        ('basketball',10,'[{"game":{"id":10},"team":{"id":"1"},"player":{"id":"43","name":"Cached Guard"},"statistics":{"points":25}}]','2026-10-16 10:00Z'),
        ('basketball',11,'[{"game":{"id":11},"team":{"id":"3"},"player":{"id":"44","name":"College Guard"},"statistics":{"points":20}}]','2026-10-16 10:00Z');
      insert into public.api_sports_player_game_stats
      select 'nfl',id,'[]'::jsonb,'2025-01-01'::timestamptz from generate_series(100,111) id;
      alter default privileges grant execute on functions to anon, authenticated;
    `)
    await load('20261017050000_member_stats_workspace.sql')
    assert.deepEqual((await researchDefinitions()).rows, originalResearch)
    await uid(user)
    for (const kind of ['paid', 'trial', 'operator', 'owner', 'research']) {
      await access({ state: 'active', kind })
      for (const endpoint of endpoints) await call(endpoint)
    }
    for (const value of [
      { state: 'signed-out' }, { state: 'age-required', kind: 'owner' },
      { state: 'discord-required', kind: 'operator' }, { state: 'membership-required', kind: 'paid' },
      { state: 'launch-pending' }, { state: 'inactive', kind: 'trial' }, {}, null,
    ]) {
      await access(value)
      for (const endpoint of endpoints) await assert.rejects(call(endpoint), /Active member access required/)
    }
    await access({ state: 'active', kind: 'paid' })
    await uid('')
    for (const endpoint of endpoints) await assert.rejects(call(endpoint), /Active member access required/)
    await uid(user)
    for (const endpoint of endpoints) await assert.rejects(call(endpoint, [], 'anon'), /permission denied/)
    for (const role of ['anon', 'authenticated']) {
      await assert.rejects(call('require_member_stats_access()', [], role), /permission denied/)
      for (const sql of [
        'select * from public.member_stats_watchlist',
        `insert into public.member_stats_watchlist values ('${user}','nfl','team','2','Fake')`,
        "update public.member_stats_watchlist set label = 'Fake'",
        'delete from public.member_stats_watchlist',
        'select * from public.research_games', 'select * from public.api_sports_player_game_stats',
      ]) await assert.rejects(rpc(sql, [], role), /permission denied/)
    }
    assert.equal((await db.query("select relrowsecurity from pg_class where oid = 'public.member_stats_watchlist'::regclass")).rows[0].relrowsecurity, true)
    await assert.rejects(call("search_research_games('nfl')"), /Research access required/)
    await assert.rejects(call("operator_research_workspace('nfl','1')"), /Research access required/)

    const first = await call("member_stats_games('nfl','historical home')")
    const second = await call("member_stats_games('nfl','historical home',null,null,20)")
    assert.equal(first.total, 225)
    assert.equal(first.games.length, 20)
    assert.equal(second.games.length, 20)
    assert.equal(first.games.some(g => second.games.some(h => h.game_id === g.game_id)), false)
    const last = await call("member_stats_games('nfl','historical',null,null,220)")
    assert.equal(last.games.length, 5)
    assert.equal(last.games.at(-1).game_id, '324')
    assert.equal((await call("member_stats_games('nfl','historical',null,null,240)")).games.length, 0)
    assert.equal((await call("member_stats_games('nfl','  HISTORICAL  324  FINISHED ')")).games[0].game_id, '324')
    assert.equal((await call("member_stats_games('nfl','%')")).total, 0)
    assert.equal((await call("member_stats_games('nfl','missing')")).total, 0)
    assert.equal((await call("member_stats_games('nfl','','2026-10-17 12:00Z','2026-10-18 12:00Z')")).total, 2)
    for (const args of ["'bad'", 'null', "'nfl',null", "'nfl',repeat('a',101)",
      "'nfl','',null,null,-1", "'nfl','',null,null,null",
      "'nfl','','2026-10-18Z','2026-10-17Z'", "'nfl','','2026-10-17Z','2026-10-17Z'"]) {
      await assert.rejects(call(`member_stats_games(${args})`))
    }
    for (const competition of ['basketball:12', 'basketball:116', 'baseball:1', 'hockey:57', 'ncaa:2']) {
      const result = await call('member_stats_games($1)', [competition])
      assert.equal(result.total, 1)
      assert.equal(result.games[0].competition, competition)
    }
    const workspace = await call("member_stats_game('nfl','1')")
    assert.deepEqual(Object.keys(workspace).sort(), ['game', 'generated_at', 'history', 'players'])
    assert.equal(workspace.game.game_id, '1')
    assert.equal(workspace.game.home_id, '1')
    assert.equal(workspace.game.away_id, '2')
    assert.equal(workspace.game.home_score, null)
    assert.ok(Number.isFinite(Date.parse(workspace.generated_at)))
    assert.equal(workspace.history.length, 100)
    assert.equal(workspace.history[0].game_id, '324')
    assert.equal(workspace.history.at(-1).game_id, '225')
    assert.deepEqual(new Set(workspace.history.map(g => g.status)), new Set(['FT', 'AOT', 'AP', 'COMPLETED', 'FINISHED']))
    assert.ok(workspace.history.every(g => Date.parse(g.start_at) < Date.parse(workspace.game.start_at)))
    assert.ok(workspace.history.some(g => g.home_id === '1'))
    assert.ok(workspace.history.some(g => g.home_id === '2'))
    assert.equal(workspace.players.length, 10)
    assert.equal(workspace.players[0].game_id, '1')
    assert.equal(workspace.players[1].game_id, '111')
    assert.ok(workspace.players.every(s => Date.parse(s.start_at) <= Date.parse(workspace.game.start_at)))
    assert.deepEqual(Object.keys(workspace.players[0]).sort(),
      ['game_id', 'payload', 'synced_at', 'start_at', 'home_name', 'away_name', 'home_id', 'away_id'].sort())
    assert.equal(workspace.players[0].payload[0].groups[0].players[0].player.id, 42)
    const nba = await call("member_stats_game('basketball:12','10')")
    assert.equal(nba.players[0].payload[0].player.id, '43')
    assert.deepEqual(nba.players[0].payload,
      (await db.query("select payload from public.api_sports_player_game_stats where sport_slug='basketball' and game_id=10")).rows[0].payload)
    assert.deepEqual(nba.history, [])
    await assert.rejects(call("member_stats_game('basketball:116','10')"), /Select a cached game/)
    for (const args of ["'bad','1'", "null,'1'", "'nfl',null", "'nfl','missing'"]) {
      await assert.rejects(call(`member_stats_game(${args})`))
    }
    await access({ state: 'active', kind: 'operator' })
    const operator = await call("operator_research_workspace('nfl','1')")
    assert.deepEqual(workspace.players, operator.players)
    assert.deepEqual((await call("search_research_games('nfl','historical')")), await call("member_stats_games('nfl','historical')"))
    await access({ state: 'active', kind: 'paid' })
    const gameKeys = ['competition', 'sport', 'game_id', 'start_at', 'home_id', 'home_name', 'away_id', 'away_name', 'home_score', 'away_score', 'status', 'synced_at'].sort()
    for (const g of [workspace.game, ...workspace.history, ...first.games]) assert.deepEqual(Object.keys(g).sort(), gameKeys)
    for (const key of ['odds', 'models', 'projection', 'projections', 'injuries', 'refreshes', 'capper', 'performance']) {
      assert.equal(Object.hasOwn(workspace, key), false)
    }

    assert.deepEqual(await save('nfl', 'team', '1'), [{ competition: 'nfl', kind: 'team', entity_id: '1', label: 'Home' }])
    const groupFavorite = await save('nfl', 'player', '42', 'Forged QB')
    assert.equal(groupFavorite.find(f => f.kind === 'player').label, 'Cached QB')
    const flatFavorite = await save('basketball:12', 'player', '43', 'Forged guard')
    assert.equal(flatFavorite.find(f => f.entity_id === '43').label, 'Cached Guard')
    await db.query(`update public.api_sports_player_game_stats set payload = payload || $1::jsonb
      where sport_slug='basketball' and game_id=10`, [JSON.stringify([
      { game: { id: 11 }, team: { id: 1 }, player: { id: 'wrong-game', name: 'Wrong game' } },
      { game: { id: 10 }, team: { id: 999 }, player: { id: 'wrong-team', name: 'Wrong team' } },
      { game: { id: 10 }, team: { id: 1 }, player: { id: 'no-name' } },
      { team: { id: 1 }, groups: { players: [] } },
      { team: { id: 1 }, groups: [{ players: null }, { players: [{ player: { id: 'blank-name', name: ' ' } }] }] },
    ])])
    for (const [competition, kind, id] of [
      ['nfl', 'team', '999999'], ['nfl', 'player', '43'], ['basketball:116', 'player', '43'],
      ['basketball:12', 'player', '44'], ['basketball:8', 'team', '5'], ['nfl', 'player', 'missing'],
      ...['wrong-game', 'wrong-team', 'no-name', 'blank-name'].map(id => ['basketball:12', 'player', id]),
    ]) await assert.rejects(save(competition, kind, id), /cached entity|Unsupported/)
    assert.equal((await save('basketball:12', 'player', '43')).length, 3)
    for (const args of [
      [null, 'team', '1', 'Home', true], ['nfl', null, '1', 'Home', true],
      ['nfl', 'coach', '1', 'Home', false], ['nfl', 'team', null, 'Home', false],
      ['nfl', 'team', ' ', 'Home', false], ['nfl', 'team', '1'.repeat(101), 'Home', true],
      ['nfl', 'team', '1', null, true], ['nfl', 'team', '1', ' ', false],
      ['nfl', 'team', '1', 'a'.repeat(201), true], ['nfl', 'team', '1', 'Home', null],
    ]) await assert.rejects(save(...args), /Invalid favorite arguments|Unsupported/)
    await uid(other)
    assert.deepEqual(await call('member_stats_watchlist()'), [])
    assert.deepEqual(await save('nfl', 'team', '2'), [{ competition: 'nfl', kind: 'team', entity_id: '2', label: 'Away' }])
    await uid(user)
    assert.equal((await call('member_stats_watchlist()')).length, 3)
    assert.equal((await save('nfl', 'team', '2', 'Away', false)).length, 3)
    assert.equal((await save('nfl', 'team', '1', 'Home', false)).length, 2)
    assert.equal((await save('nfl', 'team', 'not-in-cache', 'Deleted', false)).length, 2)
    // Cache eviction must not prevent removing an existing favorite.
    await db.exec("delete from public.api_sports_player_game_stats where sport_slug='nfl' and game_id=1")
    assert.equal((await save('nfl', 'player', '42', 'Cached QB', false)).length, 1)
    for (let id = 1100; id < 1199; id++) await save('nfl', 'team', String(id))
    assert.equal((await call('member_stats_watchlist()')).length, 100)
    assert.equal((await save('nfl', 'team', '1100')).length, 100)
    await assert.rejects(save('nfl', 'team', '1199'), /100 favorites/)
    await assert.rejects(save('basketball:12', 'team', '1'), /100 favorites/)
    assert.equal((await save('nfl', 'team', '1100', 'Remove', false)).length, 99)
    assert.equal((await save('nfl', 'team', '1199')).length, 100)
    const lockDefinition = (await db.query(`select pg_get_functiondef('public.save_member_stats_favorite(text,text,text,text,boolean)'::regprocedure) as definition`)).rows[0].definition
    assert.match(lockDefinition, /pg_advisory_xact_lock.*hashtextextended.*auth\.uid\(\)/)
    await uid(other)
    assert.equal((await call('member_stats_watchlist()')).length, 1)
    assert.equal((await save('nfl', 'team', '1')).length, 2)
    await db.exec(`delete from auth.users where id='${other}'`)
    assert.equal((await db.query('select count(*)::int as count from public.member_stats_watchlist where user_id=$1', [other])).rows[0].count, 0)
    await uid(user)
    await db.exec(`insert into public.api_sports_events
      (sport_slug,event_id,league_id,season,event_name,start_at,home_id,home_name,away_id,away_name,status_code)
      select 'ncaa','placeholder-'||id,'2','2026','Placeholder','2026-10-17 04:00Z','0',null,'0',null,'NS'
      from generate_series(1,25) id;
      insert into public.api_sports_events
      (sport_slug,event_id,league_id,season,event_name,start_at,home_id,home_name,away_id,away_name,status_code)
      select 'ncaa','named-'||id,'2','2026','Real matchup','2026-10-17 18:00Z',id::text,'College '||id,'100','Opponent','NS'
      from generate_series(1,25) id;
      insert into public.api_sports_events
      (sport_slug,event_id,league_id,season,event_name,start_at,home_id,home_name,away_id,away_name,status_code)
      values
        ('ncaa','one-missing','2','2026','Partial','2026-10-17 04:00Z','0',null,'367','Davenport','NS'),
        ('ncaa','blank-name','2','2026','Blank','2026-10-17 04:00Z','1','  ','2','Opponent','NS'),
        ('ncaa','tbd-name','2','2026','TBD','2026-10-17 04:00Z','1','TBD','2','Opponent','NS'),
        ('ncaa','null-id','2','2026','Missing ID','2026-10-17 04:00Z',null,'Named','2','Opponent','NS');`)
    assert.equal((await call("member_stats_games('ncaa:2','','2026-10-17 00:00Z','2026-10-18 00:00Z')")).total, 54)
    await load('20261017100000_research_named_matchups.sql')
    await load('20261029020000_upcoming_season_search.sql')
    const named = await call("member_stats_games('ncaa:2','','2026-10-17 00:00Z','2026-10-18 00:00Z')")
    assert.equal(named.total, 25)
    assert.equal(named.games.length, 20, 'placeholders do not crowd out a page of real matchups')
    assert.ok(named.games.every(game => game.game_id.startsWith('named-')))
    const nextNamed = await call("member_stats_games('ncaa:2','','2026-10-17 00:00Z','2026-10-18 00:00Z',20)")
    assert.equal(nextNamed.games.length, 5)
    assert.equal(nextNamed.total, 25)
    assert.equal(new Set([...named.games, ...nextNamed.games].map(game => game.game_id)).size, 25)
    assert.equal((await call("member_stats_games('ncaa:2','Davenport','2026-10-17 00:00Z','2026-10-18 00:00Z')")).total, 0)
    await assert.rejects(call("member_stats_games('ncaa:2')", [], 'anon'), /permission denied/)
    await access({ state: 'inactive' })
    await assert.rejects(call("member_stats_games('ncaa:2')"), /Active member access required/)
  } finally { await db.close() }
})
