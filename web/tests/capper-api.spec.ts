import { expect, test } from '@playwright/test'

test('non-Owners cannot load credential management data', async ({ page }) => {
  const privateCalls: string[] = []
  await page.route('**/rest/v1/rpc/**', route => {
    const name = route.request().url().split('/').at(-1) ?? ''
    if (name === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'operator', expires_at: null } })
    if (name === 'website_can_edit_all_cappers') return route.fulfill({ json: false })
    if (name.includes('capper_api')) privateCalls.push(name)
    return route.fulfill({ json: [] })
  })
  await page.goto('/integrations')
  await expect(page.getByText(/Verified Owner access is required/)).toBeVisible()
  expect(privateCalls).toEqual([])
})
test('Owner can issue a once-shown key and revoke it without saving its token in browser storage', async ({ page }) => {
  const token = `pmcap_${'a'.repeat(64)}`
  let issued = false, revoked = false
  await page.route('**/rest/v1/rpc/**', route => {
    const name = route.request().url().split('/').at(-1) ?? ''
    if (name === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'owner', expires_at: null } })
    if (name === 'website_can_edit_all_cappers') return route.fulfill({ json: true })
    if (name === 'list_capper_api_keys') return route.fulfill({ json: issued ? [{
      id: 'key-1', discord_user_id: '123456789', label: 'Chief server', created_at: new Date().toISOString(),
      revoked_at: revoked ? new Date().toISOString() : null, last_used_at: null, requests_today: 0,
    }] : [] })
    if (name === 'issue_capper_api_key') {
      expect(route.request().postDataJSON()).toEqual({ p_discord_user_id: '123456789', p_label: 'Chief server' })
      issued = true; return route.fulfill({ json: { id: 'key-1', token } })
    }
    if (name === 'revoke_capper_api_key') { revoked = true; return route.fulfill({ json: true }) }
    return route.fulfill({ json: [] })
  })
  await page.goto('/integrations')
  await page.getByLabel('Capper Discord ID', { exact: true }).fill('123456789')
  await page.getByLabel('Integration label').fill('Chief server')
  await page.getByRole('button', { name: 'Create private API key' }).click()
  await expect(page.getByLabel('New private key')).toHaveValue(token)
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(stored).not.toContain(token)
  await page.getByRole('button', { name: 'Dismiss private key' }).click()
  await expect(page.getByLabel('New private key')).toHaveCount(0)
  await page.getByRole('button', { name: 'Revoke Chief server' }).click()
  await expect(page.getByRole('cell', { name: 'Revoked', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('New private key')).toHaveCount(0)
})
test('Owner can approve and block phone research without issuing an API credential', async ({ page }) => {
  let allowed: boolean | null = null
  const privateCalls: string[] = []
  await page.route('**/rest/v1/rpc/**', route => {
    const name = route.request().url().split('/').at(-1)
    if (name === 'website_member_access') return route.fulfill({ json: { state: 'active', kind: 'owner', expires_at: null } })
    if (name === 'website_can_edit_all_cappers') return route.fulfill({ json: true })
    if (name === 'manage_capper_tools') {
      const args = route.request().postDataJSON()
      if (args?.p_discord_user_id) allowed = args.p_allowed
      return route.fulfill({ json: allowed === null ? [] : [{ discord_user_id: '123456789', allowed }] })
    }
    if (name === 'issue_capper_api_key') privateCalls.push(name)
    return route.fulfill({ json: [] })
  })
  await page.goto('/integrations')
  await page.getByLabel('Research capper Discord ID').fill('123456789')
  await page.getByRole('button', { name: 'Approve research tools', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Block 123456789' })).toBeVisible()
  await page.getByRole('button', { name: 'Block 123456789' }).click()
  await expect(page.getByRole('button', { name: 'Approve 123456789' })).toBeVisible()
  expect(privateCalls).toEqual([])
})
