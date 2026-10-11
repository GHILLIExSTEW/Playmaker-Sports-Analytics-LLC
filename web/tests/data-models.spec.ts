import { expect, test, type Page } from '@playwright/test'
import { completedModelGameCount, projectMatchup, type ModelEvent } from '../src/modelMath'

function modelEvent(id: number, start: string, homeId: string, awayId: string, homeScore: number, awayScore: number): ModelEvent {
  return {
    event_id: String(id), season: '2026', start_at: `${start}T20:00:00Z`,
    home_id: homeId, home_name: `Team ${homeId}`, home_score: { total: homeScore },
    away_id: awayId, away_name: `Team ${awayId}`, away_score: awayScore,
    status_code: 'FT', synced_at: '2026-10-10T12:00:00Z',
  }
}

function history(): ModelEvent[] {
  return Array.from({ length: 30 }, (_, index) => modelEvent(
    index + 1, new Date(Date.UTC(2026, 8, index + 1)).toISOString().slice(0, 10),
    String(index % 6), String((index + 1) % 6), 70 + index % 13, 65 + index % 11,
  ))
}

function nflRow(event: ModelEvent) {
  return {
    game_id: Number(event.event_id), season: Number(event.season), stage: 'Regular Season', week: null,
    kickoff_at: event.start_at, venue_name: null, venue_city: null, status_short: event.status_code,
    status_long: 'Finished', home_team_id: Number(event.home_id), home_team_name: event.home_name,
    home_team_logo: null, home_score: (event.home_score as { total: number }).total,
    away_team_id: Number(event.away_id), away_team_name: event.away_name, away_team_logo: null,
    away_score: Number(event.away_score), synced_at: event.synced_at,
  }
}

async function mockSupabase(page: Page, games: ModelEvent[]) {
  const modelRequests: { sport: string; league: string }[] = []
  await page.clock.install({ time: new Date('2026-10-10T15:00:00Z') })
  await page.route('https://lhsevzucmmzetpshpffv.supabase.co/**', async (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
    if (rpc === 'public_nfl_model_games') {
      await route.fulfill({ json: [...games.map(nflRow), {
        ...nflRow(modelEvent(99, '2026-10-20', '1', '0', 0, 0)),
        status_short: 'NS', home_score: null, away_score: null,
      }] })
    } else if (rpc === 'public_sport_model_events') {
      const body = route.request().postDataJSON()
      modelRequests.push({ sport: body.p_sport_slug, league: body.p_league_id })
      await route.fulfill({ json: [] })
    } else if (rpc === 'website_member_access') {
      await route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    } else {
      await route.fulfill({ json: [] })
    }
  })
  return modelRequests
}

test('score-only projections exclude future games and require enough prior samples', () => {
  const games = history()
  const matchup = {
    ...modelEvent(99, '2026-11-01', '0', '1', 0, 0),
    status_code: 'NS',
  }
  const future = modelEvent(100, '2026-12-01', '0', '1', 999, 0)
  const projection = projectMatchup([...games, future], matchup)
  expect(projection).not.toBeNull()
  expect(projection!.homeSample).toBeGreaterThanOrEqual(3)
  expect(projection!.awaySample).toBeGreaterThanOrEqual(3)
  expect(projection!.leagueSample).toBe(30)
  expect(projection!.homeWinProbability).toBeGreaterThanOrEqual(0)
  expect(projection!.homeWinProbability).toBeLessThanOrEqual(1)
  expect(completedModelGameCount([...games, future], matchup.start_at)).toBe(30)
  expect(projectMatchup(games.slice(0, 20), matchup)).toBeNull()
  // Shared numeric baseline with the Discord /model implementation.
  expect(projection!.homeScore).toBeCloseTo(72.9, 10)
  expect(projection!.awayScore).toBeCloseTo(72.05, 10)
  expect(projection!.homeWinProbability).toBeCloseTo(0.5726246983635468, 10)
})

test('Data Models shows an experimental NFL projection and a transparent insufficient-data state', async ({ page }) => {
  const games = history()
  const modelRequests = await mockSupabase(page, games)
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  await expect(page.getByText('Score-only baseline', { exact: true })).toBeVisible()
  await expect(page.getByText('Team 0 at Team 1', { exact: true })).toBeVisible()
  await expect(page.getByText('Team 1 estimated win')).toBeVisible()
  await expect(page.getByText('Source data last synced:')).toBeVisible()
  await page.getByRole('button', { name: 'NBA', exact: true }).click()
  await expect(page.getByText('No upcoming NBA games are available in the cache.')).toBeVisible()
  expect(modelRequests).toContainEqual({ sport: 'basketball', league: '12' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('Data Models reports missing history rather than manufacturing a probability', async ({ page }) => {
  await mockSupabase(page, history().slice(0, 10))
  await page.goto('/models')
  await expect(page.getByText('Not enough history for a projection yet.')).toBeVisible()
  await expect(page.getByText('10 completed games are available before this matchup.')).toBeVisible()
  await expect(page.getByText('Team 0 estimated win')).toHaveCount(0)
})

test('NCAA placeholder teams are excluded visibly without truncating paginated history', async ({ page }) => {
  await mockSupabase(page, [])
  const offsets: string[] = []
  await page.route('**/rest/v1/rpc/public_sport_model_events**', async (route) => {
    const offset = new URL(route.request().url()).searchParams.get('offset') ?? '0'
    offsets.push(offset)
    const valid = history().map((event) => ({ ...event, home_id: String(Number(event.home_id) + 10), away_id: String(Number(event.away_id) + 10) }))
    const placeholder = {
      ...modelEvent(23506, '2026-09-18', '0', '0', 999, 0),
      home_name: null, away_name: null, status_code: 'NS',
    }
    await route.fulfill({ json: offset === '0'
      ? [...valid, ...Array.from({ length: 970 }, (_, index) => ({ ...placeholder, event_id: String(23506 + index) }))]
      : [{ ...modelEvent(99999, '2026-11-01', '0', '1', 0, 0), home_id: '6', away_id: '7', status_code: 'NS' }] })
  })
  await page.goto('/models')
  await page.getByRole('button', { name: 'NCAA Football', exact: true }).click()
  await expect(page.getByText('970 provider games excluded because their teams are unresolved. They are not used in projections or history samples.')).toBeVisible()
  await expect(page.getByText('Not enough history for a projection yet.')).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect([...new Set(offsets)]).toEqual(['0', '1000'])
})

test('malformed model history remains an explicit error', async ({ page }) => {
  await mockSupabase(page, [])
  await page.route('**/rest/v1/rpc/public_sport_model_events**', (route) => route.fulfill({
    json: [{ ...modelEvent(1, '2026-09-01', '1', '2', 7, 3), start_at: 'invalid' }],
  }))
  await page.goto('/models')
  await page.getByRole('button', { name: 'NCAA Football', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Model history response was invalid')
})
