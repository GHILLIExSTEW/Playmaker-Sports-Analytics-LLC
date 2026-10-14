import { supabase } from './supabaseClient'

export type ModelCompetition = {
  key: string
  name: string
  engineKey: string
}

// engineKey matches the Discord bot's competition keys used when storing projections.
export const modelCompetitions: ModelCompetition[] = [
  { key: 'nfl', name: 'NFL', engineKey: 'nfl' },
  { key: 'nba', name: 'NBA', engineKey: 'basketball:12' },
  { key: 'mlb', name: 'MLB', engineKey: 'baseball:1' },
  { key: 'nhl', name: 'NHL', engineKey: 'hockey:57' },
  { key: 'ncaa-football', name: 'NCAA Football', engineKey: 'ncaa:2' },
  { key: 'ncaa-basketball', name: "NCAA Men's Basketball", engineKey: 'basketball:116' },
]

export type ModelInjury = { team_id: string; player: string; position: string | null; status: string; points: number }
export type ModelWeather = { temperature_f: number; wind_mph: number; precipitation_mm: number; source: string }

export type ModelProjection = {
  game_id: string
  start_at: string
  status_code: string
  home_id: string | null
  home_name: string
  away_id: string | null
  away_name: string
  home_score: number
  away_score: number
  home_probability: number
  home_sample: number
  away_sample: number
  league_sample: number
  market_home_probability: number | null
  market_spread: number | null
  market_total: number | null
  market_books: number | null
  market_fetched_at: string | null
  market_edge: number | null
  cover_probability: number | null
  over_probability: number | null
  details: {
    margin_adjustments?: [string, number][]
    total_adjustments?: [string, number][]
    rest_days?: [number | null, number | null]
    travel_km?: [number | null, number | null]
    weather?: ModelWeather | null
    injuries?: ModelInjury[]
    injury_points?: [number, number]
  }
  data_updated_at: string
  generated_at: string
}

export type ModelMetrics = { brier?: number; log_loss?: number; accuracy?: number | null; margin_mae?: number; total_mae?: number }
export type ModelBacktest = {
  status: string
  games: number
  window?: { start: string; end: string }
  model?: ModelMetrics
  baseline?: ModelMetrics
  calibration?: { low: number; high: number; games: number; predicted: number; actual: number }[]
}

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isNullableNumber = (value: unknown) => value === null || isNumber(value)
const isDate = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value))

function validProjection(value: unknown): value is ModelProjection {
  if (!value || typeof value !== 'object') return false
  const row = value as Record<string, unknown>
  return typeof row.game_id === 'string' && isDate(row.start_at)
    && typeof row.home_name === 'string' && row.home_name.trim().length > 0
    && typeof row.away_name === 'string' && row.away_name.trim().length > 0
    && isNumber(row.home_score) && isNumber(row.away_score)
    && isNumber(row.home_probability) && row.home_probability >= 0 && row.home_probability <= 1
    && isNumber(row.home_sample) && isNumber(row.away_sample) && isNumber(row.league_sample)
    && ['market_home_probability', 'market_spread', 'market_total', 'market_books', 'market_edge',
      'cover_probability', 'over_probability'].every((key) => isNullableNumber(row[key] ?? null))
    && typeof row.details === 'object' && row.details !== null
    && isDate(row.data_updated_at) && isDate(row.generated_at)
}

export async function fetchModelProjections(competition: ModelCompetition): Promise<ModelProjection[]> {
  if (!supabase) throw new Error('Public Supabase configuration is missing.')
  const { data, error } = await supabase.rpc('operator_sport_model_projections', { p_competition: competition.engineKey })
  if (error) throw error
  if (!Array.isArray(data) || !data.every(validProjection)) throw new Error('Model projection response was invalid.')
  return data
}

export async function fetchModelBacktest(competition: ModelCompetition): Promise<ModelBacktest | null> {
  if (!supabase) throw new Error('Public Supabase configuration is missing.')
  const { data, error } = await supabase.rpc('operator_sport_model_backtest', { p_competition: competition.engineKey })
  if (error) throw error
  const report = Array.isArray(data) ? (data[0] as { report?: ModelBacktest } | undefined)?.report : undefined
  return report && typeof report === 'object' && report.status === 'ok' && isNumber(report.games) ? report : null
}
