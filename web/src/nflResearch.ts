import type { NflGame } from './nflData'

const completed = new Set(['FT', 'AOT'])
export type TeamResearch = {
  games: number; wins: number; losses: number; ties: number
  pointsFor: number | null; pointsAgainst: number | null
  recent: string; homeRecord: string; awayRecord: string
  daysBetween: number | null; previousKickoff: string | null
}

export function teamResearch(games: NflGame[], matchup: NflGame, teamId: number | null): TeamResearch {
  const history = teamId === null || matchup.stage === null ? [] : games.filter((game) =>
    game.season === matchup.season && game.stage === matchup.stage
    && Date.parse(game.kickoff_at) < Date.parse(matchup.kickoff_at)
    && completed.has(game.status_short) && game.home_score !== null && game.away_score !== null
    && (game.home_team_id === teamId || game.away_team_id === teamId))
    .sort((a, b) => Date.parse(b.kickoff_at) - Date.parse(a.kickoff_at))
  const scores = history.map((game) => {
    const home = game.home_team_id === teamId
    const scored = (home ? game.home_score : game.away_score)!
    const allowed = (home ? game.away_score : game.home_score)!
    return { home, scored, allowed, result: scored > allowed ? 'W' : scored < allowed ? 'L' : 'T' }
  })
  const record = (rows: typeof scores) => `${rows.filter((row) => row.result === 'W').length}-${rows.filter((row) => row.result === 'L').length}-${rows.filter((row) => row.result === 'T').length}`
  return {
    games: scores.length, wins: scores.filter((row) => row.result === 'W').length,
    losses: scores.filter((row) => row.result === 'L').length, ties: scores.filter((row) => row.result === 'T').length,
    pointsFor: scores.length ? scores.reduce((sum, row) => sum + row.scored, 0) / scores.length : null,
    pointsAgainst: scores.length ? scores.reduce((sum, row) => sum + row.allowed, 0) / scores.length : null,
    recent: scores.slice(0, 5).map((row) => row.result).join(' ') || 'No completed games',
    homeRecord: record(scores.filter((row) => row.home)), awayRecord: record(scores.filter((row) => !row.home)),
    previousKickoff: history[0]?.kickoff_at ?? null,
    daysBetween: history[0] ? (Date.parse(matchup.kickoff_at) - Date.parse(history[0].kickoff_at)) / 86_400_000 : null,
  }
}

export function oddsResearch(oddsText: string, stakeText: string, probabilityText: string) {
  if (!/^[+-]?\d+$/.test(oddsText.trim())) throw new Error('Enter whole-number American odds, such as -110 or +150.')
  const odds = Number(oddsText)
  if (!Number.isSafeInteger(odds) || Math.abs(odds) < 100 || Math.abs(odds) > 100000) throw new Error('American odds must be between -100000 and -100 or +100 and +100000.')
  const stake = Number(stakeText)
  if (!stakeText.trim() || !Number.isFinite(stake) || stake <= 0 || stake > 1000000) throw new Error('Enter a stake greater than 0 and no more than 1,000,000.')
  const decimal = odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds)
  let probability: number | null = null
  if (probabilityText.trim()) {
    probability = Number(probabilityText) / 100
    if (!Number.isFinite(probability) || probability < 0 || probability > 1) throw new Error('Your estimated win probability must be between 0% and 100%.')
  }
  return { odds, stake, decimal, implied: 1 / decimal, profit: stake * (decimal - 1),
    probability, expectedValue: probability === null ? null : stake * (probability * decimal - 1) }
}

export function researchCsv(headers: string[], rows: (string | number | null)[][]): string {
  const cell = (value: string | number | null) => {
    let text = value === null ? '' : String(value)
    if (typeof value === 'string' && /^[\s]*[=+\-@]/.test(text)) text = `'${text}`
    return `"${text.replace(/"/g, '""')}"`
  }
  return '\uFEFF' + [headers, ...rows].map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n'
}

export function downloadResearchCsv(filename: string, headers: string[], rows: (string | number | null)[][]) {
  const url = URL.createObjectURL(new Blob([researchCsv(headers, rows)], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function validateResearchGames(games: NflGame[]) {
  for (const game of games) {
    if (!Number.isSafeInteger(game.game_id) || !Number.isInteger(game.season)
      || typeof game.kickoff_at !== 'string' || !Number.isFinite(Date.parse(game.kickoff_at))
      || typeof game.synced_at !== 'string' || !Number.isFinite(Date.parse(game.synced_at))
      || typeof game.status_short !== 'string'
      || typeof game.home_team_name !== 'string' || typeof game.away_team_name !== 'string'
      || (game.stage !== null && typeof game.stage !== 'string')
      || (game.week !== null && typeof game.week !== 'string')
      || [game.venue_name, game.venue_city].some((value) => value !== null && typeof value !== 'string')
      || [game.home_team_id, game.away_team_id].some((id) => id !== null && !Number.isSafeInteger(id))
      || [game.home_score, game.away_score].some((score) => score !== null && (!Number.isInteger(score) || score < 0))) {
      throw new Error('NFL research received an invalid game record.')
    }
  }
  return games
}
