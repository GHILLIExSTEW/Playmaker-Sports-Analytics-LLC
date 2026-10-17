import { expect, test, type Page } from '@playwright/test'

const game = { competition: 'nfl', sport: 'nfl', game_id: '1', start_at: '2026-10-20T20:00:00Z',
  home_id: '1', home_name: 'Home', away_id: '2', away_name: 'Away', home_score: null, away_score: null,
  status: 'NS', synced_at: '2026-10-17T20:00:00Z' }
const history = Array.from({ length: 12 }, (_, index) => ({ ...game, game_id: String(index + 2),
  start_at: new Date(Date.UTC(2026, 9, 19 - index)).toISOString(), status: 'FT', home_score: 24, away_score: 10 }))
const players = [{ game_id: '2', start_at: history[0].start_at, synced_at: game.synced_at,
  home_id: '1', away_id: '2', home_name: 'Home', away_name: 'Away',
  payload: [{ team: { id: 1, name: 'Home' }, groups: [{ name: 'Passing', players: [
    { player: { id: 4, name: 'Player One' }, statistics: [{ name: 'yards', value: '250' }, { name: 'touchdowns', value: null }] },
  ] }] }],
}]
type Favorite = { competition: string; kind: string; entity_id: string; label: string }

async function mock(page: Page, kind = 'paid', state = 'active') {
  let favorites: Favorite[] = []
  const requests: { rpc: string; body: Record<string, unknown> }[] = []
  const control = { failSearch: false, failSave: false, delayedGame: false, release: () => {} }
  await page.route('**/rest/v1/**', async route => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1) ?? ''
    const body = route.request().postDataJSON() ?? {}
    requests.push({ rpc, body })
    if (rpc === 'website_member_access') return route.fulfill({ json: state === 'active' ? { state, kind, expires_at: null } : { state } })
    if (rpc === 'member_stats_games') {
      if (control.failSearch) return route.fulfill({ status: 400, json: { message: 'Cache search unavailable' } })
      return route.fulfill({ json: { total: body.p_query === 'missing' ? 0 : 21,
        games: body.p_query === 'missing' ? [] : Number(body.p_offset) ? [{ ...game, game_id: '21' }]
          : Array.from({ length: 20 }, (_, index) => ({ ...game, game_id: String(index + 1) })) } })
    }
    if (rpc === 'member_stats_game') {
      if (control.delayedGame && body.p_game_id === '1') await new Promise<void>(resolve => { control.release = resolve })
      return route.fulfill({ json: { game: { ...game, game_id: body.p_game_id,
        home_name: body.p_game_id === '2' ? 'New Home' : 'Home' }, history, players, generated_at: game.synced_at } })
    }
    if (rpc === 'member_stats_watchlist') return route.fulfill({ json: favorites })
    if (rpc === 'save_member_stats_favorite') {
      if (control.failSave) return route.fulfill({ status: 400, json: { message: 'Watchlist save unavailable' } })
      favorites = favorites.filter(row => row.competition !== body.p_competition || row.kind !== body.p_kind || row.entity_id !== body.p_entity_id)
      if (body.p_saved) favorites.push({ competition: String(body.p_competition), kind: String(body.p_kind),
        entity_id: String(body.p_entity_id), label: String(body.p_label) })
      return route.fulfill({ json: favorites })
    }
    return route.fulfill({ json: [] })
  })
  return { requests, control }
}

