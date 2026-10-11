export type ModelEvent = {
  event_id: string
  season: string | null
  start_at: string
  home_id: string | null
  home_name: string
  home_score: unknown
  away_id: string | null
  away_name: string
  away_score: unknown
  status_code: string
  synced_at: string
}

export type MatchupProjection = {
  homeScore: number
  awayScore: number
  homeWinProbability: number
  homeSample: number
  awaySample: number
  leagueSample: number
  lastUpdated: string
}

const finalStatuses = new Set(['FT', 'AOT', 'COMPLETED', 'FINISHED'])
const minimumTeamSample = 3
const minimumLeagueSample = 30

function scoreNumber(value: unknown): number | null {
  if (value && typeof value === 'object' && 'total' in value) {
    return scoreNumber((value as { total: unknown }).total)
  }
  if (typeof value !== 'number' && typeof value !== 'string') return null
  const text = typeof value === 'string' ? value.trim() : String(value)
  if (!/^\d+(?:\.\d+)?$/.test(text)) return null
  const score = Number(text)
  return Number.isFinite(score) && score >= 0 ? score : null
}

function teamKey(id: string | null, name: string): string {
  return id ? `id:${id}` : `name:${name.trim().toLocaleLowerCase('en-US')}`
}

function completedScore(event: ModelEvent): { home: number; away: number } | null {
  if (!finalStatuses.has(event.status_code.toUpperCase())) return null
  const home = scoreNumber(event.home_score)
  const away = scoreNumber(event.away_score)
  return home === null || away === null ? null : { home, away }
}

function average(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function normalCdf(value: number): number {
  const x = Math.abs(value) / Math.SQRT2
  const t = 1 / (1 + 0.3275911 * x)
  const erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x)
  return (1 + Math.sign(value) * erf) / 2
}

export function projectMatchup(events: ModelEvent[], matchup: ModelEvent): MatchupProjection | null {
  const cutoff = Date.parse(matchup.start_at)
  const homeKey = teamKey(matchup.home_id, matchup.home_name)
  const awayKey = teamKey(matchup.away_id, matchup.away_name)
  const previous = events
    .filter((event) => Date.parse(event.start_at) < cutoff)
    .map((event) => ({ event, score: completedScore(event) }))
    .filter((item): item is { event: ModelEvent; score: { home: number; away: number } } => item.score !== null)
    .sort((first, second) => Date.parse(second.event.start_at) - Date.parse(first.event.start_at))

  const teamHistory = (key: string) => previous.flatMap(({ event, score }) => {
    const isHome = teamKey(event.home_id, event.home_name) === key
    if (!isHome && teamKey(event.away_id, event.away_name) !== key) return []
    return [{ scored: isHome ? score.home : score.away, allowed: isHome ? score.away : score.home }]
  }).slice(0, 10)
  const homeHistory = teamHistory(homeKey)
  const awayHistory = teamHistory(awayKey)
  const margins = previous.slice(0, 1000).map(({ score }) => score.home - score.away)
  if (homeHistory.length < minimumTeamSample || awayHistory.length < minimumTeamSample || margins.length < minimumLeagueSample) return null

  const marginAverage = average(margins)
  const marginDeviation = Math.sqrt(average(margins.map((margin) => (margin - marginAverage) ** 2)))
  if (!Number.isFinite(marginDeviation) || marginDeviation === 0) return null
  const homeScore = (average(homeHistory.map((game) => game.scored)) + average(awayHistory.map((game) => game.allowed))) / 2
  const awayScore = (average(awayHistory.map((game) => game.scored)) + average(homeHistory.map((game) => game.allowed))) / 2
  const latestSync = previous
    .filter(({ event }) => {
      const home = teamKey(event.home_id, event.home_name)
      const away = teamKey(event.away_id, event.away_name)
      return home === homeKey || away === homeKey || home === awayKey || away === awayKey
    })
    .map(({ event }) => event.synced_at)
  const lastUpdated = latestSync.sort((first, second) => Date.parse(second) - Date.parse(first))[0]
  if (!lastUpdated) return null

  return {
    homeScore,
    awayScore,
    homeWinProbability: normalCdf((homeScore - awayScore) / marginDeviation),
    homeSample: homeHistory.length,
    awaySample: awayHistory.length,
    leagueSample: margins.length,
    lastUpdated,
  }
}

export function completedModelGameCount(events: ModelEvent[], before: string): number {
  const cutoff = Date.parse(before)
  return events.filter((event) => Date.parse(event.start_at) < cutoff && completedScore(event) !== null).length
}
