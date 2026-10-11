import type { NflGame } from './nflData'
import type { NflTeamGameEfficiency } from './nflData'

const minimumSample = 3
const maximumSample = 10

export type NflTeamEfficiencySummary = {
  games: number
  offenseEpaPerPlay: number | null
  offenseSuccessRate: number | null
  defenseEpaPerPlayAllowed: number | null
  defenseSuccessRateAllowed: number | null
  lastUpdated: string | null
}

function easternDate(value: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const part = (type: string) => parts.find((item) => item.type === type)?.value
  const year = part('year')
  const month = part('month')
  const day = part('day')
  if (!year || !month || !day) throw new Error('Could not determine the matchup date.')
  return `${year}-${month}-${day}`
}

export function teamEfficiency(
  rows: NflTeamGameEfficiency[],
  matchup: NflGame,
  teamId: number | null,
): NflTeamEfficiencySummary {
  const cutoff = easternDate(matchup.kickoff_at)
  const recent = teamId === null ? [] : rows
    .filter((row) => row.team_id === teamId && row.season <= matchup.season && row.game_date < cutoff)
    .sort((first, second) => second.game_date.localeCompare(first.game_date)
      || second.game_id.localeCompare(first.game_id))
    .slice(0, maximumSample)
  const games = recent.length
  const total = (key: keyof NflTeamGameEfficiency) => recent.reduce(
    (sum, row) => sum + Number(row[key]), 0,
  )
  const offensePlays = total('offense_plays')
  const offenseSuccessPlays = total('offense_success_plays')
  const defensePlays = total('defense_plays')
  const defenseSuccessPlays = total('defense_success_plays')
  const enoughHistory = games >= minimumSample
  const latestSync = recent.map((row) => row.synced_at)
    .sort((first, second) => Date.parse(second) - Date.parse(first))[0] ?? null

  return {
    games,
    offenseEpaPerPlay: enoughHistory && offensePlays > 0 ? total('offense_epa_sum') / offensePlays : null,
    offenseSuccessRate: enoughHistory && offenseSuccessPlays > 0
      ? total('offense_successes') / offenseSuccessPlays : null,
    defenseEpaPerPlayAllowed: enoughHistory && defensePlays > 0
      ? total('defense_epa_sum') / defensePlays : null,
    defenseSuccessRateAllowed: enoughHistory && defenseSuccessPlays > 0
      ? total('defense_successes_allowed') / defenseSuccessPlays : null,
    lastUpdated: latestSync,
  }
}
