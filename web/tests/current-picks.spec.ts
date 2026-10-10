import { expect, test, type Page } from '@playwright/test'

const picks = [
  { id: 2, created_at: '2026-10-07T20:00:00Z', sport: 'NHL', capper: 'New Capper', avatar_url: null, selection: 'New NHL selection', analysis: 'NHL analysis', odds: -120, units: 2 },
  { id: 1, created_at: '2026-10-07T19:00:00Z', sport: 'NFL', capper: 'First Capper', avatar_url: null, selection: 'First NFL selection', analysis: 'NFL analysis', odds: 110, units: 1 },
]
const defaultAppearance = { name_color: null, link_color: null, text_color: null, heading_font: 'barlow',
  banner_url: null, section_order: ['stats', 'picks', 'charts', 'results'] }

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

async function mockApi(page: Page, options: { state?: string; kind?: string; feedError?: boolean; accessError?: boolean; dataset?: typeof picks; expiresAt?: string; pageOwner?: string; pageSaveError?: boolean; editAll?: boolean } = {}) {
  if (options.pageOwner || options.editAll) await page.addInitScript(() => {
    const userId = '11111111-1111-1111-1111-111111111111'
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    const token = `${btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${btoa(JSON.stringify({ sub: userId, exp: expiresAt, aud: 'authenticated', role: 'authenticated' }))}.test-signature`
    localStorage.setItem('sb-lhsevzucmmzetpshpffv-auth-token', JSON.stringify({
      access_token: token, refresh_token: 'synthetic-refresh', token_type: 'bearer', expires_in: 3600, expires_at: expiresAt,
      user: { id: userId, aud: 'authenticated', role: 'authenticated', created_at: '2026-10-01T00:00:00Z',
        app_metadata: { provider: 'discord', providers: ['discord'] }, user_metadata: {} },
    }))
  })
  const control = { state: options.state ?? 'active', feedRequests: 0, offsets: [] as number[], failAccess: options.accessError ?? false }
  const profiles = new Map<string, { name: string; accent_color: string; background_color: string; appearance: typeof defaultAppearance; bio: string; avatar_url: string | null; social_links: Record<string, string> }>()
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
    } else if (rpc === 'capper_page_profile') {
      const name = route.request().postDataJSON().p_capper
      await route.fulfill({ json: profiles.get(name) ?? { name, accent_color: '#96d38d', background_color: '#040916', appearance: defaultAppearance, bio: '', avatar_url: null, social_links: {} } })
    } else if (rpc === 'owned_capper_page') {
      await route.fulfill({ json: options.pageOwner ?? null })
    } else if (rpc === 'website_can_edit_all_cappers') {
      await route.fulfill({ json: options.editAll ?? false })
    } else if (rpc === 'save_my_pick_insight') {
      await route.fulfill({ json: true })
    } else if (rpc === 'save_capper_page') {
      const body = route.request().postDataJSON()
      const next = { name: body.p_capper, accent_color: body.p_accent_color,
        background_color: body.p_background_color, appearance: body.p_appearance, bio: body.p_bio, avatar_url: body.p_avatar_url || null, social_links: body.p_social_links }
      if (!options.pageSaveError) profiles.set(next.name, next)
      await route.fulfill({ status: options.pageSaveError ? 403 : 200,
        json: options.pageSaveError ? { message: 'Verified original OPERATOR page owner required.' } : next })
    } else if (rpc === 'public_capper_directory') {
      await route.fulfill({ json: picks.map((pick) => ({ name: pick.capper, avatar_url: profiles.get(pick.capper)?.avatar_url ?? null })) })
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

for (const mobile of [false, true]) {
  test(`cappers navigation opens a dedicated directory and preserves the homepage listing (${mobile ? 'mobile' : 'desktop'})`, async ({ page }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 })
    await mockApi(page)
    await page.goto('/')
    await expect(page.locator('#cappers .capper-card')).toHaveCount(2)
    if (mobile) await page.getByRole('button', { name: 'Toggle navigation' }).click()
    await page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', { name: 'Cappers', exact: true }).click()
    await expect(page).toHaveURL(/\/cappers$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Our cappers.' })).toBeVisible()
    await expect(page.locator('.capper-card')).toHaveCount(2)
    await expect(page.locator('.hero-section, .results-section, .method-section, .membership-promo')).toHaveCount(0)
    if (mobile) await expect(page.getByRole('button', { name: 'Toggle navigation' })).toHaveAttribute('aria-expanded', 'false')
    await expect(page).toHaveTitle('Our Cappers | Playmaker Picks')
    await page.reload()
    const firstCapper = page.locator('.capper-card').filter({ hasText: 'First Capper' })
    await expect(firstCapper).toContainText('1 settled plays · 1-0')
    await expect(firstCapper).toContainText('Verified net: +1u')
    await firstCapper.getByRole('link', { name: 'Open capper homepage' }).click()
    await expect(page).toHaveURL(/\/cappers\/first-capper$/)
    await page.getByRole('link', { name: 'All cappers', exact: true }).click()
    await expect(page).toHaveURL(/\/cappers$/)
    await page.goto('/cappers/missing-capper')
    await page.getByRole('link', { name: 'Return to all cappers' }).click()
    await expect(page).toHaveURL(/\/cappers$/)
    await page.goto('/#cappers')
    await expect(page.locator('#cappers .capper-card')).toHaveCount(2)
    await expect(page.locator('#cappers').getByRole('heading', { name: 'Know who made the call.' })).toBeVisible()
  })
}

