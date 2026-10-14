import { expect, test, type Page } from '@playwright/test'
import type { ModelBacktest, ModelProjection } from '../src/sportModels'

const NOW = '2026-10-10T15:00:00Z'

function projection(id: string, start: string, changes: Partial<ModelProjection> = {}): ModelProjection {
  return {
    game_id: id, start_at: start, status_code: 'NS',
    home_id: '1', home_name: 'Team 1', away_id: '0', away_name: 'Team 0',
    home_score: 24.44, away_score: 20.06, home_probability: 0.6423,
    home_sample: 10, away_sample: 9, league_sample: 300,
    market_home_probability: null, market_spread: null, market_total: null, market_books: null,
    market_fetched_at: null, market_edge: null, cover_probability: null, over_probability: null,
    details: { rest_days: [7, 6], travel_km: [0, 1500], margin_adjustments: [['rest', 0.4]] },
    data_updated_at: '2026-10-10T12:00:00Z', generated_at: '2026-10-10T14:30:00Z',
    ...changes,
  }
}

const report: ModelBacktest = {
  status: 'ok', games: 158,
  window: { start: '2025-11-01T18:00:00Z', end: '2026-10-05T20:00:00Z' },
  model: { brier: 0.2246, log_loss: 0.6417, accuracy: 0.586, margin_mae: 10.69, total_mae: 10.1 },
  baseline: { brier: 0.2263, log_loss: 0.6476, accuracy: 0.618, margin_mae: 9.84, total_mae: 10.3 },
  calibration: [{ low: 0.5, high: 0.55, games: 40, predicted: 0.52, actual: 0.5 }],
}

type Routes = { projections?: unknown; backtest?: unknown; projectionStatus?: number; competition?: string }

async function mockSupabase(page: Page, routes: Routes = {}) {
  const requests: string[] = []
  await page.clock.install({ time: new Date(NOW) })
  await page.route('https://lhsevzucmmzetpshpffv.supabase.co/**', async (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
    if (rpc === 'operator_sport_model_projections') {
      const competition = route.request().postDataJSON().p_competition
      requests.push(competition)
      if (routes.projectionStatus) {
        await route.fulfill({ status: routes.projectionStatus, json: { message: 'permission denied' } })
      } else {
        const matches = !routes.competition || routes.competition === competition
        await route.fulfill({ json: matches ? routes.projections ?? [] : [] })
      }
    } else if (rpc === 'operator_sport_model_backtest') {
      const competition = route.request().postDataJSON().p_competition
      const matches = !routes.competition || routes.competition === competition
      await route.fulfill({ json: matches ? routes.backtest ?? [] : [] })
    } else if (rpc === 'website_member_access') {
      await route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    } else {
      await route.fulfill({ json: [] })
    }
  })
  return requests
}

test('Data Models shows stored engine projections, market comparison, inputs and track record', async ({ page }) => {
  const game = projection('99', '2026-10-11T00:15:00Z', {
    market_home_probability: 0.6, market_spread: -3.5, market_total: 44.5, market_books: 7,
    market_fetched_at: '2026-10-10T14:00:00Z', market_edge: 0.0423, cover_probability: 0.531, over_probability: 0.412,
    details: {
      rest_days: [7, 6], travel_km: [0, 1500], margin_adjustments: [['rest', 0.4]],
      weather: { temperature_f: 31, wind_mph: 18, precipitation_mm: 2.5, source: 'forecast' },
      injuries: [{ team_id: '1', player: 'Q. Back', position: 'QB', status: 'Out', points: 3 }],
      injury_points: [3, 0],
    },
  })
  const requests = await mockSupabase(page, { projections: [game], backtest: [{ report }], competition: 'nfl' })
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  await expect(page.getByText('Team 0 at Team 1', { exact: true })).toBeVisible()
  await expect(page.getByText('64.2%')).toBeVisible()
  await expect(page.getByText('Projected total 44.5 · Team 1 by 4.4')).toBeVisible()
  const market = page.getByLabel('Market comparison')
  await expect(market).toContainText('Team 1 -3.5')
  await expect(market).toContainText('Team 1 +4.2 pts')
  await expect(market).toContainText('53.1%')
  const inputs = page.getByLabel('Model inputs')
  await expect(inputs).toContainText('Team 0 travel 1,500 km')
  await expect(inputs).toContainText('31°F, wind 18 mph')
  await expect(inputs).toContainText('Team 1: Q. Back QB Out (−3.0 pts)')
  await expect(page.getByText('158 holdout games', { exact: false })).toBeVisible()
  await expect(page.getByRole('row', { name: /Brier score 0\.225 0\.226/ })).toBeVisible()
  expect([...new Set(requests)]).toEqual(['nfl'])
  await page.getByRole('button', { name: 'NBA', exact: true }).click()
  await expect(page.getByText('No NBA projections are available.')).toBeVisible()
  await expect(page.getByText('No walk-forward backtest has been stored for NBA yet.')).toBeVisible()
  expect([...new Set(requests)]).toEqual(['nfl', 'basketball:12'])
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('Data Models keeps a game available until one hour after its scheduled start', async ({ page }) => {
  const started = projection('98', '2026-10-10T14:30:00Z', { home_name: 'Team past-home', away_name: 'Team past-away' })
  await mockSupabase(page, { projections: [started, projection('99', '2026-10-11T00:15:00Z')] })
  await page.goto('/models')
  const matchup = page.getByRole('option', { name: /Team past-away at Team past-home/ })
  await expect(matchup).toHaveCount(1)
  await page.clock.runFor(30 * 60 * 1000)
  await expect(matchup).toHaveCount(0)
})

test('Data Models without market odds says so instead of inventing a line', async ({ page }) => {
  await mockSupabase(page, { projections: [projection('99', '2026-10-11T00:15:00Z')] })
  await page.goto('/models')
  await expect(page.getByText('Market: no sportsbook consensus is stored for this game yet.')).toBeVisible()
  await expect(page.getByLabel('Market comparison')).toHaveCount(0)
})

test('NFL Lab link appears only on the NFL model tab', async ({ page }) => {
  await mockSupabase(page)
  await page.goto('/models')
  const link = page.getByRole('link', { name: 'Open NFL matchup history and odds research' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', '/nfl/lab')
  for (const name of ['NBA', 'MLB', 'NHL', 'NCAA Football', "NCAA Men's Basketball"]) {
    await page.getByRole('button', { name, exact: true }).click()
    await expect(link).toHaveCount(0)
  }
  await page.getByRole('button', { name: 'NFL', exact: true }).click()
  await expect(link).toBeVisible()
})

test('NCAA basketball requests the men\'s league engine key', async ({ page }) => {
  const requests = await mockSupabase(page)
  await page.goto('/models')
  await page.getByRole('button', { name: "NCAA Men's Basketball", exact: true }).click()
  await expect(page.getByText("No NCAA Men's Basketball projections are available.")).toBeVisible()
  expect(requests).toContain('basketball:116')
})

test('malformed projections and access errors remain explicit errors', async ({ page }) => {
  await mockSupabase(page, { projections: [{ ...projection('1', '2026-10-11T00:15:00Z'), home_probability: 2 }] })
  await page.goto('/models')
  await expect(page.getByRole('alert')).toContainText('Model projection response was invalid')
})

test('a projection RPC failure shows retry without a fallback projection', async ({ page }) => {
  await mockSupabase(page, { projectionStatus: 403 })
  await page.goto('/models')
  await expect(page.getByRole('alert')).toContainText('Model data could not be loaded.')
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(page.getByText('estimated win')).toHaveCount(0)
})
