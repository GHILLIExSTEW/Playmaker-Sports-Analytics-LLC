import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const guideHtml = readFileSync(new URL('../../Playmaker_Picks_User_Guide.html', import.meta.url), 'utf8')
const guideContent = guideHtml.split('<main>')[1].split('</main>')[0]

test('tab access persists across time, focus and reload without rechecking', async ({ page }) => {
  await page.clock.install()
  let checks = 0
  let modelRequests = 0
  await page.route('**/rest/v1/rpc/**', (route) => {
    if (route.request().url().includes('website_member_access')) {
      checks += 1
      return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    }
    if (route.request().url().includes('public_sport_model_events')) {
      modelRequests += 1
      return route.fulfill({ json: [
      { event_id: '1', season: '2026', start_at: '2026-11-01T20:00:00Z', home_id: '1', home_name: 'Home', away_id: '2', away_name: 'Away', home_score: null, away_score: null, status_code: 'NS', synced_at: '2026-10-11T20:00:00Z' },
      { event_id: '2', season: '2026', start_at: '2026-11-02T20:00:00Z', home_id: '3', home_name: 'Other Home', away_id: '4', away_name: 'Other Away', home_score: null, away_score: null, status_code: 'NS', synced_at: '2026-10-11T20:00:00Z' },
      ] })
    }
    return route.fulfill({ json: [] })
  })

  await page.goto('/models')
  await page.getByRole('button', { name: 'NCAA Football', exact: true }).click()
  await page.getByRole('combobox').selectOption('2')
  const before = checks
  const beforeModels = modelRequests
  await page.clock.fastForward(30_000)
  expect(checks).toBe(before)
  await expect(page.getByRole('button', { name: 'NCAA Football', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('combobox')).toHaveValue('2')
  expect(modelRequests).toBe(beforeModels)
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  expect(checks).toBe(before)
  await expect(page.getByRole('combobox')).toHaveValue('2')
  expect(modelRequests).toBe(beforeModels)
  await expect(page).toHaveURL(/\/models$/)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  expect(checks).toBe(before)
  await page.evaluate(() => sessionStorage.clear())
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  expect(checks).toBe(before + 1)
})

test('expired access is hidden while rechecking and failure removes it', async ({ page }) => {
  await page.clock.install()
  let fail = false
  let pending = false
  let release: () => void = () => {}
  await page.route('**/rest/v1/rpc/**', async (route) => {
    if (!route.request().url().includes('website_member_access')) return route.fulfill({ json: [] })
    if (fail) {
      pending = true
      await new Promise<void>((resolve) => { release = resolve })
      return route.fulfill({ status: 500, json: { message: 'Verification unavailable' } })
    }
    return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: new Date(Date.now() + 5000).toISOString() } })
  })
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  fail = true
  await page.clock.fastForward(5000)
  await expect.poll(() => pending).toBe(true)
  await expect(page.getByText('Checking research access...')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).not.toBeVisible()
  release()
  await expect(page.getByRole('alert')).toContainText('Membership access could not be verified')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toHaveCount(0)
})

function pageHeading(path: string) {
  return path === '/models' ? 'Sports data models.' : path === '/guide' ? 'Playmaker Picks' : 'NFL matchup lab.'
}

