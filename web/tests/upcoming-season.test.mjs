import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('upcoming season filters before pagination for every league and preserves legacy search', async () => {
  const db = new PGlite()
  const competitions = ['nfl', 'basketball:12', 'basketball:116', 'baseball:1', 'hockey:57', 'ncaa:2']
  const load = async name => db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  const search = async (competition, offset = 0, upcoming = true, query = 'vIlLaNoVa') => (
    await db.query('select public.member_stats_games($1,$2,null,null,$3,$4) as data', [competition, query, offset, upcoming])
  ).rows[0].data
  try {
    await db.exec(`
      create role anon; create role authenticated;
      create table public.research_games (
        competition text, game_id text, start_at timestamptz, home_id text, home_name text,
        away_id text, away_name text, status text, season text
      );
      create function public.require_member_stats_access(p_competition text) returns void language plpgsql as $$
      begin
        if current_setting('test.active',true) is distinct from 'yes' then raise exception 'Active member access required'; end if;
        if p_competition not in ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
          then raise exception 'Unsupported research competition'; end if;
      end $$;
      revoke all on function public.require_member_stats_access(text) from public;
      select set_config('test.active','yes',false);
    `)
    await load('20261017100000_research_named_matchups.sql')
    await load('20261029020000_upcoming_season_search.sql')
    for (const competition of competitions) {
      await db.query(`
        insert into research_games
        select $1, id::text, now() + id * interval '20 days',
          '1','Villanova','2','Opponent',
          (array['NS','TBD','SCHEDULED','NOT STARTED'])[1 + id % 4],
          case when id <= 12 then 'current' else 'next' end
        from generate_series(1,24) id
      `, [competition])
      await db.query(`
        insert into research_games
        select $1, 'excluded-' || id, now() + interval '30 days', '1','Villanova','2','Opponent', status,'current'
        from unnest(array['FT','AOT','AP','COMPLETED','FINISHED','LIVE','PST','CANC','SUSP','ABD']) with ordinality as s(status,id);
        `, [competition])
      await db.query(`
        insert into research_games values
          ($1,'past',now()-interval '1 day','1','Villanova','2','Opponent','NS','previous'),
          ($1,'unknown-team',now()+interval '1 day','1','Villanova','0','TBD','NS','current'),
          ($1,'other-team',now()+interval '1 day','3','Other','4','Elsewhere','NS','current')
      `, [competition])
    }
    await db.exec('set role authenticated')
    for (const competition of competitions) {
      const first = await search(competition)
      assert.equal(first.total, 24)
      assert.equal(first.games.length, 20)
      assert.equal(first.games[0].game_id, '1')
      assert.equal(first.games[19].game_id, '20')
      const second = await search(competition, 20)
      assert.equal(second.total, 24)
      assert.deepEqual(second.games.map(game => game.game_id), ['21', '22', '23', '24'])
      assert.ok(second.games.every(game => game.season === 'next' && game.competition === competition))
      assert.equal((await search(competition, 0, true, 'Villanova Opponent')).total, 24)
      assert.equal((await search(competition, 0, true, 'no match')).total, 0)
      assert.equal((await search(competition, 0, false)).total, 35)
      const legacy = (await db.query('select public.member_stats_games($1,$2,null,null,0) as data', [competition, 'Villanova'])).rows[0].data
      assert.equal(legacy.total, 35, 'old callers retain all-date behavior without the new parameter')
    }
    await assert.rejects(search('unsupported'), /Unsupported research competition/)
    await assert.rejects(search('nfl', -1), /Invalid search offset/)
    await assert.rejects(search('nfl', 0, null), /Upcoming filter is required/)
    await db.exec("select set_config('test.active','no',false)")
    await assert.rejects(search('nfl'), /Active member access required/)
    await db.exec('reset role; set role anon')
    await assert.rejects(search('nfl'), /permission denied/)
  } finally { await db.close() }
})
