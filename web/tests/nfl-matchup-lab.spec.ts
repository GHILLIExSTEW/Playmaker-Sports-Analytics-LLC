import { expect, test, type Page } from '@playwright/test'
import { oddsResearch, researchCsv, teamResearch } from '../src/nflResearch'
import { teamEfficiency } from '../src/nflEfficiency'
import type { NflGame, NflTeamGameEfficiency } from '../src/nflData'

function game(id: number, date: string, home: number, away: number, homeScore: number | null, awayScore: number | null, status = 'FT'): NflGame {
  return { game_id: id, season: 2026, stage: 'Regular Season', week: `Week ${id}`, kickoff_at: `${date}T20:00:00Z`,
    venue_name: null, venue_city: null, status_short: status, status_long: status,
    home_team_id: home, home_team_name: `Team ${home}`, home_team_logo: null, home_score: homeScore,
    away_team_id: away, away_team_name: `Team ${away}`, away_team_logo: null, away_score: awayScore,
    scores: {}, synced_at: '2026-10-01T01:00:00Z' }
}
const dataset = [
  game(1, '2026-09-01', 1, 3, 21, 14),
  game(2, '2026-09-08', 4, 1, 20, 10),
  game(3, '2026-09-15', 2, 5, 17, 17),
  game(4, '2026-10-08', 1, 2, null, null, 'NS'),
  game(5, '2026-10-15', 1, 6, 99, 0),
  game(6, '2026-09-22', 1, 7, 99, 0, 'CANC'),
  { ...game(7, '2026-08-01', 1, 8, 99, 0), stage: 'Preseason' },
  { ...game(8, '2025-09-01', 1, 8, 99, 0), season: 2025 },
]
function efficiencyGame(teamId: number, gameId: string, date: string, offenseEpa: number): NflTeamGameEfficiency {
  return {
    team_id: teamId, season: 2026, game_id: gameId, game_date: date,
    offense_epa_sum: offenseEpa, offense_plays: 10, offense_successes: 5, offense_success_plays: 10,
    defense_epa_sum: -2, defense_plays: 10, defense_successes_allowed: 2, defense_success_plays: 10,
    synced_at: '2026-10-09T06:00:00Z',
  }
}
const efficiencyRows = [
  efficiencyGame(1, 'game-1', '2026-09-01', 1),
  efficiencyGame(1, 'game-2', '2026-09-08', 2),
  efficiencyGame(1, 'game-3', '2026-10-07', 6),
  efficiencyGame(1, 'same-day', '2026-10-08', 100),
  efficiencyGame(1, 'future', '2026-10-15', 100),
  efficiencyGame(2, 'game-1', '2026-09-01', -1),
  efficiencyGame(2, 'game-2', '2026-09-08', 0),
  efficiencyGame(2, 'game-3', '2026-10-07', 1),
]
async function mock(page: Page, fail = false) {
  await page.route('https://lhsevzucmmzetpshpffv.supabase.co/**', async (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
    if (rpc === 'public_nfl_games') await route.fulfill({
      status: fail ? 503 : 200, json: fail ? { message: 'Cache unavailable' } : dataset,
    })
    else if (rpc === 'public_nfl_team_efficiency') await route.fulfill({ json: efficiencyRows })
    else if (rpc === 'website_member_access') await route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    else await route.fulfill({ json: [] })
  })
}

test('team efficiency aggregates weighted prior games and excludes the matchup date', () => {
  const summary = teamEfficiency(efficiencyRows, dataset[3], 1)
  expect(summary.games).toBe(3)
  expect(summary.offenseEpaPerPlay).toBeCloseTo(0.3, 12)
  expect(summary.offenseSuccessRate).toBeCloseTo(0.5, 12)
  expect(summary.defenseEpaPerPlayAllowed).toBeCloseTo(-0.2, 12)
  expect(summary.defenseSuccessRateAllowed).toBeCloseTo(0.2, 12)
  expect(teamEfficiency(efficiencyRows.slice(0, 2), dataset[3], 1).offenseEpaPerPlay).toBeNull()
  expect(teamEfficiency(efficiencyRows, dataset[3], null).games).toBe(0)
})

test('research excludes future, canceled, different season and preseason games', () => {
  const stats = teamResearch(dataset, dataset[3], 1)
  expect(stats.games).toBe(2)
  expect(stats.pointsFor).toBe(15.5)
  expect(stats.pointsAgainst).toBe(17)
  expect(stats.recent).toBe('L W')
  expect(stats.homeRecord).toBe('1-0-0')
  expect(stats.awayRecord).toBe('0-1-0')
  expect(stats.daysBetween).toBe(30)
  expect(teamResearch(dataset, dataset[3], null).pointsFor).toBeNull()
  expect(teamResearch(dataset, { ...dataset[3], stage: null }, 1).games).toBe(0)
  expect(teamResearch(dataset, dataset[3], 2).ties).toBe(1)
})

test('odds math verifies exact break-even, profit and user-estimated EV', () => {
  const minus = oddsResearch('-110', '100', '55')
  expect(minus.implied).toBeCloseTo(110 / 210, 12)
  expect(minus.profit).toBeCloseTo(10000 / 110, 12)
  expect(minus.expectedValue).toBeCloseTo(5, 12)
  const plus = oddsResearch('+150', '10', '40')
  expect(plus.decimal).toBe(2.5)
  expect(plus.expectedValue).toBe(0)
  expect(oddsResearch('+100', '1', '').probability).toBeNull()
  for (const [odds, stake, probability] of [['0','1',''],['-99','1',''],['110.5','1',''],['+150','0',''],['+150','1','101'],['+150','1','-1'],['+150','','']]) {
    expect(() => oddsResearch(odds, stake, probability)).toThrow()
  }
})

