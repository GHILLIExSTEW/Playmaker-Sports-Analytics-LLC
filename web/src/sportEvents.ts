import { fetchNflGames, type NflGame } from './nflData'
import { supabase } from './supabaseClient'
import type { SportCatalogItem } from './sportsCatalog'
import { hasNamedMatchup } from './eventIdentity'

export type SportEvent = {
  event_id: string
  league_name: string | null
  season: string | null
  round_name: string | null
  event_name: string
  start_at: string
  venue: { name?: string; city?: string; country?: string } | null
  home_name: string | null
  home_logo: string | null
  away_name: string | null
  away_logo: string | null
  home_score: unknown
  away_score: unknown
  status_code: string
  status: string
  synced_at: string
}

export const finalEventStatuses = new Set(['FT', 'AOT', 'CANC', 'ABD', 'WO', 'COMPLETED', 'FINISHED'])

export async function fetchSportEvents(sport: SportCatalogItem): Promise<SportEvent[]> {
  if (sport.feed === 'unavailable') throw new Error(`A schedule feed is not connected for ${sport.name}.`)
  if (sport.feed === 'nfl') return (await fetchNflGames()).map(mapNflGame)
  const events: SportEvent[] = []
  for (let offset = 0; ; offset += 1000) {
    const { data, error } = await supabase.rpc('public_sport_events', { p_sport_slug: sport.slug })
      .order('start_at').order('event_id').range(offset, offset + 999)
    if (error) throw error
    if (!Array.isArray(data)) throw new Error(`Unexpected ${sport.name} events response.`)
    events.push(...(data as SportEvent[]).filter((event) => hasNamedMatchup(event.event_name, event.home_name, event.away_name)))
    if (data.length < 1000) return events
  }
}

export function formatEventScore(value: unknown): string {
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  if (value && typeof value === 'object' && 'total' in value) {
    const total = value.total
    if (typeof total === 'number' || typeof total === 'string') return String(total)
  }
  return '—'
}

function mapNflGame(game: NflGame): SportEvent {
  return {
    event_id: String(game.game_id),
    league_name: 'NFL',
    season: String(game.season),
    round_name: game.week || game.stage,
    event_name: `${game.away_team_name} vs ${game.home_team_name}`,
    start_at: game.kickoff_at,
    venue: { name: game.venue_name || undefined, city: game.venue_city || undefined },
    home_name: game.home_team_name,
    home_logo: game.home_team_logo,
    away_name: game.away_team_name,
    away_logo: game.away_team_logo,
    home_score: game.home_score,
    away_score: game.away_score,
    status_code: game.status_short,
    status: game.status_long,
    synced_at: game.synced_at,
  }
}
