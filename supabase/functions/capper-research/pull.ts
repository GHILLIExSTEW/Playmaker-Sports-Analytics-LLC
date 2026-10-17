type Rpc = (name: string, args: Record<string, unknown>) => Promise<unknown>
type Result = { status: number; body: Record<string, unknown> }
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const record = (value: unknown): Record<string, unknown> => object(value) ? value : {}
const id = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? String(value)
  : typeof value === 'string' && /^[1-9][0-9]{0,14}$/.test(value) ? value : null
const nullableText = (value: unknown) => typeof value === 'string' ? value : null
const requiredText = (value: unknown) => {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Missing provider text')
  return value
}
export function normalizeTeamGames(rows: unknown[], reservation: Record<string, unknown>, at: string) {
  const nfl = reservation.competition === 'nfl', gridiron = nfl || reservation.sport === 'ncaa'
  return rows.map(value => {
    if (!object(value)) throw new Error('Invalid provider game')
    const event = gridiron ? record(value.game) : value, league = record(value.league), teams = record(value.teams)
    const home = record(teams.home), away = record(teams.away), scores = record(value.scores), venue = record(event.venue)
    const status = record(event.status), eventId = id(event.id), homeId = id(home.id), awayId = id(away.id)
    if (!eventId || !homeId || !awayId || ![homeId, awayId].includes(String(reservation.team_id))
      || String(league.id) !== reservation.league || String(league.season ?? value.season) !== reservation.season) throw new Error('Unexpected provider scope')
    const date = gridiron ? record(event.date) : null
    const start = date ? (typeof date.timestamp === 'number' ? new Date(date.timestamp * 1000)
      : new Date(`${date.date}T${date.time ?? '00:00'}Z`)) : new Date(String(event.date))
    if (!Number.isFinite(start.getTime())) throw new Error('Invalid provider date')
    const common = { home_team_name: requiredText(home.name), away_team_name: requiredText(away.name) }
    const score = (value: unknown) => {
      if (value === null || value === undefined) return null
      if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw new Error('Invalid provider score')
      return value
    }
    const short = requiredText(status.short), long = requiredText(status.long)
    if (nfl) return {
      game_id: Number(eventId), league_id: Number(league.id), season: Number(league.season), stage: nullableText(event.stage),
      week: event.week == null ? null : String(event.week), kickoff_at: start.toISOString(),
      venue_name: nullableText(venue.name), venue_city: nullableText(venue.city), status_short: short, status_long: long,
      home_team_id: Number(homeId), home_team_logo: nullableText(home.logo),
      away_team_id: Number(awayId), away_team_logo: nullableText(away.logo), ...common,
      home_score: score(record(scores.home).total), away_score: score(record(scores.away).total), scores, synced_at: at,
    }
    return {
      sport_slug: reservation.sport, event_id: eventId, league_id: reservation.league,
      league_name: nullableText(league.name), season: reservation.season,
      round_name: event.week == null && event.stage == null ? null : String(event.week ?? event.stage),
      event_name: `${common.home_team_name} vs ${common.away_team_name}`, start_at: start.toISOString(), venue,
      home_id: homeId, home_name: common.home_team_name, home_logo: nullableText(home.logo),
      away_id: awayId, away_name: common.away_team_name, away_logo: nullableText(away.logo),
      home_score: scores.home ?? null, away_score: scores.away ?? null, status_code: short, status: long,
      raw_event: value, synced_at: at,
    }
  })
}
export async function pullTeamData(body: Record<string, unknown>, rpc: Rpc, deps: {
  providerKey: string; fetch: typeof fetch; serviceRpc: Rpc; log: (event: string) => void
}): Promise<Result> {
  const fail = (status: number, error: string): Result => ({ status, body: { error } })
  if (Object.keys(body).some(key => !['action','competition','game_id','team_id'].includes(key))
    || typeof body.competition !== 'string' || !['nfl','ncaa:2','basketball:12','basketball:116','baseball:1','hockey:57'].includes(body.competition)
    || !id(body.game_id) || !id(body.team_id)) return fail(400, 'Select a valid matchup and team.')
  if (!deps.providerKey) return fail(503, 'Sports API is not configured. No data pull was made.')
  let reservation: unknown
  try { reservation = await rpc('reserve_capper_team_pull', {
    p_competition: body.competition, p_game_id: body.game_id, p_team_id: body.team_id,
  }) } catch (error) {
    deps.log('team_pull_reservation_failed')
    return fail(object(error) && error.code === 'P0001' ? 429 : object(error) && error.code === '42501' ? 403 : 503,
      object(error) && ['P0001','22023'].includes(String(error.code)) ? String(error.message) : 'Data pull access or allowance could not be verified.')
  }
  const hosts: Record<string, string> = {
    'american-football': 'https://v1.american-football.api-sports.io',
    basketball: 'https://v1.basketball.api-sports.io', baseball: 'https://v1.baseball.api-sports.io', hockey: 'https://v1.hockey.api-sports.io',
  }
  if (!object(reservation) || typeof reservation.request_id !== 'string' || typeof reservation.product !== 'string'
    || !Object.hasOwn(hosts, reservation.product) || reservation.team_id !== body.team_id || reservation.competition !== body.competition
    || typeof reservation.season !== 'string' || !/^\d{4}(-\d{4})?$/.test(reservation.season)
    || !id(reservation.league)) return fail(503, 'Invalid data pull reservation. No provider call made.')
  const requestId = reservation.request_id
  let games: unknown[] = [], injuries: unknown[] | null = null, failed = false
  try {
    const get = async (path: string, params: Record<string, string>) => {
      const url = new URL(path, hosts[String(reservation.product)])
      for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
      const response = await deps.fetch(url, { headers: { 'x-apisports-key': deps.providerKey }, redirect: 'error', signal: AbortSignal.timeout(20_000) })
      if (!response.ok || !response.body) throw new Error('Provider rejected pull')
      const reader = response.body.getReader(), chunks: Uint8Array[] = []
      let size = 0
      try {
        for (;;) {
          const part = await reader.read()
          if (part.done) break
          size += part.value.length
          if (size > 4_194_304) { await reader.cancel(); throw new Error('Provider response too large') }
          chunks.push(part.value)
        }
      } finally { reader.releaseLock() }
      const bytes = new Uint8Array(size)
      let offset = 0
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
      const data: unknown = JSON.parse(new TextDecoder().decode(bytes))
      if (!object(data) || !Array.isArray(data.response) || data.response.length > 1000
        || (!Array.isArray(data.errors) && !object(data.errors)) || Object.keys(data.errors).length
        || (data.paging !== undefined && (!object(data.paging) || typeof data.paging.total !== 'number' || data.paging.total > 1))) {
        throw new Error('Invalid, incomplete or rejected provider response')
      }
      return data.response
    }
    const at = new Date().toISOString()
    games = normalizeTeamGames(await get('/games', { team: String(reservation.team_id), league: String(reservation.league), season: reservation.season }), reservation, at)
    if (body.competition === 'nfl') {
      const rows = await get('/injuries', { team: String(reservation.team_id) })
      const unique = new Map<string, Record<string, unknown>>()
      for (const value of rows) {
        if (!object(value)) throw new Error('Invalid injury')
        const player = record(value.player), playerId = id(player.id), team = record(value.team)
        if (!playerId || String(team.id) !== reservation.team_id) throw new Error('Invalid injury team/player')
        const date = value.date == null ? null : String(value.date).slice(0,10)
        if (date !== null && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('Invalid injury date')
        unique.set(playerId, { competition: 'nfl', team_id: reservation.team_id, player_id: playerId,
          player_name: requiredText(player.name), status: nullableText(value.status), description: nullableText(value.description), reported_on: date, fetched_at: at })
      }
      injuries = [...unique.values()]
    }
  } catch {
    failed = true
    deps.log('team_pull_provider_failed')
  }
  try {
    const saved = await deps.serviceRpc('finish_capper_team_pull', {
      p_request_id: requestId, p_games: failed ? [] : games, p_injuries: failed ? null : injuries, p_failed: failed,
    })
    if (saved !== true) throw new Error('Pull not saved')
  } catch {
    deps.log('team_pull_save_failed')
    return fail(503, 'Provider pull attempted but cache/audit could not be saved. Allowance consumed; do not retry automatically.')
  }
  if (failed) return fail(502, 'Provider pull failed or returned incomplete/invalid data. Cache unchanged; allowance consumed. No automatic retry was made.')
  return { status: 202, body: { request_id: requestId, model_refresh: 'pending', games: games.length, generated_at: new Date().toISOString(),
    message: `Saved ${games.length} team games for season ${reservation.season}.${injuries ? ` Refreshed NFL injury feed (${injuries.length} entries; coverage may be incomplete).` : ' Injury refresh is unavailable for this league.'} Weather, odds and player logs were not refreshed.` } }
}
