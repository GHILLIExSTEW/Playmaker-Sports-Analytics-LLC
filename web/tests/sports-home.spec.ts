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
        venue: null, home_name: slug === 'formula-1' ? null : 'Home Club', away_name: slug === 'formula-1' ? null : 'Away Club',
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
  test(`homepage sections remain opaque while scrolling and returning home (${mobile ? 'mobile' : 'desktop'})`, async ({ page }) => {
    await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1440, height: 768 })
    await mockSports(page)
    await page.addInitScript(() => {
      class InactiveObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
        takeRecords() { return [] }
      }
      Object.defineProperty(window, 'IntersectionObserver', { value: InactiveObserver })
    })
    await page.goto('/')
    await expect(page.locator('.personalized-feed')).toContainText('Sign in to build a feed')
    const hero = await page.locator('.hero-section').boundingBox()
    const feed = await page.locator('.personalized-feed').boundingBox()
    expect(hero).not.toBeNull()
    expect(feed).not.toBeNull()
    expect(Math.abs(feed!.y - (hero!.y + hero!.height))).toBeLessThanOrEqual(1)
    for (const selector of ['.personalized-feed', '#results', '#cappers', '#membership', '#method', '#community']) {
      const section = page.locator(selector)
      await section.scrollIntoViewIfNeeded()
      await expect(section).toHaveCSS('opacity', '1')
      await expect(section).toHaveCSS('transform', 'none')
    }
    await page.getByRole('link', { name: 'Manage favorites' }).click()
    await expect(page).toHaveURL(/\/account$/)
    await page.getByRole('link', { name: 'Playmaker Picks home' }).click()
    await expect(page.locator('.personalized-feed')).toHaveCSS('opacity', '1')
    await page.goto('/#cappers')
    await expect(page.locator('#cappers')).toHaveCSS('opacity', '1')
  })

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

for (const [sportName, leagueName] of [
  ['Football', 'Major League Soccer'], ['Basketball', 'NBA'], ['Basketball', 'NBA W'],
  ['Baseball', 'MLB'], ['Hockey', 'NHL'], ['Hockey', 'AHL'],
  ['Rugby', 'Major League Rugby'], ['Volleyball', 'Pro Volleyball Federation'],
]) {
  test(`${sportName} prioritizes ${leagueName} and retains international fallback`, async ({ page }) => {
    await mockSports(page)
    await page.route('**/rest/v1/rpc/public_sport_events*', (route) => route.fulfill({ json: [
      { event_id: 'international', league_name: 'National League', event_name: 'International fixture', start_at: '2026-10-10T16:00:00Z', status_code: 'NS', status: 'Not Started', synced_at: '2026-10-10T14:00:00Z' },
      { event_id: 'us-later', league_name: ` ${leagueName.toLowerCase()} `, event_name: 'U.S. later fixture', start_at: '2026-10-12T16:00:00Z', status_code: 'NS', status: 'Not Started', synced_at: '2026-10-10T14:00:00Z' },
      { event_id: 'us-earlier', league_name: leagueName, event_name: 'U.S. earlier fixture', start_at: '2026-10-11T16:00:00Z', status_code: 'NS', status: 'Not Started', synced_at: '2026-10-10T14:00:00Z' },
    ] }))
    await page.goto('/')
    const board = page.getByRole('region', { name: 'Sports schedule and scores' })
    await board.getByRole('button', { name: sportName, exact: true }).click()
    await expect(board.getByRole('article').getByRole('heading')).toHaveText(['U.S. earlier fixture', 'U.S. later fixture', 'International fixture'])
  })
}

test('U.S. priority preserves event groups and never duplicates an event starting now', async ({ page }) => {
  await mockSports(page)
  const fixture = (id: string, league: string, date: string, status = 'NS') => ({
    event_id: id, league_name: league, event_name: id, start_at: date,
    status_code: status, status, synced_at: '2026-10-10T14:00:00Z',
  })
  await page.route('**/rest/v1/rpc/public_sport_events*', (route) => route.fulfill({ json: [
    fixture('International live', 'International League', '2026-10-10T14:00:00Z'),
    fixture('U.S. starting now', 'MLB', '2026-10-10T15:00:00Z'),
    fixture('International upcoming', 'International League', '2026-10-10T16:00:00Z'),
    fixture('U.S. upcoming', 'MLB', '2026-10-11T16:00:00Z'),
    fixture('U.S. recent', 'MLB', '2026-10-09T16:00:00Z', 'FT'),
  ] }))
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await board.getByRole('button', { name: 'Baseball', exact: true }).click()
  await expect(board.getByRole('article').getByRole('heading')).toHaveText(['U.S. starting now', 'International live', 'U.S. upcoming'])
})

