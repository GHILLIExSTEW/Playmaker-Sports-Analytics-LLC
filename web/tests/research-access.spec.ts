import { expect, test } from '@playwright/test'

test('model league selection survives periodic and focus access checks', async ({ page }) => {
  await page.clock.install()
  let checks = 0
  await page.route('**/rest/v1/rpc/**', (route) => {
    if (route.request().url().includes('website_member_access')) {
      checks += 1
      return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    }
    if (route.request().url().includes('public_sport_model_events')) return route.fulfill({ json: [
      { event_id: '1', season: '2026', start_at: '2026-11-01T20:00:00Z', home_id: '1', home_name: 'Home', away_id: '2', away_name: 'Away', home_score: null, away_score: null, status_code: 'NS', synced_at: '2026-10-11T20:00:00Z' },
      { event_id: '2', season: '2026', start_at: '2026-11-02T20:00:00Z', home_id: '3', home_name: 'Other Home', away_id: '4', away_name: 'Other Away', home_score: null, away_score: null, status_code: 'NS', synced_at: '2026-10-11T20:00:00Z' },
    ] })
    return route.fulfill({ json: [] })
  })

  await page.goto('/models')
  await page.getByRole('button', { name: 'NCAA Football', exact: true }).click()
  await page.getByRole('combobox').selectOption('2')
  const before = checks
  await page.clock.fastForward(30_000)
  await expect.poll(() => checks).toBeGreaterThan(before)
  await expect(page.getByRole('button', { name: 'NCAA Football', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('combobox')).toHaveValue('2')
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect.poll(() => checks).toBeGreaterThan(before + 1)
  await expect(page.getByRole('combobox')).toHaveValue('2')
  await expect(page).toHaveURL(/\/models$/)
})

test('research is hidden during a pending check and removed on verification failure', async ({ page }) => {
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
    return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
  })
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toBeVisible()
  fail = true
  await page.clock.fastForward(30_000)
  await expect.poll(() => pending).toBe(true)
  await expect(page.getByText('Checking research access...')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).not.toBeVisible()
  release()
  await expect(page.getByRole('alert')).toContainText('Membership access could not be verified')
  await expect(page.getByRole('heading', { name: 'Sports data models.' })).toHaveCount(0)
})

for (const path of ['/models', '/nfl/lab']) {
  for (const state of [
    { state: 'signed-out' },
    { state: 'active', kind: 'paid', expires_at: null },
    { state: 'active', kind: 'trial', expires_at: null },
  ]) {
    test(`${path} denies ${'kind' in state ? state.kind : state.state} without fetching research`, async ({ page }) => {
      const research: string[] = []
      await page.route('**/rest/v1/rpc/**', async (route) => {
        const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
        if (rpc === 'website_member_access') await route.fulfill({ json: state })
        else {
          if (rpc && ['public_nfl_games', 'public_nfl_model_games', 'public_sport_model_events', 'public_nfl_team_efficiency'].includes(rpc)) research.push(rpc)
          await route.fulfill({ json: [] })
        }
      })
      await page.goto(path)
      await expect(page.getByRole('heading', { name: 'Team research access required.' })).toBeVisible()
      expect(research).toEqual([])
    })
  }
  test(`${path} allows owner access`, async ({ page }) => {
    await page.route('**/rest/v1/rpc/**', (route) => route.fulfill({
      json: route.request().url().includes('website_member_access')
        ? { state: 'active', kind: 'owner', expires_at: null } : [],
    }))
    await page.goto(path)
    await expect(page.getByRole('heading', { name: path === '/models' ? 'Sports data models.' : 'NFL matchup lab.' })).toBeVisible()
  })
  test(`${path} fails closed, retries, and removes research after role revocation`, async ({ page }) => {
    await page.clock.install()
    let access: 'error' | 'operator' | 'paid' = 'error'
    await page.route('**/rest/v1/rpc/**', (route) => {
      if (!route.request().url().includes('website_member_access')) return route.fulfill({ json: [] })
      return access === 'error'
        ? route.fulfill({ status: 500, json: { message: 'Access check unavailable' } })
        : route.fulfill({ json: { state: 'active', kind: access, expires_at: null } })
    })
    await page.goto(path)
    await expect(page.getByRole('alert')).toContainText('Membership access could not be verified')
    const heading = page.getByRole('heading', { name: path === '/models' ? 'Sports data models.' : 'NFL matchup lab.' })
    await expect(heading).not.toBeVisible()
    access = 'operator'
    await page.getByRole('button', { name: 'Retry access check' }).click()
    await expect(heading).toBeVisible()
    access = 'paid'
    await page.clock.fastForward(30_000)
    await expect(page.getByRole('heading', { name: 'Team research access required.' })).toBeVisible()
    await expect(heading).not.toBeVisible()
  })
}
