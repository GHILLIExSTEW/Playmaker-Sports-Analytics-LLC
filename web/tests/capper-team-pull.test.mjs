import assert from 'node:assert/strict'
import test from 'node:test'
import { pullTeamData, normalizeTeamGames } from '../../supabase/functions/capper-research/pull.ts'

const body = { action: 'pull', competition: 'nfl', game_id: '1', team_id: '2' }
const reservation = { request_id: 'request-1', product: 'american-football', sport: 'nfl', league: '1',
  season: '2026', team_id: '2', competition: 'nfl' }
const game = { game: { id: 1, date: { timestamp: 1792342800 }, status: { short: 'NS', long: 'Not started' } },
  league: { id: 1, season: 2026 }, teams: { home: { id: 1, name: 'Home' }, away: { id: 2, name: 'Away' } }, scores: { home: { total: null }, away: { total: null } } }
function harness({ denied = false, invalid = false, saveFails = false, paging = false } = {}) {
  const calls = [], saves = [], reservations = []
  return { calls, saves, reservations, run: () => pullTeamData(body, async (name, args) => {
    reservations.push({ name, args })
    if (denied) throw { code: 'P0001', message: 'Daily data-pull allowance reached' }
    return reservation
  }, {
    providerKey: 'private-provider-key', log: () => {},
    fetch: async (url, options) => {
      calls.push({ url: String(url), options })
      return Response.json({ errors: invalid ? { limit: 'Exceeded' } : [], paging: { total: paging ? 2 : 1 },
        response: String(url).includes('/injuries') ? [{ player: { id: 3, name: 'Player' }, team: { id: 2 }, status: 'Out', date: '2026-10-17' }] : [game] })
    },
    serviceRpc: async (name, args) => { saves.push({ name, args }); if (saveFails) throw new Error('Database down'); return true },
  }) }
}
test('team pull uses server-scoped team/season, saves both feeds, and does not use AI', async () => {
  const h = harness(), result = await h.run()
  assert.equal(result.status, 200)
  assert.equal(h.calls.length, 2)
  const gamesUrl = new URL(h.calls[0].url)
  assert.equal(gamesUrl.searchParams.get('team'), '2')
  assert.equal(gamesUrl.searchParams.get('season'), '2026')
  assert.equal(gamesUrl.searchParams.get('league'), '1')
  assert.ok(h.calls.every(call => call.options.redirect === 'error'))
  assert.equal(h.saves[0].args.p_games[0].away_team_id, 2)
  assert.equal(h.saves[0].args.p_injuries[0].player_name, 'Player')
  assert.equal(h.saves[0].args.p_failed, false)
  assert.equal(h.reservations[0].name, 'reserve_capper_team_pull')
  assert.match(result.body.message, /Weather, odds and player logs were not refreshed/)
})
test('denied pulls never call provider; errors and multi-page responses never overwrite cache or retry', async () => {
  const denied = harness({ denied: true })
  assert.equal((await denied.run()).status, 429)
  assert.equal(denied.calls.length, 0)
  for (const options of [{ invalid: true }, { paging: true }]) {
    const h = harness(options)
    assert.equal((await h.run()).status, 502)
    assert.equal(h.calls.length, 1)
    assert.equal(h.saves[0].args.p_failed, true)
    assert.deepEqual(h.saves[0].args.p_games, [])
  }
  assert.equal((await harness({ saveFails: true }).run()).status, 503)
})
test('normalization rejects mismatched teams/league/season and dates; other sports retain score objects', () => {
  assert.throws(() => normalizeTeamGames([{ ...game, league: { id: 2, season: 2026 } }], reservation, new Date().toISOString()))
  assert.throws(() => normalizeTeamGames([{ ...game, game: { ...game.game, date: {} } }], reservation, new Date().toISOString()))
  for (const [sport, league, season] of [['basketball', '12', '2026-2027'], ['basketball', '116', '2026-2027'], ['hockey', '57', '2026'], ['baseball', '1', '2026']]) {
    const result = normalizeTeamGames([{ id: 4, date: '2026-10-18T17:00:00Z', status: game.game.status,
      league: { id: Number(league), season }, teams: game.teams, scores: { home: { total: 5 }, away: { total: 2 } } }],
      { ...reservation, sport, competition: `${sport}:${league}`, league, season }, new Date().toISOString())
    assert.deepEqual(result[0].home_score, { total: 5 })
    assert.equal(result[0].sport_slug, sport)
  }
  assert.equal(normalizeTeamGames([game], { ...reservation, sport: 'ncaa', competition: 'ncaa:2', league: '1' }, new Date().toISOString())[0].sport_slug, 'ncaa')
})
