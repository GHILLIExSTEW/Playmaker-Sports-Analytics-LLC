import { expect, test } from '@playwright/test'

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
