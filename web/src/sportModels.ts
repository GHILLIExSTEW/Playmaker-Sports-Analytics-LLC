import { supabase } from './supabaseClient'
import { fetchNflGames, type NflGame } from './nflData'
import type { ModelEvent } from './modelMath'
export type { MatchupProjection, ModelEvent } from './modelMath'

export type ModelCompetition = {
  key: string
  name: string
  source: 'nfl' | 'events'
  sportSlug: string
  leagueId: string
}

export const modelCompetitions: ModelCompetition[] = [
  { key: 'nfl', name: 'NFL', source: 'nfl', sportSlug: 'nfl', leagueId: '1' },
  { key: 'nba', name: 'NBA', source: 'events', sportSlug: 'basketball', leagueId: '12' },
  { key: 'mlb', name: 'MLB', source: 'events', sportSlug: 'baseball', leagueId: '1' },
  { key: 'nhl', name: 'NHL', source: 'events', sportSlug: 'hockey', leagueId: '57' },
  { key: 'ncaa-football', name: 'NCAA Football', source: 'events', sportSlug: 'ncaa', leagueId: '2' },
  { key: 'ncaa-basketball', name: "NCAA Men's Basketball", source: 'events', sportSlug: 'basketball', leagueId: '8' },
]

type ProviderModelEvent = Omit<ModelEvent, 'home_name' | 'away_name'> & {
  home_name: string | null
  away_name: string | null
}

function validEvent(value: unknown): value is ProviderModelEvent {
  if (!value || typeof value !== 'object') return false
  const event = value as Partial<ModelEvent>
  return typeof event.event_id === 'string'
    && (event.season === null || typeof event.season === 'string')
    && typeof event.start_at === 'string' && Number.isFinite(Date.parse(event.start_at))
    && (event.home_id === null || typeof event.home_id === 'string')
    && (event.home_name === null || typeof event.home_name === 'string')
    && (event.away_id === null || typeof event.away_id === 'string')
    && (event.away_name === null || typeof event.away_name === 'string')
    && typeof event.status_code === 'string'
    && typeof event.synced_at === 'string' && Number.isFinite(Date.parse(event.synced_at))
}

function resolvedEvent(event: ProviderModelEvent): event is ModelEvent {
  return typeof event.home_name === 'string' && event.home_name.trim().length > 0
    && typeof event.away_name === 'string' && event.away_name.trim().length > 0
    && event.home_id !== '0' && event.away_id !== '0'
}

function fromNflGame(game: NflGame): ModelEvent {
  return {
    event_id: String(game.game_id), season: String(game.season), start_at: game.kickoff_at,
    home_id: game.home_team_id === null ? null : String(game.home_team_id),
    home_name: game.home_team_name, home_score: game.home_score,
    away_id: game.away_team_id === null ? null : String(game.away_team_id),
    away_name: game.away_team_name, away_score: game.away_score,
    status_code: game.status_short, synced_at: game.synced_at,
  }
}

export async function fetchModelEvents(competition: ModelCompetition): Promise<{ events: ModelEvent[]; unresolved: number }> {
  if (!supabase) throw new Error('Public Supabase configuration is missing.')
  if (competition.source === 'nfl') {
    const games = await fetchNflGames('public_nfl_model_games')
    return { events: games.map(fromNflGame), unresolved: 0 }
  }

  const events: ModelEvent[] = []
  let unresolved = 0
  for (let offset = 0; ; offset += 1000) {
    const { data, error } = await supabase.rpc('public_sport_model_events', {
      p_sport_slug: competition.sportSlug,
      p_league_id: competition.leagueId,
    }).order('start_at').order('event_id').range(offset, offset + 999)
    if (error) throw error
    if (!Array.isArray(data) || !data.every(validEvent)) throw new Error('Model history response was invalid.')
    const resolved = data.filter(resolvedEvent)
    events.push(...resolved)
    unresolved += data.length - resolved.length
    if (data.length < 1000) return { events, unresolved }
  }
}
