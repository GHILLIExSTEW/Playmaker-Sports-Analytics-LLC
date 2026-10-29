import { supabase } from './supabaseClient'
import { cachedGames, object, playerLogs, type ResearchGame, type PlayerLog } from './researchWorkspace'

export type Favorite = { competition: string; kind: 'team' | 'player'; entity_id: string; label: string }
export type GameStats = { game: ResearchGame; history: ResearchGame[]; players: PlayerLog[]; generated_at: string }
export const localDay = (offset = 0) => {
  const day = new Date()
  day.setDate(day.getDate() + offset)
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
}
const boundary = (day: string, next = false) => {
  if (!day) return null
  const value = new Date(`${day}T00:00:00`)
  if (next) value.setDate(value.getDate() + 1)
  return value.toISOString()
}
export async function findMemberGames(competition: string, query: string, from: string, through: string, offset: number, upcomingOnly = false) {
  const { data, error } = await supabase.rpc('member_stats_games', {
    p_competition: competition, p_query: query.trim(), p_start: boundary(from),
    p_end: boundary(through, true), p_offset: offset,
    ...(upcomingOnly ? { p_upcoming_only: true } : {}),
  })
  if (error) throw error
  const value: unknown = data
  if (!object(value) || typeof value.total !== 'number' || !Number.isSafeInteger(value.total) || value.total < 0) {
    throw new Error('Invalid member game search response.')
  }
  return { games: cachedGames(value.games), total: value.total }
}
export async function fetchMemberGame(competition: string, gameId: string): Promise<GameStats> {
  const { data, error } = await supabase.rpc('member_stats_game', { p_competition: competition, p_game_id: gameId })
  if (error) throw error
  const value: unknown = data
  if (!object(value) || typeof value.generated_at !== 'string') throw new Error('Invalid member game statistics.')
  return { game: cachedGames([value.game])[0], history: cachedGames(value.history), players: playerLogs(value.players), generated_at: value.generated_at }
}
function favorites(value: unknown): Favorite[] {
  if (!Array.isArray(value) || !value.every((row): row is Favorite => object(row)
    && typeof row.competition === 'string' && (row.kind === 'team' || row.kind === 'player')
    && typeof row.entity_id === 'string' && typeof row.label === 'string')) throw new Error('Invalid watchlist response.')
  return value
}
export async function fetchFavorites(): Promise<Favorite[]> {
  const { data, error } = await supabase.rpc('member_stats_watchlist')
  if (error) throw error
  return favorites(data)
}
export async function saveFavorite(item: Favorite, saved: boolean): Promise<Favorite[]> {
  const { data, error } = await supabase.rpc('save_member_stats_favorite', {
    p_competition: item.competition, p_kind: item.kind, p_entity_id: item.entity_id, p_label: item.label, p_saved: saved,
  })
  if (error) throw error
  return favorites(data)
}
