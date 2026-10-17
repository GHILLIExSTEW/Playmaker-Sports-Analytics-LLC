import assert from 'node:assert/strict'
import test from 'node:test'
import { createHandler } from '../../supabase/functions/capper-api/handler.ts'

const token = `pmcap_${'a'.repeat(64)}`
const request = (body = { product: 'american-football', path: '/games', params: { league: 1 } }, key = token) =>
  new Request('https://example.com/functions/v1/capper-api', {
    method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
function harness({ reservation = { state: 'reserved', request_id: 'request-1' }, provider, finish = true, failRpc = false } = {}) {
  const calls = [], fetched = [], logs = []
  const handle = createHandler({
    providerKey: 'private-provider-key', log: (...values) => logs.push(values),
    rpc: async (name, args) => {
      calls.push({ name, args })
      if (failRpc) throw new Error('Internal details must never be returned')
      return name === 'reserve_capper_api_call' ? reservation : finish
    },
    fetch: async (url, options) => {
      fetched.push({ url: String(url), options })
      return provider ? provider(url, options) : Response.json({ errors: [], response: [{ id: 1 }] })
    },
  })
  return { handle, calls, fetched, logs }
}
test('private key is hashed and exactly one allowlisted provider GET is audited', async () => {
  const h = harness()
  const response = await h.handle(request())
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('cache-control'), 'no-store')
  const body = await response.json()
  assert.equal(body.request_id, 'request-1')
  assert.equal(body.provider_status, 200)
  assert.deepEqual(body.data.response, [{ id: 1 }])
  assert.equal(h.fetched.length, 1)
  assert.equal(h.fetched[0].url, 'https://v1.american-football.api-sports.io/games?league=1')
  assert.equal(h.fetched[0].options.headers['x-apisports-key'], 'private-provider-key')
  assert.equal(h.fetched[0].options.method, 'GET')
  assert.equal(h.fetched[0].options.redirect, 'error')
  assert.ok(h.fetched[0].options.signal instanceof AbortSignal)
  assert.equal(h.calls[0].name, 'reserve_capper_api_call')
  assert.match(h.calls[0].args.p_key_hash, /^[a-f0-9]{64}$/)
  assert.equal(JSON.stringify(h.calls).includes(token), false)
  assert.deepEqual(h.calls[1], { name: 'finish_capper_api_call', args: { p_request_id: 'request-1', p_status: 200 } })
  assert.equal(JSON.stringify(body).includes('private-provider-key'), false)
})
test('invalid keys, hosts, paths, parameters and oversized bodies cannot reach budget or provider', async () => {
  for (const req of [
    request(undefined, 'bad'), request({ product: 'constructor', path: '/games' }),
    request({ product: 'football', path: 'https://evil.example/games' }),
    request({ product: 'football', path: '//evil.example/games' }),
    request({ product: 'football', path: '/games/../status' }),
    request({ product: 'football', path: '/status' }),
    request({ product: 'football', path: '/status/anything' }),
    request({ product: 'football', path: '/games?key=secret' }),
    request({ product: 'football', path: '/games', params: { nested: {} } }),
    request({ product: 'football', path: '/games', params: { huge: 'x'.repeat(257) } }),
    request({ product: 'football', path: '/games', method: 'POST' }),
    request({ product: 'football', path: '/games', params: Object.fromEntries(Array.from({ length: 21 }, (_, i) => [`p${i}`, 1])) }),
    request({ product: 'football', path: '/games', extra: 'x'.repeat(17_000) }),
    new Request('https://example.com', { method: 'GET' }),
    new Request('https://example.com', { method: 'POST', body: 'no content type' }),
    new Request('https://example.com', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: '{' }),
  ]) {
    const h = harness()
    assert.ok((await h.handle(req)).status >= 400)
    assert.equal(h.calls.length, 0)
    assert.equal(h.fetched.length, 0)
  }
})
test('invalid, revoked, exhausted and unavailable reservations fail closed', async () => {
  for (const [options, status] of [
    [{ reservation: { state: 'unauthorized' } }, 401],
    [{ reservation: { state: 'denied', reason: 'Shared daily allowance exhausted' } }, 429],
    [{ reservation: null }, 503], [{ reservation: {} }, 503], [{ failRpc: true }, 503],
  ]) {
    const h = harness(options)
    const response = await h.handle(request())
    assert.equal(response.status, status)
    assert.equal(h.fetched.length, 0)
    assert.equal((await response.text()).includes('Internal details'), false)
  }
})
test('provider errors are explicit, audited and never retried or refunded', async () => {
  for (const [provider, expected, recorded] of [
    [() => Response.json({ errors: { plan: 'Not covered' } }), 502, 200],
    [() => Response.json({ errors: { rate: 'Too many requests' } }, { status: 429 }), 429, 429],
    [() => Response.json({ errors: { missing: 'Not found' } }, { status: 404 }), 404, 404],
    [() => { throw new Error('network timeout') }, 502, 0],
    [() => new Response('not JSON'), 502, 200],
    [() => Response.json('not an API object'), 502, 200],
    [() => new Response('x'.repeat(2_097_153)), 502, 200],
  ]) {
    const h = harness({ provider })
    const response = await h.handle(request())
    assert.equal(response.status, expected)
    assert.ok((await response.json()).error)
    assert.equal(h.fetched.length, 1)
    assert.equal(h.calls.length, 2)
    assert.equal(h.calls[1].args.p_status, recorded)
  }
})
test('audit failure after provider execution never returns success', async () => {
  const h = harness({ finish: false })
  const response = await h.handle(request())
  assert.equal(response.status, 503)
  const body = await response.json()
  assert.equal(body.request_id, 'request-1')
  assert.match(body.error, /allowance was consumed/)
  assert.equal(h.fetched.length, 1)
})