test('recent U.S. events come first, with newest events first within each priority', async ({ page }) => {
  await mockSports(page)
  const fixture = (id: string, league: string, day: string) => ({
    event_id: id, league_name: league, event_name: id, start_at: `2026-10-${day}T16:00:00Z`,
    status_code: 'FT', status: 'Finished', synced_at: '2026-10-10T14:00:00Z',
  })
  await page.route('**/rest/v1/rpc/public_sport_events*', (route) => route.fulfill({ json: [
    fixture('International newest', 'National League', '09'),
    fixture('U.S. older', 'NHL', '07'),
    fixture('U.S. newer', 'NHL', '08'),
    fixture('International older', 'National League', '06'),
  ] }))
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await board.getByRole('button', { name: 'Hockey', exact: true }).click()
  await expect(board.getByRole('article').getByRole('heading')).toHaveText(['U.S. newer', 'U.S. older', 'International newest'])
})

test('MMA shows named fighters and placeholder matchups are omitted from all sport views', async ({ page }) => {
  await mockSports(page)
  await page.route('**/rest/v1/rpc/public_sport_events*', async (route) => {
    const slug = route.request().postDataJSON().p_sport_slug
    const fixture = (eventName: string, index: number, status = 'NS') => ({
      event_id: String(index), league_name: slug === 'mma' ? 'UFC 332' : 'League', event_name: eventName,
      start_at: '2026-10-10T16:00:00Z', status_code: status, status,
      home_name: null, away_name: null, synced_at: '2026-10-10T14:00:00Z',
    })
    await route.fulfill({ json: [
      fixture('Home vs Away', 1), fixture(' HOME vs. AWAY ', 2), fixture('Home Team versus Away Team', 3),
      fixture('TBD vs Real Club', 4), fixture('Home vs Named Club', 5, 'FT'),
      { ...fixture('Incorrect named fixture', 6), home_name: 'Home', away_name: 'Away' },
      slug === 'mma'
        ? { ...fixture('Ismail Naurdiev vs Marvin Vettori', 7), home_name: 'Ismail Naurdiev', away_name: 'Marvin Vettori' }
        : fixture('Named Club vs Visiting Club', 7),
    ] })
  })
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await board.getByRole('button', { name: 'MMA', exact: true }).click()
  await expect(board.getByRole('article')).toHaveCount(1)
  await expect(board.getByRole('article')).toContainText('Ismail Naurdiev vs Marvin Vettori')
  await board.getByRole('link', { name: 'Open MMA center' }).click()
  await expect(page.locator('.sport-event-card')).toHaveCount(1)
  await expect(page.locator('.sport-event-card')).toContainText('Ismail Naurdiev vs Marvin Vettori')
  await page.goto('/sports/basketball')
  await expect(page.locator('.sport-event-card')).toHaveCount(1)
  await expect(page.locator('.sport-event-card')).toContainText('Named Club vs Visiting Club')
  await page.getByRole('button', { name: 'Scores', exact: true }).click()
  await expect(page.locator('.sport-event-card')).toHaveCount(0)
})

test('placeholder rows do not stop pagination and NFL placeholders are also omitted', async ({ page }) => {
  await mockSports(page)
  await page.route('**/rest/v1/rpc/public_sport_events*', async (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0)
    const fixture = (name: string, id: number) => ({
      event_id: String(id), league_name: 'League', event_name: name, start_at: '2026-10-10T16:00:00Z',
      status_code: 'NS', status: 'Not Started', synced_at: '2026-10-10T14:00:00Z',
    })
    await route.fulfill({ json: offset === 0
      ? Array.from({ length: 1000 }, (_, index) => fixture('Home vs Away', index))
      : [fixture('Real Club vs Other Club', 1000)] })
  })
  await page.route('**/rest/v1/rpc/public_nfl_games*', (route) => route.fulfill({ json: [{
    game_id: 1, home_team_name: 'Home', away_team_name: 'Away', kickoff_at: '2026-10-10T16:00:00Z',
    status_short: 'NS', status_long: 'Not Started', season: 2026,
  }] }))
  await page.goto('/')
  const board = page.getByRole('region', { name: 'Sports schedule and scores' })
  await expect(board.getByRole('article')).toHaveCount(1)
  await expect(board.getByRole('article')).toContainText('Real Club vs Other Club')
  await board.getByRole('button', { name: 'NFL', exact: true }).click()
  await expect(board.getByRole('article')).toHaveCount(0)
  await expect(board).toContainText('No current NFL events')
  await page.goto('/nfl')
  await expect(page.locator('.nfl-game-card')).toHaveCount(0)
})