test('capper directory shows loading, retryable errors and an empty roster', async ({ page }) => {
  await mockApi(page)
  let releaseDirectory!: () => void
  const directoryWait = new Promise<void>((resolve) => { releaseDirectory = resolve })
  let failed = true
  await page.route('**/rest/v1/rpc/public_capper_directory*', async (route) => {
    await directoryWait
    await route.fulfill({ status: failed ? 500 : 200, json: failed ? { message: 'Directory unavailable' } : [] })
  })
  await page.goto('/cappers/')
  await expect(page.getByRole('status')).toContainText('Loading capper directory')
  releaseDirectory()
  await expect(page.getByRole('alert')).toContainText('The capper directory could not be loaded.')
  failed = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.getByText('No current OPERATOR cappers are listed yet.')).toBeVisible()
  await expect(page.locator('.capper-card')).toHaveCount(0)
})

test('capper directory keeps profiles accessible when performance records fail', async ({ page }) => {
  await mockApi(page)
  await page.route('**/rest/v1/rpc/public_settled_results*', (route) => route.fulfill({ status: 500, json: { message: 'Results unavailable' } }))
  await page.goto('/cappers')
  await expect(page.locator('.capper-card')).toHaveCount(2)
  await expect(page.getByRole('alert')).toContainText('Capper performance records could not be loaded.')
  await expect(page.locator('.capper-card').filter({ hasText: 'Verified net:' })).toHaveCount(0)
  await page.unroute('**/rest/v1/rpc/public_settled_results*')
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.locator('.capper-card').filter({ hasText: 'First Capper' })).toContainText('Verified net: +1u')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('owner customizes capper bio, color, avatar and links with persistent public rendering', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  await page.route('https://images.example/avatar.png', (route) => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>' }))
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  await page.getByLabel('Bio / description').fill('My approach to finding value.')
  await page.getByLabel('Accent color').fill('#123abc')
  await page.getByLabel('Page background color').fill('#f0e0cc')
  await page.getByLabel('Profile image URL').fill('https://images.example/avatar.png')
  await page.getByLabel('X URL', { exact: true }).fill('https://x.com/newcapper')
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByText('Capper page saved.', { exact: true })).toBeVisible()
  await expect(page.getByText('My approach to finding value.', { exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: 'New Capper avatar' })).toHaveAttribute('src', 'https://images.example/avatar.png')
  await expect(page.getByRole('navigation', { name: 'New Capper social links' }).getByRole('link', { name: 'X', exact: true })).toHaveAttribute('href', 'https://x.com/newcapper')
  await expect(page.locator('.capper-custom-profile')).toHaveCSS('border-top-color', 'rgb(18, 58, 188)')
  await expect(page.locator('.capper-home')).toHaveCSS('background-color', 'rgb(240, 224, 204)')
  await expect(page.locator('.capper-home')).toHaveCSS('color', 'rgb(17, 17, 17)')
  await page.reload()
  await expect(page.getByText('My approach to finding value.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit your capper page' })).toBeVisible()
  await expect(page.locator('.capper-home')).toHaveCSS('background-color', 'rgb(240, 224, 204)')
  await page.goto('/')
  await expect(page.locator('.site-header')).toHaveCSS('background-color', 'rgba(4, 9, 22, 0.96)')
})

