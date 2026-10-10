import { expect, test, type Page } from '@playwright/test'
import { sportsCatalog } from '../src/sportsCatalog'

async function mockSports(page: Page, failingSport?: string) {
  const calls: { slug: string; offset: number }[] = []
  await page.clock.install({ time: new Date('2026-10-10T15:00:00Z') })
  await page.route('https://lhsevzucmmzetpshpffv.supabase.co/**', async (route) => {
    const rpc = new URL(route.request().url()).pathname.split('/').at(-1)
    if (rpc === 'public_sport_events') {
      const slug = route.request().postDataJSON().p_sport_slug
      const params = new URL(route.request().url()).searchParams
      const offset = Number(params.get('offset') || 0)
      calls.push({ slug, offset })
      const count = slug === 'football' ? 1001 : 1
      const events = Array.from({ length: count }, (_, index) => ({
        event_id: String(index), league_name: `${slug} league`, season: '2026', round_name: 'Round 1',
        event_name: `${slug} event ${index}`, start_at: index === 1000 ? '2026-10-10T16:00:00Z' : '2026-10-11T16:00:00Z',
        venue: null, home_name: slug === 'formula-1' ? null : 'Home', away_name: slug === 'formula-1' ? null : 'Away',
        home_logo: null, away_logo: null, home_score: { total: 7 }, away_score: 3,
        status_code: 'NS', status: 'Not Started', synced_at: '2026-10-10T14:00:00Z',
      }))
      await route.fulfill({ status: slug === failingSport ? 500 : 200, json: slug === failingSport ? { message: 'Unavailable' } : events.slice(offset, offset + 1000) })
    } else if (rpc === 'public_nfl_games') {
      calls.push({ slug: 'american-football', offset: 0 })
      await route.fulfill({ json: [{
        game_id: 1, season: 2026, stage: 'Regular Season', week: '5', kickoff_at: '2026-10-10T16:00:00Z',
        venue_name: null, venue_city: null, status_short: 'NS', status_long: 'Not Started',
        home_team_name: 'NFL Home', away_team_name: 'NFL Away', home_team_logo: null, away_team_logo: null,
        home_score: null, away_score: null, synced_at: '2026-10-10T14:00:00Z',
      }] })
    } else if (rpc === 'website_member_access') {
      await route.fulfill({ json: { state: 'signed-out' } })
    } else {
      await route.fulfill({ json: [] })
    }
  })
  return calls
}

for (const mobile of [false, true]) {
  test(`homepage loads every connected sport and links to its center (${mobile ? 'mobile' : 'desktop'})`, async ({ page }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 })
    const calls = await mockSports(page)
    await page.goto('/')
    const board = page.getByRole('region', { name: 'Sports schedule and scores' })
    await expect(board.getByRole('article').first()).toContainText('football event 1000')
    await expect(board.getByRole('article')).toHaveCount(3)
    for (const sport of sportsCatalog) {
      await board.getByRole('button', { name: sport.name, exact: true }).click()
      await expect(board.getByRole('link', { name: `Open ${sport.name} center` })).toHaveAttribute('href', `/sports/${sport.slug}`)
      if (sport.feed === 'unavailable') {
        await expect(board).toContainText(`A schedule feed is not connected for ${sport.name}`)
        await expect(board.getByRole('article')).toHaveCount(0)
      } else {
        await expect(board.getByRole('article').first()).toBeVisible()
        await expect(board).toContainText('Oldest cached event sync:')
      }
    }
    expect(new Set(calls.map((call) => call.slug)).size).toBe(11)
    expect(calls).toContainEqual({ slug: 'football', offset: 1000 })
    await board.getByRole('button', { name: 'Basketball', exact: true }).click()
    await board.getByRole('link', { name: 'Open Basketball center' }).click()
    await expect(page).toHaveURL(/\/sports\/basketball$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Basketball.' })).toBeVisible()
  })
}

test('a failed sport does not block others and can be retried', async ({ page }) => {
  await mockSports(page, 'hockey')
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await board.getByRole('button', { name: 'Hockey', exact: true }).click()
  await expect(board.getByRole('alert')).toContainText('Hockey data could not be loaded.')
  await board.getByRole('button', { name: 'Baseball', exact: true }).click()
  await expect(board.getByRole('article')).toHaveCount(1)
  await page.unroute('https://lhsevzucmmzetpshpffv.supabase.co/**')
  await mockSports(page)
  await board.getByRole('button', { name: 'Hockey', exact: true }).click()
  await board.getByRole('button', { name: 'Retry sports data' }).click()
  await expect(board.getByRole('article')).toHaveCount(1)
  await expect(board.getByRole('alert')).toHaveCount(0)
})

test('empty caches are explicit and recent final events are shown without upcoming events', async ({ page }) => {
  await mockSports(page)
  await page.route('**/rest/v1/rpc/public_sport_events*', async (route) => {
    const slug = route.request().postDataJSON().p_sport_slug
    await route.fulfill({ json: slug === 'mma' ? [{
      event_id: 'fight-1', event_name: 'Recent fight', league_name: 'MMA', start_at: '2026-10-09T20:00:00Z',
      home_name: null, away_name: null, status_code: 'FT', status: 'Finished', synced_at: '2026-10-10T14:00:00Z',
    }] : [] })
  })
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await expect(board).toContainText('No current Football events are available')
  await board.getByRole('button', { name: 'MMA', exact: true }).click()
  await expect(board.getByRole('article')).toContainText('Recent fight')
  await expect(board.getByRole('article')).toContainText('Finished')
})
