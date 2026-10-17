import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('Discord research exception preserves identity, age, other users and private helper permissions', async () => {
  const db = new PGlite()
  try {
    await db.exec(`
      create role anon; create role authenticated;
      create schema auth;
      create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
      create table auth.identities (user_id uuid, provider text, provider_id text);
      create table public.member_profiles (user_id uuid, age_verified_at timestamptz);
      create function public.website_member_access() returns jsonb language sql as $$ select current_setting('test.access')::jsonb $$;
      create function public.require_operator_research_access() returns void language plpgsql as $$ begin return; end $$;
      alter default privileges grant execute on functions to anon, authenticated;
      insert into auth.identities values ('00000000-0000-0000-0000-000000000001', 'discord', '1179625165332676632');
      insert into public.member_profiles values ('00000000-0000-0000-0000-000000000001', now());
      select set_config('test.uid','00000000-0000-0000-0000-000000000001',false);
    `)
    await db.exec(await readFile(new URL('../../supabase/migrations/20261017010000_website_research_exception.sql', import.meta.url), 'utf8'))
    const access = async (value) => {
      await db.query("select set_config('test.access',$1,false)", [JSON.stringify(value)])
      return (await db.query('select public.website_member_access() as access')).rows[0].access
    }
    for (const state of ['membership-required', 'launch-pending', 'active']) {
      assert.deepEqual(await access({ state, kind: 'paid' }), { state: 'active', kind: 'research', expires_at: null })
      await db.query('select public.require_operator_research_access()')
    }
    for (const state of ['signed-out', 'age-required', 'discord-required']) {
      assert.deepEqual(await access({ state }), { state })
      await assert.rejects(db.query('select public.require_operator_research_access()'), /approved research exception required/)
    }
    for (const kind of ['owner', 'operator']) {
      const original = { state: 'active', kind, expires_at: null }
      assert.deepEqual(await access(original), original)
    }
    await db.exec("update public.member_profiles set age_verified_at = null")
    assert.equal((await access({ state: 'membership-required' })).state, 'membership-required')
    await db.exec("update public.member_profiles set age_verified_at = now(); update auth.identities set provider = 'email'")
    assert.equal((await access({ state: 'membership-required' })).state, 'membership-required')
    await db.exec("update auth.identities set provider = 'discord', provider_id = '999'")
    assert.equal((await access({ state: 'active', kind: 'paid' })).kind, 'paid')
    await assert.rejects(db.query('select public.require_operator_research_access()'), /approved research exception required/)
    await db.exec("select set_config('test.uid','',false)")
    assert.equal((await access({ state: 'membership-required' })).state, 'membership-required')
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', 'public.website_member_access_before_research_exception()', 'execute') as allowed`)).rows[0].allowed, false)
      assert.equal((await db.query(`select has_function_privilege('${role}', 'public.require_operator_research_access()', 'execute') as allowed`)).rows[0].allowed, false)
      assert.equal((await db.query(`select has_function_privilege('${role}', 'public.website_member_access()', 'execute') as allowed`)).rows[0].allowed, true)
    }
  } finally { await db.close() }
})

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
    await load('20261011230000_operator_user_guide.sql')
    await db.exec(`
      update public.operator_user_guide_content set html = 'Old guide content';
      grant select, update on public.operator_user_guide_content to anon, authenticated;
      grant execute on function public.operator_user_guide() to anon;
    `)
    await load('20261011230000_operator_user_guide.sql')
    assert.equal((await db.query('select count(*)::int as count from public.operator_user_guide_content')).rows[0].count, 1)
    const guideHtml = (await readFile(new URL('../../Playmaker_Picks_User_Guide.html', import.meta.url), 'utf8'))
      .split('<main>')[1].split('</main>')[0]
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
      if (access.kind === 'operator' || access.kind === 'owner') {
        assert.equal((await call('operator_user_guide()'))[0].operator_user_guide, guideHtml)
      } else await assert.rejects(call('operator_user_guide()'), /OPERATOR or Owner/)
    }
    await db.exec("select set_config('test.uid', '', false)")
    for (const rpc of rpcs) {
      await assert.rejects(call(rpc), /OPERATOR or Owner/)
      await assert.rejects(call(rpc, 'anon'), /permission denied/)
      for (const role of ['anon', 'authenticated']) await assert.rejects(call(`internal_${rpc}`, role), /permission denied/)
    }
    await assert.rejects(call('require_operator_research_access()'), /permission denied/)
    await assert.rejects(call('operator_user_guide()'), /OPERATOR or Owner/)
    await assert.rejects(call('operator_user_guide()', 'anon'), /permission denied/)
    assert.equal((await call('public_nfl_games()', 'anon')).length, 1)
    assert.equal((await call("public_sport_events('hockey')", 'anon')).length, 1)
    for (const table of ['api_sports_model_backfill_state', 'nflverse_team_game_efficiency', 'operator_user_guide_content']) {
      for (const role of ['anon', 'authenticated']) {
        await db.exec(`set role ${role}`)
        try { await assert.rejects(db.query(`select * from public.${table}`), /permission denied/) }
        finally { await db.exec('reset role') }
      }
    }
    for (const role of ['anon', 'authenticated']) {
      await db.exec(`set role ${role}`)
      try {
        await assert.rejects(db.query("update public.operator_user_guide_content set html = '<script>bad</script>'"), /permission denied/)
      } finally { await db.exec('reset role') }
    }
  } finally { await db.close() }
})