test('another capper cannot see editing controls and failed saves remain explicit', async ({ page }) => {
  await mockApi(page, { pageOwner: 'First Capper', pageSaveError: true })
  await page.goto('/cappers/new-capper')
  await expect(page.locator('.capper-custom-profile')).toHaveCSS('border-top-color', 'rgb(150, 211, 141)')
  await expect(page.getByRole('button', { name: 'Edit your capper page' })).toHaveCount(0)
  await page.goto('/cappers/first-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  await page.getByLabel('Bio / description').fill('Unsaved bio')
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Page was not saved.')
  await expect(page.getByText('Capper page saved.', { exact: true })).toHaveCount(0)
})

test('owner uploads avatar to own folder as resized WebP and persists its public URL', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  const uploads: { path: string; type: string; body: Buffer }[] = []
  await page.route('**/storage/v1/object/website-assets/**', async (route) => {
    uploads.push({ path: new URL(route.request().url()).pathname, type: route.request().headers()['content-type'], body: route.request().postDataBuffer()! })
    await route.fulfill({ json: { Key: 'uploaded' } })
  })
  await page.route('**/storage/v1/object/public/website-assets/**', (route) => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>',
  }))
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 1024
    canvas.height = 768
    const context = canvas.getContext('2d')!
    context.fillStyle = 'red'
    context.fillRect(0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/png').split(',')[1]
  })
  await page.getByLabel('Upload profile image').setInputFiles({
    name: 'avatar.png', mimeType: 'image/png',
    buffer: Buffer.from(png, 'base64'),
  })
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByText('Capper page saved.', { exact: true })).toBeVisible()
  expect(uploads).toHaveLength(1)
  expect(uploads[0].path).toMatch(/\/website-assets\/capper-avatars\/11111111-1111-1111-1111-111111111111\/[0-9a-f-]{36}\.webp$/)
  expect(uploads[0].type).toContain('multipart/form-data')
  expect(uploads[0].body.toString('latin1')).toContain('Content-Type: image/webp')
  const start = uploads[0].body.indexOf(Buffer.from('RIFF'))
  const size = uploads[0].body.readUInt32LE(start + 4) + 8
  const dimensions = await page.evaluate(async (base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0))
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/webp' }))
    const result = [bitmap.width, bitmap.height]
    bitmap.close()
    return result
  }, uploads[0].body.subarray(start, start + size).toString('base64'))
  expect(dimensions).toEqual([512, 384])
  await expect(page.getByRole('img', { name: 'New Capper avatar' })).toHaveAttribute('src', /\/object\/public\/website-assets\/capper-avatars\/.*\.webp$/)
})

test('invalid image file does not upload or save', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  await page.getByLabel('Upload profile image').setInputFiles({ name: 'script.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg/>') })
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Choose a PNG, JPEG or WebP')
  await expect(page.getByText('Capper page saved.', { exact: true })).toHaveCount(0)
})

test('banner upload is resized to 1600px and displayed from the saved public URL', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  const uploads: Buffer[] = []
  let path = ''
  await page.route('**/storage/v1/object/website-assets/**', async (route) => {
    const upload = route.request().postDataBuffer()
    if (upload) uploads.push(upload)
    path = new URL(route.request().url()).pathname
    await route.fulfill({ json: { Key: 'uploaded' } })
  })
  await page.route('**/storage/v1/object/public/website-assets/**', (route) => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="30"/>',
  }))
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 3200; canvas.height = 1200
    canvas.getContext('2d')!.fillRect(0, 0, 3200, 1200)
    return canvas.toDataURL('image/png').split(',')[1]
  })
  await page.getByLabel('Upload banner image').setInputFiles({ name: 'banner.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') })
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByText('Capper page saved.', { exact: true })).toBeVisible()
  expect(path).toContain('/website-assets/capper-avatars/11111111-1111-1111-1111-111111111111/')
  const body = uploads[0]
  expect(uploads).toHaveLength(1)
  if (!body) throw new Error('Expected banner upload.')
  const start = body.indexOf(Buffer.from('RIFF'))
  const size = body.readUInt32LE(start + 4) + 8
  const dimensions = await page.evaluate(async (base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0))
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/webp' }))
    const result = [bitmap.width, bitmap.height]
    bitmap.close()
    return result
  }, body.subarray(start, start + size).toString('base64'))
  expect(dimensions).toEqual([1600, 600])
  await expect(page.getByRole('img', { name: 'New Capper banner' })).toHaveAttribute('src', /\/object\/public\/website-assets\/capper-avatars\/.*\.webp$/)
})

