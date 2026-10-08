import { expect, test, type Page } from '@playwright/test'

const picks = [
  { id: 2, created_at: '2026-10-07T20:00:00Z', sport: 'NHL', capper: 'New Capper', avatar_url: null, selection: 'New NHL selection', analysis: 'NHL analysis', odds: -120, units: 2 },
  { id: 1, created_at: '2026-10-07T19:00:00Z', sport: 'NFL', capper: 'First Capper', avatar_url: null, selection: 'First NFL selection', analysis: 'NFL analysis', odds: 110, units: 1 },
]

test('insight stays collapsed and missing insight has no reveal control', async ({ page }) => {
  await mockApi(page, { dataset: [{ ...picks[0], analysis: '' }, picks[1]] })
  await page.goto('/picks')
  const withInsight = page.getByRole('article').filter({ hasText: 'First NFL selection' })
  const withoutInsight = page.getByRole('article').filter({ hasText: 'New NHL selection' })
  await expect(withoutInsight.locator('summary')).toHaveCount(0)
  await expect(withInsight.getByText('NFL analysis', { exact: true })).not.toBeVisible()
  await withInsight.getByText('Capper insight', { exact: true }).click()
  await expect(withInsight.getByText('NFL analysis', { exact: true })).toBeVisible()
  await withInsight.getByText('Capper insight', { exact: true }).click()
  await expect(withInsight.getByText('NFL analysis', { exact: true })).not.toBeVisible()
})

async function mockApi(page: Page, options: { state?: string; kind?: string; feedError?: boolean; accessError?: boolean; dataset?: typeof picks; expiresAt?: string } = {}) {
  const control = { state: options.state ?? 'active', feedRequests: 0, offsets: [] as number[], failAccess: options.accessError ?? false }
  await page.route('https://lhsevzucmmzetpshpffv.supabase.co/**', async (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
    if (rpc === 'website_member_access') {
      await route.fulfill({
        status: control.failAccess ? 500 : 200,
        json: control.failAccess ? { message: 'Access verification unavailable' }
          : control.state === 'active' ? { state: 'active', kind: options.kind ?? 'paid', expires_at: options.kind === 'operator' ? null : options.expiresAt ?? '2099-01-01T00:00:00Z' } : { state: control.state },
      })
    } else if (rpc === 'member_current_picks') {
      control.feedRequests += 1
      const body = route.request().postDataJSON()
      const query = new URL(route.request().url()).searchParams
      const offset = Number(query.get('offset') ?? 0)
      control.offsets.push(offset)
      const limit = Number(query.get('limit') ?? 1000)
      const matching = (options.dataset ?? picks).filter((pick) => !body?.p_capper || pick.capper === body.p_capper)
      await route.fulfill({ status: options.feedError ? 500 : 200,
        json: options.feedError ? { message: 'Feed unavailable' } : matching.slice(offset, offset + limit) })
    } else if (rpc === 'public_capper_directory') {
      await route.fulfill({ json: picks.map((pick) => ({ name: pick.capper, avatar_url: null })) })
    } else if (rpc === 'public_settled_results') {
      await route.fulfill({ json: [{ created_at: '2026-10-06T19:00:00Z', settled_at: '2026-10-06T23:00:00Z',
        sport: 'NFL', capper: 'First Capper', selection: 'Past selection', odds: 100, units: 1, status: 'win', net_units: 1, avatar_url: null }] })
    } else if (rpc === 'member_profiles') {
      await route.fulfill({ json: [{ age_verified_at: '2026-10-01T00:00:00Z', created_at: '2026-10-01T00:00:00Z',
        display_name: 'Saved Member', public_handle: 'saved-member', avatar_url: null, public_profile_enabled: true,
        timezone: 'America/Chicago', discord_alerts_enabled: true, email_alerts_enabled: false }] })
    } else if (rpc === 'logout') {
      control.state = 'signed-out'
      await route.fulfill({ json: {} })
    } else {
      await route.fulfill({ json: [] })
    }
  })
  return control
}

