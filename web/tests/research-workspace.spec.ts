import { expect, test } from '@playwright/test'

const game = {
  competition: 'nfl', sport: 'nfl', game_id: '1', start_at: '2099-10-20T20:00:00Z',
  home_id: '1', home_name: 'Home', away_id: '2', away_name: 'Away',
  home_score: null, away_score: null, status: 'NS', synced_at: '2026-10-17T20:00:00Z',
}
const snapshot = {
  game_id: '2', start_at: '2026-10-16T20:00:00Z', synced_at: '2026-10-17T20:00:00Z',
  home_id: '1', home_name: 'Home', away_id: '2', away_name: 'Away',
  payload: [{ team: { id: 1, name: 'Home' }, groups: [{ name: 'Passing', players: [
    { player: { id: 4, name: 'Player One' }, statistics: [{ name: 'yards', value: '250' }, { name: 'touchdowns', value: null }] },
  ] }] }],
}
const workspace = {
  games: [game], history: [{ ...game, game_id: '2', start_at: snapshot.start_at, status: 'FT', home_score: 24, away_score: 10 }],
  injuries: [], projection: null, players: [snapshot], odds: {
    bookmakers: [{ name: 'Book A', bets: [{ name: 'Moneyline', values: [{ value: 'Home', odd: '2.00' }] }] }],
    fetched_at: '2026-10-17T20:00:00Z',
  }, refreshes: [], generated_at: '2026-10-17T20:00:00Z',
}

for (const kind of ['paid','trial','signed-out']) {
  test(`workspace denies ${kind} without fetching private data`, async ({ page }) => {
    const requests: string[] = []
    await page.route('**/rest/v1/rpc/**', route => {
      const rpc = new URL(route.request().url()).pathname.split('/').at(-1) ?? ''
      if (rpc === 'website_member_access') return route.fulfill({ json: kind === 'signed-out' ? { state: kind } : { state: 'active', kind, expires_at: null } })
      if (rpc.includes('research') || rpc.includes('performance')) requests.push(rpc)
      return route.fulfill({ json: [] })
    })
    await page.goto('/research')
    await expect(page.getByRole('heading', { name: 'Team research access required.' })).toBeVisible()
    expect(requests).toEqual([])
  })
}