test('capper can edit own pick insight on the board but not another authors pick', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  await page.goto('/picks')
  const ownPick = page.getByRole('article').filter({ hasText: 'New NHL selection' })
  const otherPick = page.getByRole('article').filter({ hasText: 'First NFL selection' })
  await expect(otherPick.getByRole('button', { name: 'Edit your insight' })).toHaveCount(0)
  await ownPick.getByRole('button', { name: 'Edit your insight' }).click()
  await ownPick.getByLabel('Your reasoning for this pick').fill('My updated capper reasoning')
  await ownPick.getByRole('button', { name: 'Save insight', exact: true }).click()
  await expect(ownPick.getByText('Insight saved.', { exact: true })).toBeVisible()
  await ownPick.getByText('Capper insight', { exact: true }).click()
  await expect(ownPick.getByText('My updated capper reasoning', { exact: true })).toBeVisible()
})

test('capper can add missing insight on their page and failed changes remain explicit', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper', dataset: [{ ...picks[0], analysis: '' }] })
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Add your insight' }).click()
  await page.getByLabel('Your reasoning for this pick').fill('First justification')
  await page.route('**/rest/v1/rpc/save_my_pick_insight', (route) => route.fulfill({
    status: 403, json: { message: 'Only the original author can submit insight.' },
  }))
  await page.getByRole('button', { name: 'Save insight', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Insight was not saved.')
  await expect(page.getByText('Capper insight', { exact: true })).toHaveCount(0)
})

test('Owner role editor can customize another capper page and edit every open insight', async ({ page }) => {
  await mockApi(page, { editAll: true, kind: 'owner' })
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  await page.getByLabel('Bio / description').fill('Updated by site Owner')
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByText('Updated by site Owner', { exact: true })).toBeVisible()
  await page.goto('/picks')
  await expect(page.getByRole('button', { name: 'Edit your insight' })).toHaveCount(2)
  const pick = page.getByRole('article').filter({ hasText: 'First NFL selection' })
  await pick.getByRole('button', { name: 'Edit your insight' }).click()
  await pick.getByLabel('Your reasoning for this pick').fill('Owner-assisted reasoning')
  await pick.getByRole('button', { name: 'Save insight', exact: true }).click()
  await expect(pick.getByText('Insight saved.', { exact: true })).toBeVisible()
})

test('custom colors, font, banner and section order persist and render in DOM order', async ({ page }) => {
  await mockApi(page, { pageOwner: 'New Capper' })
  await page.route('https://images.example/banner.png', (route) => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="30"><rect width="100" height="30" fill="blue"/></svg>',
  }))
  await page.goto('/cappers/new-capper')
  await page.getByRole('button', { name: 'Edit your capper page' }).click()
  await page.getByRole('group', { name: 'Display name color' }).getByLabel('Automatic color').uncheck()
  await page.getByLabel('Custom name color').fill('#ffcc00')
  await page.getByRole('group', { name: 'Link color' }).getByLabel('Automatic color').uncheck()
  await page.getByLabel('Custom link color').fill('#99ddff')
  await page.getByRole('group', { name: 'Body text color' }).getByLabel('Automatic color').uncheck()
  await page.getByLabel('Custom text color').fill('#eeeeee')
  await page.getByLabel('Heading font').selectOption('georgia')
  await page.getByLabel('Banner image URL').fill('https://images.example/banner.png')
  await page.getByLabel('Website URL', { exact: true }).fill('https://example.com')
  await page.getByRole('button', { name: 'Move Current picks up' }).click()
  await page.getByRole('button', { name: 'Save capper page', exact: true }).click()
  await expect(page.getByText('Capper page saved.', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'New Capper', exact: true })).toHaveCSS('color', 'rgb(255, 204, 0)')
  await expect(page.getByRole('heading', { name: 'New Capper', exact: true })).toHaveCSS('font-family', 'Georgia, serif')
  await expect(page.getByRole('navigation', { name: 'New Capper social links' }).getByRole('link', { name: 'Website' })).toHaveCSS('color', 'rgb(153, 221, 255)')
  await expect(page.locator('.capper-home')).toHaveCSS('color', 'rgb(238, 238, 238)')
  await expect(page.getByRole('img', { name: 'New Capper banner' })).toBeVisible()
  expect(await page.locator('[data-capper-section]').evaluateAll((elements) => elements.map((element) => element.getAttribute('data-capper-section')))).toEqual(['picks', 'stats', 'charts', 'results'])
  await page.reload()
  await expect(page.getByRole('img', { name: 'New Capper banner' })).toBeVisible()
  expect(await page.locator('[data-capper-section]').evaluateAll((elements) => elements.map((element) => element.getAttribute('data-capper-section')))).toEqual(['picks', 'stats', 'charts', 'results'])
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

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