for (const path of ['/models', '/nfl/lab', '/guide']) {
  for (const state of [
    { state: 'signed-out' },
    { state: 'active', kind: 'paid', expires_at: null },
    { state: 'active', kind: 'trial', expires_at: null },
  ]) {
    test(`${path} denies ${'kind' in state ? state.kind : state.state} without fetching research`, async ({ page }) => {
      const research: string[] = []
      const guideRequests: string[] = []
      page.on('request', (request) => {
        if (request.url().includes('UserGuidePage') || request.url().includes('Playmaker_Picks_User_Guide.html')) guideRequests.push(request.url())
      })
      await page.route('**/rest/v1/rpc/**', async (route) => {
        const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
        if (rpc === 'website_member_access') await route.fulfill({ json: state })
        else {
          if (rpc && ['public_nfl_games', 'public_nfl_model_games', 'public_sport_model_events', 'public_nfl_team_efficiency', 'operator_user_guide'].includes(rpc)) research.push(rpc)
          await route.fulfill({ json: [] })
        }
      })
      await page.goto(path)
      await expect(page.getByRole('heading', { name: 'Team research access required.' })).toBeVisible()
      expect(research).toEqual([])
      await expect(page.getByRole('article', { name: 'Playmaker user guide' })).toHaveCount(0)
      expect(guideRequests).toEqual([])
    })
  }
  test(`${path} allows owner access`, async ({ page }) => {
    await page.route('**/rest/v1/rpc/**', (route) => route.fulfill({
      json: route.request().url().includes('website_member_access')
        ? { state: 'active', kind: 'owner', expires_at: null }
        : route.request().url().includes('operator_user_guide') ? guideContent : [],
    }))
    await page.goto(path)
    await expect(page.getByRole('heading', { name: pageHeading(path), exact: true })).toBeVisible()
  })
  test(`${path} fails closed, retries, and removes research after access expiry`, async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-11T22:00:00Z') })
    let access: 'error' | 'operator' | 'paid' = 'error'
    await page.route('**/rest/v1/rpc/**', (route) => {
      if (!route.request().url().includes('website_member_access')) return route.fulfill({
        json: route.request().url().includes('operator_user_guide') ? guideContent : [],
      })
      return access === 'error'
        ? route.fulfill({ status: 500, json: { message: 'Access check unavailable' } })
        : route.fulfill({ json: { state: 'active', kind: access, expires_at: '2026-10-11T22:00:05Z' } })
    })
    await page.goto(path)
    await expect(page.getByRole('alert')).toContainText('Membership access could not be verified')
    const heading = page.getByRole('heading', { name: pageHeading(path), exact: true })
    await expect(heading).not.toBeVisible()
    access = 'operator'
    await page.getByRole('button', { name: 'Retry access check' }).click()
    await expect(heading).toBeVisible()
    access = 'paid'
    await page.clock.fastForward(5000)
    await expect(page.getByRole('heading', { name: 'Team research access required.' })).toBeVisible()
    await expect(heading).not.toBeVisible()
  })
}

test('guide navigation opens the canonical content for operators without mobile overflow', async ({ page }) => {
  await page.route('**/rest/v1/rpc/**', (route) => route.fulfill({
    json: route.request().url().includes('website_member_access')
      ? { state: 'active', kind: 'operator', expires_at: null }
      : route.request().url().includes('operator_user_guide') ? guideContent : [],
  }))
  await page.goto('/models')
  await page.getByRole('link', { name: 'Guide (team only)', exact: true }).click()
  await expect(page).toHaveURL(/\/guide$/)
  await expect(page).toHaveTitle('User Guide | Playmaker Picks')
  const guide = page.getByRole('article', { name: 'Playmaker user guide' })
  await expect(guide.getByRole('heading', { level: 2 })).toHaveCount(20)
  await expect(guide).toContainText('7,500')
  await expect(guide).toContainText('public_sport_model_events')
  await expect(guide).toContainText('Laboratory /model')
  await page.setViewportSize({ width: 375, height: 812 })
  const size = await guide.evaluate((element) => ({ width: element.clientWidth, scroll: element.scrollWidth }))
  expect(size.scroll).toBeLessThanOrEqual(size.width)
})

test('guide server denial is explicit and retry fetches the guide again', async ({ page }) => {
  let fail = true
  await page.route('**/rest/v1/rpc/**', (route) => {
    if (route.request().url().includes('website_member_access')) return route.fulfill({
      json: { state: 'active', kind: 'operator', expires_at: null },
    })
    if (route.request().url().includes('operator_user_guide')) return fail
      ? route.fulfill({ status: 403, json: { message: 'Verified OPERATOR or Owner research access required' } })
      : route.fulfill({ json: guideContent })
    return route.fulfill({ json: [] })
  })
  await page.goto('/guide')
  await expect(page.getByRole('alert')).toContainText('Verified OPERATOR or Owner')
  await expect(page.getByRole('article', { name: 'Playmaker user guide' })).toHaveCount(0)
  fail = false
  await page.getByRole('button', { name: 'Retry guide' }).click()
  await expect(page.getByRole('article', { name: 'Playmaker user guide' })).toBeVisible()
})