test('approved research sees matchup, player thresholds, odds and play-level ROI', async ({ page }) => {
  await page.route('**/rest/v1/rpc/**', route => {
    const rpc = route.request().url().split('/').at(-1)
    if (rpc === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'research', expires_at: null } })
    if (rpc === 'operator_research_workspace') return route.fulfill({ json: workspace })
    if (rpc === 'operator_capper_performance') return route.fulfill({ json: [
      { capper: 'Capper', sport: 'NFL', market: 'Parlay', created_at: new Date().toISOString(), settled_at: new Date().toISOString(), odds: 150, units: 2, status: 'win', net_units: 3 },
      { capper: 'Capper', sport: 'NFL', market: 'moneyline', created_at: new Date().toISOString(), settled_at: new Date().toISOString(), odds: -110, units: 1, status: 'loss', net_units: -1 },
      { capper: 'Capper', sport: 'NFL', market: 'Unclassified', created_at: new Date().toISOString(), settled_at: new Date().toISOString(), odds: 100, units: 5, status: 'void', net_units: 0 },
    ] })
    return route.fulfill({ json: [] })
  })
  await page.goto('/research')
  await page.getByLabel('Game', { exact: true }).selectOption('1')
  await expect(page.getByRole('heading', { name: 'Home', exact: true })).toBeVisible()
  await expect(page.getByText('Scored 24.0 / allowed 10.0 per game')).toBeVisible()
  await page.getByRole('tab', { name: 'Players', exact: true }).click()
  await page.getByLabel('Player', { exact: true }).selectOption('4:Home')
  await page.getByLabel('Statistic', { exact: true }).selectOption('Passing yards')
  await page.getByLabel('Prop threshold').fill('249.5')
  await expect(page.getByText(/1 observations \/ average 250.0 \/ Over 1, Under 0, Push 0/)).toBeVisible()
  await expect(page.getByLabel('Statistic', { exact: true }).getByRole('option', { name: 'Passing touchdowns' })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Odds', exact: true }).click()
  await expect(page.getByRole('cell', { name: '50.0%', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Performance', exact: true }).click()
  await expect(page.getByText('3 bets / 3.00u stake / 2.00u net / 66.7% ROI')).toBeVisible()
  await page.getByLabel('Market', { exact: true }).selectOption('Parlay')
  await expect(page.getByText('1 bets / 2.00u stake / 3.00u net / 150.0% ROI')).toBeVisible()
})

test('refresh errors and empty caches are explicit', async ({ page }) => {
  let empty = false
  await page.route('**/rest/v1/rpc/**', route => {
    const rpc = route.request().url().split('/').at(-1)
    if (rpc === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    if (rpc === 'request_research_refresh') return route.fulfill({ status: 400, json: { message: 'Shared daily allowance exhausted' } })
    if (rpc === 'operator_research_workspace') return route.fulfill({ json: { ...workspace, games: empty ? [] : workspace.games } })
    return route.fulfill({ json: [] })
  })
  await page.goto('/research')
  await page.getByLabel('Game', { exact: true }).selectOption('1')
  await page.getByRole('button', { name: 'Request live game refresh' }).click()
  await expect(page.getByRole('alert')).toContainText('Shared daily allowance exhausted')
  empty = true
  await page.getByLabel('League', { exact: true }).selectOption('hockey:57')
  await expect(page.getByText(/No cached games in this league/)).toBeVisible()
})

test('out-of-order responses do not replace the newly selected league', async ({ page }) => {
  let release: () => void = () => {}
  await page.route('**/rest/v1/rpc/**', async route => {
    const rpc = route.request().url().split('/').at(-1)
    if (rpc === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'owner', expires_at: null } })
    if (rpc === 'operator_research_workspace') {
      const body = route.request().postDataJSON()
      if (body.p_competition === 'nfl') {
        await new Promise<void>(resolve => { release = resolve })
        return route.fulfill({ json: workspace })
      }
      return route.fulfill({ json: { ...workspace, games: [] } })
    }
    return route.fulfill({ json: [] })
  })
  await page.goto('/research')
  await page.getByLabel('League', { exact: true }).selectOption('baseball:1')
  await expect(page.getByText(/No cached games in this league/)).toBeVisible()
  release()
  await expect(page.getByLabel('Game', { exact: true }).getByRole('option')).toHaveCount(1)
})

test('queued refresh is polled from cache and completion does not resubmit', async ({ page }) => {
  let submitted = 0, snapshots = 0
  await page.route('**/rest/v1/rpc/**', route => {
    const rpc = route.request().url().split('/').at(-1)
    if (rpc === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    if (rpc === 'request_research_refresh') { submitted++; return route.fulfill({ json: 'request-1' }) }
    if (rpc === 'operator_research_workspace') {
      if (submitted) snapshots++
      return route.fulfill({ json: { ...workspace, refreshes: submitted ? [{
        id: 'request-1', game_id: '1', kind: 'game', state: snapshots > 1 ? 'completed' : 'queued',
        result: snapshots > 1 ? 'Game refreshed' : null, requested_at: '2026-10-17T20:00:00Z',
      }] : [] } })
    }
    return route.fulfill({ json: [] })
  })
  await page.goto('/research')
  await page.getByLabel('Game', { exact: true }).selectOption('1')
  await page.getByRole('button', { name: 'Request live game refresh' }).click()
  await expect(page.getByText(/1 \/ game: queued/)).toBeVisible()
  await expect(page.getByText(/1 \/ game: completed/)).toBeVisible({ timeout: 10_000 })
  expect(submitted).toBe(1)
})

test('unannounced provider teams do not break the whole league', async ({ page }) => {
  await page.route('**/rest/v1/rpc/**', route => route.fulfill({ json:
    route.request().url().endsWith('website_member_access')
      ? { state: 'active', kind: 'operator', expires_at: null }
      : route.request().url().endsWith('operator_research_workspace')
        ? { ...workspace, games: [{ ...game, home_name: null, home_id: null, status: null }] } : [],
  }))
  await page.goto('/research')
  await page.getByLabel('Game', { exact: true }).selectOption('1')
  await expect(page.getByRole('heading', { name: 'Away at Team not announced' })).toBeVisible()
  await expect(page.getByText(/Unknown \/ Game cache/)).toBeVisible()
})
