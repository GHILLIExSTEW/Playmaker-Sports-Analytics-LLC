import assert from 'node:assert/strict'
import test from 'node:test'
import { createResearchHandler } from '../../supabase/functions/capper-research/handler.ts'

const body = { action: 'analyze', competition: 'nfl', game_id: '1', notes: 'My line +3.5', report_date: '' }
const req = (value = body, origin = 'https://playmakersportsanalytics.com', token = 'user-jwt') => new Request('https://example.com', {
  method: 'POST', headers: { 'content-type': 'application/json', origin, authorization: `Bearer ${token}` }, body: JSON.stringify(value),
})
test('team pulls require a verified session and dispatch independently from AI actions', async () => {
  let pulls = 0
  const deps = { apiKey: '', model: 'unused', log: () => {}, fetch: async () => { throw new Error('AI must not run') },
    authorize: async () => async () => { throw new Error('AI RPC must not run') },
    pull: async (body) => { pulls++; assert.equal(body.team_id, '2'); return { status: 200, body: { message: 'Saved' } } } }
  assert.equal((await createResearchHandler(deps)(req({ action: 'pull', competition: 'nfl', game_id: '1', team_id: '2' }))).status, 200)
  assert.equal((await createResearchHandler({ ...deps, authorize: async () => null })(req({ action: 'pull' }))).status, 401)
  assert.equal(pulls, 1)
})
function harness({ auth = true, rejectContext = false, rejectBudget = false, providerStatus = 200, incomplete = false, apiKey = 'private-ai-key', projection = null } = {}) {
  const calls = [], provider = [], logs = []
  const handle = createResearchHandler({
    apiKey, model: 'gpt-4.1-mini', log: event => logs.push(event),
    authorize: async () => auth ? async (name, args) => {
      calls.push({ name, args })
      if (name === 'capper_tools_game') {
        if (rejectContext) throw { code: '42501' }
        return { game: { start_at: '2026-10-18T12:00Z', home_name: 'Verified team' }, history: [],
          players: [{ start_at: '2026-10-19T12:00Z', secret_future_stat: 999 }, { start_at: '2026-10-17T12:00Z', payload: [] }],
          injuries: [], weather: null, projection, odds: { home_probability: 0.569, home_spread: -2.5, total: 42.5, books: 2, fetched_at: '2026-10-17T12:00Z' } }
      }
      if (rejectBudget) throw { code: 'P0001', message: 'Daily AI allowance reached' }
      return 'request-1'
    } : null,
    fetch: async (url, options) => {
      provider.push({ url, options })
      return Response.json({ status: incomplete ? 'incomplete' : 'completed', output: [{ content: [{ type: 'output_text', text: 'Grounded breakdown.' }] }] }, { status: providerStatus })
    },
  })
  return { handle, calls, provider, logs }
}
test('research verifies session, approval, server evidence and allowance before exactly one AI request', async () => {
  const h = harness(), response = await h.handle(req())
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('access-control-allow-origin'), 'https://playmakersportsanalytics.com')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(h.calls.map(c => c.name), ['capper_tools_game', 'reserve_capper_tools_ai'])
  const request = JSON.parse(h.provider[0].options.body)
  assert.equal(request.store, false)
  assert.equal(request.max_output_tokens, 1800)
  assert.ok(request.input[0].content[0].text.includes('Verified team'))
  assert.ok(!request.input[0].content[0].text.includes('secret_future_stat'))
  assert.ok(request.input[0].content[0].text.includes('user_supplied_unverified'))
  assert.ok(!request.input[0].content[0].text.includes('home_probability'))
  assert.ok(request.instructions.includes('home designation does NOT verify the game venue'))
  assert.equal((await response.json()).text, 'Grounded breakdown.')
  assert.equal(h.provider.length, 1)
})
test('unapproved, signed-out, invalid origin and malformed inputs never reach OpenAI', async () => {
  for (const options of [{ auth: false }, { rejectContext: true }, { rejectBudget: true }, { apiKey: '' }]) {
    const h = harness(options), response = await h.handle(req())
    assert.ok(response.status >= 400)
    assert.equal(h.provider.length, 0)
  }
  for (const request of [req(body, 'https://evil.example'), req({ ...body, competition: 'bad' }),
    req({ ...body, notes: 'x'.repeat(6001) }), req({ ...body, action: 'extract', image: 'data:image/png;base64,YmFk' }),
    req({ ...body, action: 'analyze', image: 'unexpected' })]) {
    const h = harness(), response = await h.handle(request)
    assert.ok(response.status >= 400)
    assert.equal(h.provider.length, 0)
    assert.equal(h.calls.length, 0)
  }
})
test('valid screenshot is sent only for extraction with no persistence requested', async () => {
  const h = harness(), image = `data:image/png;base64,${Buffer.from('\x89PNG\r\n\x1a\n', 'binary').toString('base64')}`
  const response = await h.handle(req({ ...body, action: 'extract', image }))
  assert.equal(response.status, 200)
  const value = JSON.parse(h.provider[0].options.body)
  assert.equal(value.input[0].content[1].image_url, image)
  assert.equal(value.store, false)
  assert.ok(!JSON.stringify(h.calls).includes(image))
})
test('AI errors and incomplete responses stay explicit and never retry', async () => {
  for (const options of [{ providerStatus: 429 }, { providerStatus: 500 }, { incomplete: true }]) {
    const h = harness(options), response = await h.handle(req())
    assert.ok(response.status >= 400)
    assert.equal(h.provider.length, 1)
    const value = await response.json()
    assert.equal(value.request_id, 'request-1')
    assert.ok(value.error)
    assert.ok(!JSON.stringify(value).includes('private-ai-key'))
  }
})
test('AI receives only permitted model estimates and never raw model internals or market win probabilities', async () => {
  const h = harness({ projection: { home_score: 27.4, away_score: 21.2, home_probability: .64,
    model_version: 'ratings-v2', generated_at: '2026-10-17T12:00Z', data_updated_at: '2026-10-17T11:00Z',
    details: { secret_model_parameter: 99 }, market_edge: .1 } })
  assert.equal((await h.handle(req())).status, 200)
  const request = JSON.parse(h.provider[0].options.body)
  const source = request.input[0].content[0].text
  assert.ok(source.includes('"home_probability":0.64'))
  assert.ok(!source.includes('0.569'))
  assert.ok(!source.includes('secret_model_parameter'))
  assert.ok(!source.includes('market_edge'))
  assert.ok(request.instructions.includes('queue recalculation using the updated cache'))
  assert.ok(request.instructions.includes('NHL starting goalies are unknown'))
})