test('model projection RPCs are operator-only and keep games until one hour after start', async () => {
  const db = new PGlite()
  try {
    const load = async (filename) => db.exec(await readFile(new URL(`../../supabase/migrations/${filename}`, import.meta.url), 'utf8'))
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema auth;
      create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
      create function public.website_member_access() returns jsonb language sql as $$ select current_setting('test.access')::jsonb $$;
      select set_config('test.uid', '00000000-0000-0000-0000-000000000001', false);
      alter default privileges grant execute on functions to anon, authenticated;
    `)
    for (const file of ['20260930260000_api_sports_nfl.sql', '20260930300000_api_sports_multi_event_cache.sql',
      '20261011170000_sports_model_history.sql', '20261011180000_nflverse_team_efficiency.sql',
      '20261011214000_operator_research_access.sql', '20261014120000_sport_model_projections.sql']) await load(file)
    const row = (id, offset) => `('nfl','${id}',now() + interval '${offset}','NS','1','Home','2','Away',24.4,20.1,0.64,'normal',13.5,10,8,8,200,
      null,null,null,null,null,null,null,null,'{}'::jsonb,'ratings-v2',now(),now())`
    await db.exec(`
      insert into public.sport_model_projections values ${row('past', '-2 hours')}, ${row('live', '-30 minutes')},
        ${row('soon', '3 hours')}, ${row('far', '20 days')};
      insert into public.sport_model_backtests values ('nfl','ratings-v2','{}','{"status":"ok","games":158}',now());
      insert into public.api_sports_events (sport_slug,event_id,league_id,season,event_name,start_at)
        values ('basketball','1','116','2026','NCAA game',now()), ('basketball','2','8','2026','Pro B game',now());
    `)
    const call = async (rpc, role = 'authenticated') => {
      await db.exec(`set role ${role}`)
      try { return (await db.query(`select * from public.${rpc}`)).rows }
      finally { await db.exec('reset role') }
    }
    for (const kind of ['paid', 'trial', 'operator', 'owner']) {
      await db.query("select set_config('test.access', $1, false)", [JSON.stringify({ state: 'active', kind })])
      if (kind === 'operator' || kind === 'owner') {
        assert.deepEqual((await call("operator_sport_model_projections('nfl')")).map((item) => item.game_id), ['live', 'soon'])
        assert.equal((await call("operator_sport_model_backtest('nfl')"))[0].report.games, 158)
        assert.deepEqual(await call("operator_sport_model_projections('hockey:57')"), [])
        assert.equal((await call("public_sport_model_events('basketball','116')")).length, 1)
        assert.equal((await call("public_sport_model_events('basketball','8')")).length, 0)
      } else {
        await assert.rejects(call("operator_sport_model_projections('nfl')"), /OPERATOR or Owner/)
        await assert.rejects(call("operator_sport_model_backtest('nfl')"), /OPERATOR or Owner/)
      }
    }
    for (const rpc of ["operator_sport_model_projections('nfl')", "operator_sport_model_backtest('nfl')"]) {
      await assert.rejects(call(rpc, 'anon'), /permission denied/)
    }
    for (const table of ['sport_model_odds', 'sport_model_players', 'sport_model_injuries', 'sport_model_weather',
      'sport_model_projections', 'sport_model_backtests']) {
      for (const role of ['anon', 'authenticated']) {
        await db.exec(`set role ${role}`)
        try { await assert.rejects(db.query(`select * from public.${table}`), /permission denied/) }
        finally { await db.exec('reset role') }
      }
    }
  } finally { await db.close() }
})
