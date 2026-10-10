import { supabase } from './supabaseClient'
import { hasNamedMatchup } from './eventIdentity'

export type NflGame = {
  game_id: number
  season: number
  stage: string | null
  week: string | null
  kickoff_at: string
  venue_name: string | null
  venue_city: string | null
  status_short: string
  status_long: string
  home_team_id: number | null
  home_team_name: string
  home_team_logo: string | null
  home_score: number | null
  away_team_id: number | null
  away_team_name: string
  away_team_logo: string | null
  away_score: number | null
  scores: {
    home?: { quarter_1?: number | null; quarter_2?: number | null; quarter_3?: number | null; quarter_4?: number | null; overtime?: number | null; total?: number | null }
    away?: { quarter_1?: number | null; quarter_2?: number | null; quarter_3?: number | null; quarter_4?: number | null; overtime?: number | null; total?: number | null }
  }
  synced_at: string
}

export type NflStanding = {
  season: number
  team_id: number
  team_name: string
  team_logo: string | null
  conference: string | null
  division: string | null
  standing_position: number
  wins: number
  losses: number
  ties: number
  points_for: number
  points_against: number
  point_difference: number
  streak: string | null
  synced_at: string
}

export type NflSyncStatus = { sync_key: string; last_success_at: string | null; success: boolean }

async function fetchRpc<T>(name: string, orderColumns: string[] = []): Promise<T[]> {
  const rows: T[] = []
  for (let offset = 0; ; offset += 1000) {
    let query = supabase.rpc(name)
    for (const column of orderColumns) query = query.order(column)
    const { data, error } = await query.range(offset, offset + 999)
    if (error) throw error
    if (!Array.isArray(data)) throw new Error(`Unexpected ${name} response.`)
    rows.push(...data as T[])
    if (data.length < 1000) return rows
  }
}

export async function fetchNflGames(): Promise<NflGame[]> {
  return (await fetchRpc<NflGame>('public_nfl_games', ['kickoff_at', 'game_id']))
    .filter((game) => hasNamedMatchup(`${game.away_team_name} vs ${game.home_team_name}`, game.home_team_name, game.away_team_name))
}

export async function fetchNflStandings(): Promise<NflStanding[]> {
  return fetchRpc<NflStanding>('public_nfl_standings')
}

export async function fetchNflSyncStatus(): Promise<NflSyncStatus[]> {
  return fetchRpc<NflSyncStatus>('public_nfl_data_status')
}