test('filters by sport/capper and opens a current-only capper page', async ({ page }) => {
  await mockApi(page)
  await page.goto('/picks')
  await expect(page.getByRole('heading', { name: 'Expert picks.' })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(2)
  await page.getByLabel('Sport', { exact: true }).selectOption('NFL')
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('heading', { name: 'First NFL selection' })).toBeVisible()
  await page.getByLabel('Capper', { exact: true }).selectOption('New Capper')
  await expect(page.getByText('No published open picks match these filters.')).toBeVisible()
  await page.getByLabel('Sport', { exact: true }).selectOption('All')
  await page.getByRole('link', { name: 'Capper page & settled record' }).click()
  await expect(page).toHaveURL(/\/cappers\/new-capper$/)
  await expect(page.getByRole('heading', { name: 'New Capper', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'New NHL selection' })).toBeVisible()
  await expect(page.getByText('No settled results yet.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'First NFL selection' })).toHaveCount(0)
})

test('OPERATOR access renders the picks board without claiming paid membership', async ({ page }) => {
  await mockApi(page, { kind: 'operator' })
  await page.goto('/picks')
  await expect(page.getByText('HIGHROLLER team access', { exact: false })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(2)
  await expect(page.getByText('HIGHROLLER paid access', { exact: false })).toHaveCount(0)
})

for (const [state, message] of [
  ['signed-out', 'Current picks are for HIGHROLLER members.'],
  ['age-required', 'Complete age verification'],
  ['discord-required', 'A verified Discord sign-in is required.'],
  ['launch-pending', 'Website premium access is awaiting launch validation.'],
  ['membership-required', 'No current verified HIGHROLLER access was found.'],
]) {
  test(`${state} does not request or display premium picks`, async ({ page }) => {
    const control = await mockApi(page, { state })
    await page.goto('/picks')
    await expect(page.getByText(message, { exact: false })).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(0)
    expect(control.feedRequests).toBe(0)
  })
}

test('revoked access clears previously loaded picks', async ({ page }) => {
  const control = await mockApi(page)
  await page.goto('/picks')
  await expect(page.getByRole('article')).toHaveCount(2)
  control.state = 'membership-required'
  await page.getByRole('button', { name: 'Refresh picks & access' }).click()
  await expect(page.getByText('No current verified HIGHROLLER access was found.', { exact: false })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(0)
})

test('access verification failure is explicit and retry recovers', async ({ page }) => {
  const control = await mockApi(page, { accessError: true })
  await page.goto('/picks')
  await expect(page.getByRole('alert')).toContainText('Membership access could not be verified')
  expect(control.feedRequests).toBe(0)
  control.failAccess = false
  await page.getByRole('button', { name: 'Retry access check' }).click()
  await expect(page.getByRole('article')).toHaveCount(2)
})

test('feed failure is not presented as an empty successful board', async ({ page }) => {
  await mockApi(page, { feedError: true })
  await page.goto('/picks')
  await expect(page.getByRole('alert')).toContainText('Current picks could not be loaded')
  await expect(page.getByRole('article')).toHaveCount(0)
  await expect(page.getByText('No published open picks', { exact: false })).toHaveCount(0)
})

test('mobile board fits viewport and navigation reaches picks', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page)
  await page.goto('/picks')
  await expect(page.getByRole('article')).toHaveCount(2)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Toggle navigation' }).click()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', { name: 'Expert Picks' })).toBeVisible()
})

test('larger boards fetch every page and progressively display results', async ({ page }) => {
  const dataset = Array.from({ length: 1001 }, (_, index) => ({ ...picks[0], id: index + 1, selection: `Selection ${index + 1}` }))
  const control = await mockApi(page, { dataset })
  await page.goto('/picks')
  await expect(page.getByText('1001 open plays')).toBeVisible()
  expect(control.offsets).toContain(0)
  expect(control.offsets).toContain(1000)
  await expect(page.getByRole('article')).toHaveCount(50)
  await page.getByRole('button', { name: 'Show more picks' }).click()
  await expect(page.getByRole('article')).toHaveCount(100)
})