test('CSV escapes quotes, line breaks and spreadsheet formula strings', () => {
  const csv = researchCsv(['name','value'], [['=HYPERLINK("bad")', -110], ['Team,\nName', null]])
  expect(csv).toContain(`"'=HYPERLINK(""bad"")","-110"`)
  expect(csv).toContain('"Team,\nName",""')
})

test('matchup lab displays samples and downloads game and matchup data', async ({ page }) => {
  await mock(page)
  await page.goto('/nfl/lab')
  await expect(page.getByRole('heading', { name: 'NFL matchup lab.' })).toBeVisible()
  await expect(page.getByLabel('Choose matchup')).toHaveValue('4')
  const home = page.locator('.nfl-lab-team').filter({ has: page.getByRole('heading', { name: 'Team 1', exact: true }) })
  await expect(home.getByText('15.5', { exact: true })).toBeVisible()
  await expect(home.getByText('L W', { exact: true })).toBeVisible()
  await expect(home.getByText('0.300', { exact: true })).toBeVisible()
  await expect(home.getByText('50.0%', { exact: true })).toBeVisible()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export matchup CSV', exact: true }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('nfl-matchup-4.csv')
  const stream = await download.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk))
  const csv = Buffer.concat(chunks).toString('utf8')
  expect(csv).toContain('"Team 1","home","2","1","1","0"')
  expect(csv).toContain('"15.5","17"')
  await expect(page.getByText('Implied probability / break-even:', { exact: false })).toContainText('52.38%')
  await page.getByLabel('Your estimated win probability (%) — optional').fill('55')
  await expect(page.getByText('Expected profit per bet using YOUR estimate:', { exact: false })).toContainText('5.00')
  await page.getByLabel('American odds').fill('0')
  await expect(page.getByRole('alert')).toContainText('American odds must be')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await expect(home).toBeInViewport()
})

test('calculator inputs survive the periodic research access check', async ({ page }) => {
  await mock(page)
  await page.goto('/nfl/lab')
  await page.getByLabel('American odds').fill('+150')
  await page.getByLabel('Your estimated win probability (%) — optional').fill('61')
  await page.clock.fastForward(30_000)
  await expect(page.getByLabel('Choose matchup')).toHaveValue('4')
  await expect(page.getByLabel('American odds')).toHaveValue('+150')
  await expect(page.getByLabel('Your estimated win probability (%) — optional')).toHaveValue('61')
})

test('empty and malformed research caches never manufacture matchup stats', async ({ page }) => {
  await mock(page)
  await page.route('**/rest/v1/rpc/public_nfl_games*', (route) => route.fulfill({ json: [] }))
  await page.goto('/nfl/lab')
  await expect(page.getByText('No eligible NFL matchups have synced yet.')).toBeVisible()
  await page.unroute('**/rest/v1/rpc/public_nfl_games*')
  await page.route('**/rest/v1/rpc/public_nfl_games*', (route) => route.fulfill({ json: [{ ...dataset[3], home_score: -1 }] }))
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('invalid game record')
  await expect(page.getByLabel('Choose matchup')).toHaveCount(0)
})

test('all game pages are loaded before matchup research and export', async ({ page }) => {
  await mock(page)
  const pages: string[] = []
  await page.route('**/rest/v1/rpc/public_nfl_games*', async (route) => {
    const url = new URL(route.request().url())
    const offset = url.searchParams.get('offset') ?? '0'
    pages.push(offset)
    const olderGames = Array.from({ length: 1000 }, (_, i) => game(i + 100, '2026-09-01', 1, 3, 21, 14))
    await route.fulfill({ json: offset === '0' ? olderGames : [dataset[3]] })
  })
  await page.goto('/nfl/lab')
  await expect(page.getByLabel('Choose matchup')).toHaveValue('4')
  expect([...new Set(pages)]).toEqual(['0', '1000'])
  const home = page.locator('.nfl-lab-team').filter({ has: page.getByRole('heading', { name: 'Team 1', exact: true }) })
  await expect(home.getByText('1000', { exact: true })).toBeVisible()
})

test('NFL sports surface links into research', async ({ page }) => {
  await mock(page)
  await page.goto('/sports/american-football')
  await page.getByRole('link', { name: 'Open NFL matchup lab & odds calculator' }).click()
  await expect(page.getByRole('heading', { name: 'NFL matchup lab.' })).toBeVisible()
  await page.getByRole('link', { name: 'NFL scores & schedule' }).click()
  await page.getByRole('link', { name: 'NFL matchup lab & odds calculator' }).click()
  await expect(page.getByLabel('Choose matchup')).toBeVisible()
})

test('cache failure is explicit and calculator remains usable', async ({ page }) => {
  await mock(page, true)
  await page.goto('/nfl/lab')
  await expect(page.getByRole('alert')).toContainText('Cache unavailable')
  await expect(page.getByText('Implied probability / break-even:', { exact: false })).toContainText('52.38%')
  await expect(page.getByRole('button', { name: 'Export matchup CSV' })).toHaveCount(0)
  await page.unroute('https://lhsevzucmmzetpshpffv.supabase.co/**')
  await mock(page)
  await page.getByRole('button', { name: 'Retry research' }).click()
  await expect(page.getByLabel('Choose matchup')).toBeVisible()
})
