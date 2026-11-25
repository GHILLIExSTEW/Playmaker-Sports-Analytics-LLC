import { expect, test } from '@playwright/test'

test('public shop displays products and the Custom Stitches profile without management tools', async ({ page }) => {
  const calls: string[] = []
  await page.route('**/rest/v1/rpc/**', (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1) ?? ''
    calls.push(rpc)
    return route.fulfill({ json: rpc === 'public_merchandise_catalog' ? [
      { id: '1', name: 'Playmaker cap', description: 'Embroidered', price: 25, in_stock: true, image_url: null, order_url: null },
      { id: '2', name: 'Playmaker shirt', description: '', price: 30, in_stock: true, image_url: null, order_url: 'https://shop.example/shirt' },
    ] : [] })
  })

  await page.goto('/merchandise')
  await expect(page.getByRole('heading', { name: 'Playmaker cap' })).toBeVisible()
  await expect(page.getByText('Online checkout coming soon.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Order merchandise' })).toHaveAttribute('href', 'https://shop.example/shirt')
  await expect(page.getByRole('heading', { name: 'Manage merchandise' })).toHaveCount(0)
  expect(calls).not.toContain('merchandise_products_for_manager')

  await page.getByRole('button', { name: 'Custom Stitches' }).click()
  await expect(page.getByRole('heading', { name: 'Custom Stitches' })).toBeVisible()
  await expect(page.getByText('Partner website coming soon.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Playmaker cap' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Shop merchandise' }).click()
  await expect(page.getByRole('heading', { name: 'Playmaker cap' })).toBeVisible()
})

test('the separate admin page requires sign-in and does not expose product controls', async ({ page }) => {
  await page.route('**/rest/v1/rpc/**', (route) => route.fulfill({ json: [] }))
  await page.goto('/merchandise/admin')
  await expect(page.getByRole('heading', { name: 'Merchandise management', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign in with Discord' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Add a product' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Custom Stitches' })).toHaveCount(0)
})