for (const rosterUnavailable of [false, true]) {
test(`account reloads saved profile controls and displays verified membership; sign-out closes picks (${rosterUnavailable ? 'unavailable' : 'available'} roster)`, async ({ page }) => {
  await mockApi(page)
  if (rosterUnavailable) {
    await page.route('**/rest/v1/rpc/public_favorite_cappers*', (route) => route.fulfill({
      status: 400, json: { message: 'Website capper roster is unavailable or stale. Check the bot OPERATOR role sync.' },
    }))
  }
  await page.addInitScript(() => {
    const userId = '11111111-1111-1111-1111-111111111111'
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    const token = `${btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${btoa(JSON.stringify({ sub: userId, exp: expiresAt, aud: 'authenticated', role: 'authenticated' }))}.test-signature`
    localStorage.setItem('sb-lhsevzucmmzetpshpffv-auth-token', JSON.stringify({
      access_token: token, refresh_token: 'synthetic-refresh', token_type: 'bearer', expires_in: 3600, expires_at: expiresAt,
      user: { id: userId, aud: 'authenticated', role: 'authenticated', created_at: '2026-10-01T00:00:00Z',
        app_metadata: { provider: 'discord', providers: ['discord'] }, user_metadata: { full_name: 'Discord Name' },
        identities: [{ provider: 'discord', provider_id: '123456789', user_id: userId }] },
    }))
  })
  await page.goto('/account')
  await expect(page.getByLabel('Display name', { exact: true })).toHaveValue('Saved Member')
  await expect(page.getByLabel('Public handle', { exact: false })).toHaveValue('saved-member')
  await expect(page.getByLabel('Time zone', { exact: false })).toHaveValue('America/Chicago')
  await expect(page.getByLabel('Make my profile public')).toBeChecked()
  await expect(page.getByText('HIGHROLLER paid', { exact: true })).toBeVisible()
  if (rosterUnavailable) {
    await expect(page.getByRole('alert')).toContainText('Capper choices are unavailable.')
    await expect(page.getByRole('heading', { name: 'Account settings.' })).toBeVisible()
    await page.unroute('**/rest/v1/rpc/public_favorite_cappers*')
    await page.getByRole('button', { name: 'Retry capper choices' }).click()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByLabel('Display name', { exact: true })).toHaveValue('Saved Member')
  }
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Continue with Discord' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', { name: 'Expert Picks' }).click()
  await expect(page.getByText('Current picks are for HIGHROLLER members.', { exact: false })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(0)
})
}

test('no published open picks has a distinct empty state', async ({ page }) => {
  await mockApi(page, { dataset: [] })
  await page.goto('/picks')
  await expect(page.getByText('No published open picks.', { exact: false })).toBeVisible()
  await expect(page.getByText('0 open plays')).toBeVisible()
})

test('historical non-operators cannot become capper pages from settled records', async ({ page }) => {
  await mockApi(page)
  await page.route('**/rest/v1/rpc/public_capper_directory*', (route) => route.fulfill({ json: [{ name: 'New Capper', avatar_url: null }] }))
  await page.goto('/cappers/first-capper')
  await expect(page.getByRole('heading', { name: 'Profile not found' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Current picks.' })).toHaveCount(0)
})

for (const [name, expiresAt, elapsed] of [
  ['pass expiry', '2026-10-08T01:00:05Z', 5000],
  ['scheduled access recheck', '2099-01-01T00:00:00Z', 30_000],
] as const) {
  test(`${name} automatically hides previously loaded picks`, async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-08T01:00:00Z') })
    const control = await mockApi(page, { expiresAt })
    await page.goto('/picks')
    await expect(page.getByRole('article')).toHaveCount(2)
    control.state = 'membership-required'
    await page.clock.fastForward(elapsed)
    await expect(page.getByText('No current verified HIGHROLLER access was found.', { exact: false })).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(0)
  })
}
