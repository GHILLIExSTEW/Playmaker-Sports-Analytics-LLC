import { expect, test, type Page } from '@playwright/test'

const game = { competition: 'nfl', sport: 'nfl', game_id: '1', start_at: '2026-10-18T17:00:00Z',
  home_id: '1', home_name: 'Kansas City Chiefs', away_id: '2', away_name: 'Buffalo Bills',
  home_score: null, away_score: null, status: 'NS', synced_at: '2026-10-17T02:00:00Z' }
async function mock(page: Page, approved = true, projection: Record<string, unknown> | null = null, competition = 'nfl') {
  const calls: string[] = [], ai: Record<string, unknown>[] = []
  let currentProjection = projection
  await page.addInitScript(() => {
    const userId = '11111111-1111-1111-1111-111111111111', expiresAt = Math.floor(Date.now() / 1000) + 3600
    const token = `${btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${btoa(JSON.stringify({ sub: userId, exp: expiresAt, aud: 'authenticated', role: 'authenticated' }))}.test-signature`
    localStorage.setItem('sb-lhsevzucmmzetpshpffv-auth-token', JSON.stringify({
      access_token: token, refresh_token: 'synthetic-refresh', token_type: 'bearer', expires_in: 3600, expires_at: expiresAt,
      user: { id: userId, aud: 'authenticated', role: 'authenticated', created_at: '2026-10-01T00:00:00Z', app_metadata: { provider: 'discord' }, user_metadata: {} },
    }))
  })
  await page.route('**/rest/v1/rpc/**', route => {
    const name = route.request().url().split('/').at(-1) ?? ''
    calls.push(name)
    if (name === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    if (name === 'capper_tools_access') return route.fulfill({ json: approved })
    if (name === 'member_stats_games') return route.fulfill({ json: { games: [game], total: 1 } })
    if (name === 'capper_model_refresh_status') return route.fulfill({ json: { state: 'completed', projections: 1 } })
    if (name === 'capper_tools_game') return route.fulfill({ json: {
      game: { ...game, competition }, history: [{ ...game, game_id: '2', status: 'FT', start_at: '2026-10-10T17:00:00Z', home_score: 27, away_score: 24 }],
      players: [], injuries: [], weather: { temperature_f: 62, wind_mph: 8, precipitation_mm: 0, source: 'forecast', fetched_at: '2026-10-17T01:00:00Z' },
      odds: null, projection: currentProjection, generated_at: '2026-10-17T03:00:00Z',
    } })
    return route.fulfill({ json: [] })
  })
  await page.route('**/functions/v1/capper-research', route => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: {
      'access-control-allow-origin': '*', 'access-control-allow-headers': '*',
    } })
    const data = route.request().postDataJSON()
    ai.push(data)
    return route.fulfill({ headers: { 'access-control-allow-origin': '*' }, json: {
      ...(data.action === 'pull' ? { message: 'Saved 16 team games. Weather, odds and player logs were not refreshed.', model_refresh: 'pending' } : {}),
      text: data.action === 'extract' ? 'User screenshot: Chiefs -3.5, report date not visible.' : 'Matchup snapshot\nCached evidence only. Injury coverage unavailable.',
      generated_at: '2026-10-17T03:00:00Z', request_id: 'ai-1', action: data.action,
    } })
  })
  return { calls, ai, setProjection: (value: Record<string, unknown> | null) => { currentProjection = value } }
}
test('unapproved members cannot open private research data', async ({ page }) => {
  const h = await mock(page, false)
  await page.goto('/tools')
  await expect(page.getByRole('heading', { name: 'Invitation required' })).toBeVisible()
  expect(h.calls).not.toContain('capper_tools_game')
  expect(h.calls).not.toContain('member_stats_games')
})
test('mobile research has team/date search, missing coverage, weather and grounded analysis', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const h = await mock(page)
  await page.goto('/tools')
  await page.getByLabel('Team or matchup').fill('Chiefs')
  await page.getByLabel('Game date').fill('2026-10-18')
  await page.getByRole('button', { name: 'Find games' }).click()
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await expect(page.getByText('62°F')).toBeVisible()
  await expect(page.getByText(/This does not mean no injuries/)).toBeVisible()
  await page.getByLabel(/Your line, source, and notes/).fill('Chiefs -3.5 at my book')
  await page.getByRole('button', { name: 'Analyze matchup', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your breakdown' })).toBeVisible()
  expect(h.ai).toHaveLength(1)
  expect(h.ai[0].notes).toBe('Chiefs -3.5 at my book')
  expect(h.ai[0]).not.toHaveProperty('cached')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Back to games' }).click()
  await expect(page.getByRole('heading', { name: 'Pick your matchup' })).toBeVisible()
})
test('screenshot extraction requires review and remains user supplied', async ({ page }) => {
  const h = await mock(page)
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByText('Read a screenshot', { exact: true }).click()
  await page.getByLabel('Sports screenshot').setInputFiles({
    name: 'line.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4xoAAAAASUVORK5CYII=', 'base64'),
  })
  await page.getByRole('button', { name: 'Read screenshot', exact: true }).click()
  await expect(page.getByLabel(/Your line, source, and notes/)).toHaveValue(/Chiefs -3.5/)
  await expect(page.getByRole('button', { name: 'Analyze matchup', exact: true })).toBeDisabled()
  await page.getByRole('checkbox', { name: /I reviewed/ }).check()
  await page.getByRole('button', { name: 'Analyze matchup', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your breakdown' })).toBeVisible()
  expect(h.ai).toHaveLength(2)
  expect(h.ai[0].action).toBe('extract')
  expect(h.ai[1]).not.toHaveProperty('image')
})
test('AI infrastructure errors are visible and not retried', async ({ page }) => {
  const h = await mock(page)
  let count = 0
  await page.route('**/functions/v1/capper-research', route => {
    if (route.request().method() === 'OPTIONS') return route.fallback()
    count++
    return route.fulfill({ status: 503, headers: { 'access-control-allow-origin': '*' }, json: { error: 'AI analysis is not configured yet.' } })
  })
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByRole('button', { name: 'Analyze matchup', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('AI analysis is not configured yet.')
  expect(count).toBe(1)
  expect(h.ai).toHaveLength(0)
})
test('selected team pull reloads cached report and uses a separate allowance without analysis', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const h = await mock(page)
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByLabel('Team to refresh').selectOption('1')
  await page.getByRole('button', { name: 'Pull latest team data', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('One data pull used; no AI action used.')
  expect(h.ai).toEqual([{ action: 'pull', competition: 'nfl', game_id: '1', team_id: '1' }])
  expect(h.calls.filter(name => name === 'capper_tools_game')).toHaveLength(2)
  expect(h.calls).toContain('capper_model_refresh_status')
  await expect(page.getByRole('status').filter({ hasText: 'Playmaker model recalculated' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your breakdown' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
test('research dropdowns use readable dark control and option colors', async ({ page }) => {
  await mock(page)
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  const selects = page.locator('.capper-tools select')
  await expect(page.getByLabel('Team to refresh')).toBeVisible()
  expect(await selects.count()).toBeGreaterThan(0)
  for (const select of await selects.all()) {
    await expect(select).toHaveCSS('color-scheme', 'dark')
    for (const element of [select, ...await select.locator('option').all()]) {
      const contrast = await element.evaluate(node => {
        const style = getComputedStyle(node)
        const luminance = (color: string) => {
          const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(value => {
            const channel = value / 255
            return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4
          })
          return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
        }
        const foreground = luminance(style.color), background = luminance(style.backgroundColor)
        return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05)
      })
      expect(contrast).toBeGreaterThanOrEqual(4.5)
    }
  }
})
test('approved phone research shows stored model scores, probabilities and stale-after-pull warning', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mock(page, true, { home_score: 27.4, away_score: 21.2, home_probability: .64,
    home_sample: 10, away_sample: 9, league_sample: 200, model_version: 'ratings-v2',
    generated_at: '2026-10-17T01:00:00Z', data_updated_at: '2026-10-17T00:00:00Z' })
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await expect(page.getByLabel('Projected score')).toContainText('27.4')
  await expect(page.getByLabel('Projected score')).toContainText('21.2')
  await expect(page.getByText('Kansas City Chiefs win estimate: 64.0%', { exact: true })).toBeVisible()
  await expect(page.getByText('Buffalo Bills win estimate: 36.0%', { exact: true })).toBeVisible()
  await expect(page.getByText('Projected total: 48.6', { exact: true })).toBeVisible()
  await expect(page.getByText(/model has not been recalculated from that pull/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
test('NHL research does not invent projections or confirmed goalies when coverage is missing', async ({ page }) => {
  await mock(page, true, null, 'hockey:57')
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await expect(page.getByText(/No eligible pregame projection/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'NHL starting goalies' })).toBeVisible()
  await expect(page.getByText('Unknown · Confirmed goalie feed is not connected.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Projected score')).toHaveCount(0)
})
test('team pull waits for the model worker before reloading fresh scores and enabling analysis', async ({ page }) => {
  const old = { home_score: 27.4, away_score: 21.2, home_probability: .64,
    home_sample: 10, away_sample: 9, league_sample: 200, model_version: 'ratings-v2',
    generated_at: '2026-10-17T01:00:00Z', data_updated_at: '2026-10-17T00:00:00Z' }
  const h = await mock(page, true, old)
  let checks = 0
  await page.route('**/rest/v1/rpc/capper_model_refresh_status', route => {
    checks++
    expect(route.request().postDataJSON()).toEqual({ p_request_id: 'ai-1' })
    if (checks === 1) return route.fulfill({ json: { state: 'running' } })
    h.setProjection({ ...old, home_score: 24.4, home_probability: .57,
      generated_at: '2026-10-17T03:01:00Z', data_updated_at: '2026-10-17T03:00:00Z' })
    return route.fulfill({ json: { state: 'completed', projections: 1 } })
  })
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByRole('button', { name: 'Pull latest team data', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Recalculating model…', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Analyze matchup', exact: true })).toBeDisabled()
  expect(h.calls.filter(name => name === 'capper_tools_game')).toHaveLength(1)
  await expect(page.getByRole('status').filter({ hasText: 'Playmaker model recalculated' })).toBeVisible()
  await expect(page.getByLabel('Projected score')).toContainText('24.4')
  await expect(page.getByText('Kansas City Chiefs win estimate: 57.0%', { exact: true })).toBeVisible()
  await expect(page.getByText(/model has not been recalculated from that pull/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Analyze matchup', exact: true })).toBeEnabled()
  expect(h.ai).toHaveLength(1)
})
test('failed model worker reloads saved data but never claims a successful prediction refresh or retries the pull', async ({ page }) => {
  const h = await mock(page)
  await page.route('**/rest/v1/rpc/capper_model_refresh_status', route =>
    route.fulfill({ json: { state: 'failed' } }))
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByRole('button', { name: 'Pull latest team data', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('model recalculation failed or the model worker was unavailable')
  await expect(page.getByRole('status')).toHaveCount(0)
  expect(h.calls.filter(name => name === 'capper_tools_game')).toHaveLength(2)
  expect(h.ai).toHaveLength(1)
})
test('unavailable model status is explicit and does not repeat a provider pull', async ({ page }) => {
  const h = await mock(page)
  await page.route('**/rest/v1/rpc/capper_model_refresh_status', route =>
    route.fulfill({ status: 503, json: { message: 'Status service unavailable' } }))
  await page.goto('/tools')
  await page.getByRole('button', { name: /Buffalo Bills.*Kansas City Chiefs/ }).click()
  await page.getByRole('button', { name: 'Pull latest team data', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('model refresh status could not be checked')
  expect(h.ai).toHaveLength(1)
})
