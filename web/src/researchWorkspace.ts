import { supabase } from './supabaseClient'

export type ResearchGame = {
  competition: string; sport: string; game_id: string; start_at: string
  home_id: string | null; home_name: string; away_id: string | null; away_name: string
  home_score: unknown; away_score: unknown; status: string; synced_at: string
}
type CachedResearchGame = Omit<ResearchGame, 'home_name' | 'away_name' | 'status'> & {
  home_name: string | null; away_name: string | null; status: string | null
}
export type RefreshRequest = {
  id: string; game_id: string; kind: string; state: string; result: string | null; requested_at: string
}
export type PlayerLog = { player: string; playerId: string; team: string; game: string; date: string; updated: string; stats: Record<string, number> }
export type BookLine = { book: string; market: string; selection: string; decimal: number }
export type PerformanceRow = { created_at: string; settled_at: string; sport: string; capper: string; market: string; odds: number; units: number; status: string; net_units: number }
export type Workspace = {
  games: ResearchGame[]; history: ResearchGame[]; injuries: Record<string, unknown>[]
  projection: Record<string, unknown> | null; players: PlayerLog[]
  lines: BookLine[]; oddsUpdated: string | null; refreshes: RefreshRequest[]; generated_at: string
}
export const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const text = (value: unknown): string => typeof value === 'string' ? value : ''
export function score(value: unknown): number | null {
  if (object(value)) return score(value.total)
  if (value === null || value === '' || typeof value === 'boolean') return null
  if (typeof value !== 'number' && typeof value !== 'string') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
function game(value: unknown): value is CachedResearchGame {
  return object(value) && ['competition','sport','game_id','start_at','synced_at'].every(key => typeof value[key] === 'string')
    && ['home_id','away_id','home_name','away_name','status'].every(key => value[key] === null || typeof value[key] === 'string')
}
const displayGame = (row: CachedResearchGame): ResearchGame => ({ ...row,
  home_name: row.home_name ?? 'Team not announced', away_name: row.away_name ?? 'Team not announced', status: row.status ?? 'Unknown',
})
export function cachedGames(value: unknown): ResearchGame[] {
  if (!Array.isArray(value) || !value.every(game)) throw new Error('Invalid cached game list.')
  return value.map(displayGame)
}
function numericStats(value: unknown, prefix = ''): Record<string, number> {
  if (object(value)) return Object.assign({}, ...Object.entries(value).map(([key, child]) => numericStats(child, `${prefix} ${key}`.trim())))
  if (Array.isArray(value)) return Object.assign({}, ...value.map(child => {
    if (object(child) && typeof child.name === 'string') return numericStats(child.value, `${prefix} ${child.name}`.trim())
    return {}
  }))
  const numeric = score(value)
  return numeric === null || !prefix ? {} : { [prefix.replaceAll('_', ' ')]: numeric }
}
export function playerLogs(snapshots: unknown): PlayerLog[] {
  if (!Array.isArray(snapshots)) throw new Error('Invalid player snapshot list.')
  const logs: PlayerLog[] = []
  for (const snapshot of array(snapshots)) {
    if (!object(snapshot)) throw new Error('Invalid player snapshot.')
    const add = (entry: Record<string, unknown>, team: string) => {
      const player = entry.player
      if (!object(player) || typeof player.name !== 'string' || (typeof player.id !== 'number' && typeof player.id !== 'string')) throw new Error('Invalid player identity.')
      const values = entry.statistics ?? Object.fromEntries(Object.entries(entry).filter(([key]) => !['game','team','player'].includes(key)))
      const key = `${snapshot.game_id}:${team}:${player.id}`
      const existing = logs.find(log => `${log.game}:${log.team}:${log.playerId}` === key)
      if (existing) Object.assign(existing.stats, numericStats(values))
      else logs.push({ player: player.name, playerId: String(player.id), team, game: String(snapshot.game_id),
        date: text(snapshot.start_at), updated: text(snapshot.synced_at), stats: numericStats(values) })
    }
    for (const entry of array(snapshot.payload)) {
      if (!object(entry)) throw new Error('Invalid player record.')
      if (Array.isArray(entry.groups)) {
        const team = object(entry.team) ? text(entry.team.name) : ''
        if (!team) throw new Error('Player snapshot has no team identity.')
        for (const group of entry.groups) {
          if (!object(group) || !Array.isArray(group.players)) throw new Error('Invalid player statistics group.')
          for (const player of group.players) {
            if (!object(player) || !Array.isArray(player.statistics)) throw new Error('Invalid player statistics record.')
            const statistics = array(player.statistics).map(stat => object(stat) ? { ...stat, name: `${text(group.name)} ${text(stat.name)}`.trim() } : stat)
            add({ ...player, statistics }, team)
          }
        }
      } else if (object(entry.player) && object(entry.team)) {
        if (!object(entry.game) || String(entry.game.id) !== String(snapshot.game_id)) throw new Error('Player statistics belong to a different game.')
        const team = String(entry.team.id) === snapshot.home_id ? text(snapshot.home_name)
          : String(entry.team.id) === snapshot.away_id ? text(snapshot.away_name) : ''
        if (!team) throw new Error('Player team does not match the cached game.')
        add(entry, team)
      } else throw new Error('Unsupported cached player statistics shape.')
    }
  }
  return logs
}
export function bookLines(value: unknown): BookLine[] {
  const lines: BookLine[] = []
  for (const book of array(value)) {
    if (!object(book)) throw new Error('Invalid sportsbook response.')
    for (const market of array(book.bets)) {
      if (!object(market)) throw new Error('Invalid odds market.')
      for (const entry of array(market.values)) {
        if (!object(entry)) throw new Error('Invalid odds selection.')
        const decimal = score(entry.odd)
        if (decimal !== null && decimal > 1) lines.push({
          book: text(book.name), market: text(market.name), selection: String(entry.value), decimal,
        })
      }
    }
  }
  return lines
}
export async function fetchWorkspace(competition: string, gameId: string | null): Promise<Workspace> {
  const { data, error } = await supabase.rpc('operator_research_workspace', { p_competition: competition, p_game_id: gameId })
  if (error) throw error
  const value: unknown = data
  if (!object(value) || !Array.isArray(value.games) || !value.games.every(game)
    || !Array.isArray(value.history) || !value.history.every(game)
    || !Array.isArray(value.injuries) || !value.injuries.every(object)
    || !Array.isArray(value.refreshes) || !value.refreshes.every((row): row is RefreshRequest =>
      object(row) && ['id','game_id','kind','state','requested_at'].every(key => typeof row[key] === 'string')
      && (row.result === null || typeof row.result === 'string'))
    || !(value.projection === null || object(value.projection)) || typeof value.generated_at !== 'string') {
    throw new Error('Research workspace returned invalid data.')
  }
  if (value.odds !== null && (!object(value.odds) || !Array.isArray(value.odds.bookmakers)
    || typeof value.odds.fetched_at !== 'string')) throw new Error('Invalid cached odds response.')
  return { games: value.games.map(displayGame), history: value.history.map(displayGame), injuries: value.injuries, projection: value.projection,
    players: playerLogs(value.players), lines: bookLines(value.odds?.bookmakers),
    oddsUpdated: value.odds ? text(value.odds.fetched_at) : null, refreshes: value.refreshes, generated_at: value.generated_at }
}
export async function searchGames(competition: string, query: string, from: string, through: string, offset: number): Promise<{ games: ResearchGame[]; total: number }> {
  const boundary = (day: string, nextDay = false) => {
    if (!day) return null
    const value = new Date(`${day}T00:00:00`)
    if (nextDay) value.setDate(value.getDate() + 1)
    return value.toISOString()
  }
  const { data, error } = await supabase.rpc('search_research_games', {
    p_competition: competition, p_query: query.trim(), p_start: boundary(from),
    p_end: boundary(through, true), p_offset: offset,
  })
  if (error) throw error
  const value: unknown = data
  if (!object(value) || !Array.isArray(value.games) || !value.games.every(game)
    || typeof value.total !== 'number' || !Number.isSafeInteger(value.total) || value.total < 0) {
    throw new Error('Game search returned invalid data.')
  }
  return { games: value.games.map(displayGame), total: value.total }
}
export async function requestRefresh(competition: string, gameId: string, kind: string): Promise<string> {
  const { data, error } = await supabase.rpc('request_research_refresh', { p_competition: competition, p_game_id: gameId, p_kind: kind })
  if (error) throw error
  if (typeof data !== 'string') throw new Error('Refresh was not acknowledged.')
  return data
}
export async function fetchPerformance(): Promise<PerformanceRow[]> {
  const { data, error } = await supabase.rpc('operator_capper_performance')
  if (error) throw error
  const value: unknown = data
  if (!Array.isArray(value) || !value.every((row): row is PerformanceRow => object(row)
    && ['created_at','settled_at','sport','capper','market','status'].every(key => typeof row[key] === 'string')
    && ['odds','units','net_units'].every(key => typeof row[key] === 'number' && Number.isFinite(row[key])))) {
    throw new Error('Capper performance returned invalid data.')
  }
  return value
}