for (const state of ['signed-out', 'age-required', 'membership-required', 'discord-required', 'launch-pending']) {
  test(`member stats denies ${state} without private calls`, async ({ page }) => {
    const { requests } = await mock(page, 'paid', state)
    await page.goto('/stats')
    await expect(page.getByRole('heading', { name: 'HIGHROLLER member stats' })).toBeVisible()
    await expect(page.getByText(/Checking member access/)).toHaveCount(0)
    expect(requests.filter(row => row.rpc.startsWith('member_stats') || row.rpc === 'save_member_stats_favorite')).toEqual([])
  })
}
for (const kind of ['paid', 'trial', 'owner', 'operator', 'research']) {
  test(`${kind} can use cached comparisons, props and persistent favorites without staff APIs`, async ({ page }) => {
    const { requests } = await mock(page, kind)
    await page.goto('/stats')
    await page.getByRole('radio').first().check()
    await expect(page.getByRole('heading', { name: 'Away at Home' })).toBeVisible()
    const home = page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'Home', exact: true }) })
    await expect(home).toContainText('10 cached scored games / 10 wins')
    await expect(home).toContainText('Scored 24.0 / allowed 10.0 per game')
    await page.getByLabel('Team sample').selectOption('5')
    await expect(home).toContainText('5 cached scored games')
    await page.getByLabel('Team split').selectOption('away')
    await expect(home).toContainText('No cached completed games')
    await page.getByLabel('Player', { exact: true }).selectOption('4')
    await page.getByLabel('Statistic', { exact: true }).selectOption('Passing yards')
    await page.getByLabel('Prop threshold').fill('249.5')
    await expect(page.getByText(/1 observations \/ average 250.0 \/ Over 1, Under 0, Push 0/)).toBeVisible()
    await expect(page.getByLabel('Statistic').getByRole('option', { name: 'Passing touchdowns' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Save Home', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Find Home', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Save Player One', exact: true }).click()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Find Home', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Find Player One', exact: true }).click()
    await page.getByRole('radio').first().check()
    await expect(page.getByLabel('Player', { exact: true })).toHaveValue('4')
    await page.getByRole('button', { name: 'Unsave Player One', exact: true }).first().click()
    await expect(page.getByRole('button', { name: 'Find Player One', exact: true })).toHaveCount(0)
    expect(requests.some(row => /operator_|search_research|request_research_refresh|save_capper/.test(row.rpc))).toBe(false)
    await expect(page.getByRole('button', { name: /live.*refresh/ })).toHaveCount(0)
  })
}

test('member calendar filters, pages, wraps mobile results and reports errors', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const { requests, control } = await mock(page)
  await page.goto('/stats')
  await expect(page.getByRole('radio')).toHaveCount(20)
  await page.getByLabel('Team, matchup or game ID').fill('Home')
  await page.getByLabel('From date').fill('2025-01-01')
  await page.getByLabel('Through date').fill('2025-01-03')
  await page.getByRole('button', { name: 'Search games', exact: true }).click()
  await expect.poll(() => requests.filter(row => row.rpc === 'member_stats_games').at(-1)?.body.p_query).toBe('Home')
  const body = requests.filter(row => row.rpc === 'member_stats_games').at(-1)?.body
  expect(body?.p_start).toBe(new Date('2025-01-01T00:00:00').toISOString())
  expect(body?.p_end).toBe(new Date('2025-01-04T00:00:00').toISOString())
  await page.getByRole('button', { name: 'Next games' }).click()
  await expect(page.getByRole('radio')).toHaveCount(1)
  await expect(page.getByText('21-21 of 21 cached games')).toBeVisible()
  await page.getByRole('button', { name: 'Previous games' }).click()
  await expect(page.getByRole('radio')).toHaveCount(20)
  const bounds = await page.locator('.research-game-list').evaluate(element => ({
    height: element.clientHeight, scroll: element.scrollHeight,
    width: document.documentElement.scrollWidth, viewport: innerWidth,
  }))
  expect(bounds.height).toBeLessThanOrEqual(400)
  expect(bounds.scroll).toBeGreaterThan(bounds.height)
  expect(bounds.width).toBeLessThanOrEqual(bounds.viewport)
  control.failSearch = true
  await page.getByRole('button', { name: 'Reload cache' }).click()
  await expect(page.getByRole('alert')).toContainText('Cache search unavailable')
  control.failSearch = false
  await page.getByRole('button', { name: 'Retry game search' }).click()
  await expect(page.getByRole('radio')).toHaveCount(20)
  await page.getByLabel('Team, matchup or game ID').fill('missing')
  await page.getByRole('button', { name: 'Search games', exact: true }).click()
  await expect(page.getByText(/No cached games match/)).toBeVisible()
})

test('failed watchlist writes are explicit and never appear saved', async ({ page }) => {
  const { control } = await mock(page)
  await page.goto('/stats')
  await page.getByRole('radio').first().check()
  control.failSave = true
  await page.getByRole('button', { name: 'Save Home', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Watchlist save unavailable')
  await expect(page.getByRole('button', { name: 'Find Home', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Save Home', exact: true })).toHaveAttribute('aria-pressed', 'false')
})

test('late game details cannot replace a newer game selection', async ({ page }) => {
  const { control } = await mock(page)
  control.delayedGame = true
  await page.goto('/stats')
  await page.getByRole('radio').nth(0).check()
  await expect(page.getByText('Loading game details...')).toBeVisible()
  await page.getByRole('radio').nth(1).check()
  await expect(page.getByRole('heading', { name: 'Away at New Home' })).toBeVisible()
  control.release()
  await expect(page.getByRole('heading', { name: 'Away at Home', exact: true })).toHaveCount(0)
})
