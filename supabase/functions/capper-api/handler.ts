type Dependencies = {
  rpc: (name: string, args: Record<string, unknown>) => Promise<unknown>
  fetch: typeof fetch
  providerKey: string
  log: (event: string, requestId?: string) => void
}
const hosts: Record<string, string> = {
  'american-football': 'https://v1.american-football.api-sports.io',
  basketball: 'https://v1.basketball.api-sports.io',
  football: 'https://v3.football.api-sports.io',
  hockey: 'https://v1.hockey.api-sports.io',
  baseball: 'https://v1.baseball.api-sports.io',
  rugby: 'https://v1.rugby.api-sports.io',
  handball: 'https://v1.handball.api-sports.io',
  volleyball: 'https://v1.volleyball.api-sports.io',
  mma: 'https://v1.mma.api-sports.io',
  'formula-1': 'https://v1.formula-1.api-sports.io',
}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const json = (status: number, body: Record<string, unknown>) => Response.json(body, {
  status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
})
async function readJson(body: ReadableStream<Uint8Array> | null, limit: number): Promise<unknown> {
  if (!body) throw new Error('Empty body')
  const reader = body.getReader()
  let size = 0
  const chunks: Uint8Array[] = []
  try {
    for (;;) {
      const part = await reader.read()
      if (part.done) break
      size += part.value.byteLength
      if (size > limit) { await reader.cancel(); throw new Error('Body too large') }
      chunks.push(part.value)
    }
  } finally { reader.releaseLock() }
  const bytes = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
  return JSON.parse(new TextDecoder().decode(bytes))
}
export function createHandler(deps: Dependencies) {
  return async (request: Request): Promise<Response> => {
    if (request.method !== 'POST') return json(405, { error: 'Use POST with a JSON request body.' })
    if (!request.headers.get('content-type')?.toLowerCase().startsWith('application/json')) {
      return json(415, { error: 'Content-Type must be application/json.' })
    }
    const token = request.headers.get('authorization')?.match(/^Bearer (pmcap_[a-f0-9]{64})$/)?.[1]
    if (!token) return json(401, { error: 'A private capper API key is required.' })
    let body: unknown
    try { body = await readJson(request.body, 16_384) }
    catch { return json(400, { error: 'Supply valid JSON no larger than 16 KiB.' }) }
    if (!object(body) || Object.keys(body).some(key => !['product', 'path', 'params'].includes(key))
      || typeof body.product !== 'string' || !Object.hasOwn(hosts, body.product)
      || typeof body.path !== 'string' || !/^\/[a-z0-9-]+(?:\/[a-z0-9-]+)*$/.test(body.path)
      || body.path.length > 128 || body.path.split('/')[1] === 'status'
      || (body.params !== undefined && !object(body.params))) {
      return json(400, { error: 'Use a supported product, relative data path and scalar query parameters.' })
    }
    const params = body.params ?? {}
    if (!object(params) || Object.keys(params).length > 20 || Object.entries(params).some(([key, value]) =>
      !/^[a-zA-Z0-9_-]{1,64}$/.test(key) || !['string', 'number', 'boolean'].includes(typeof value)
      || (typeof value === 'number' && !Number.isFinite(value)) || String(value).length > 256)) {
      return json(400, { error: 'Use at most 20 scalar parameters of at most 256 characters each.' })
    }
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
    const keyHash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
    let reservation: unknown
    try {
      reservation = await deps.rpc('reserve_capper_api_call', {
        p_key_hash: keyHash, p_product: body.product, p_path: body.path,
      })
    } catch {
      deps.log('capper_api_reservation_failed')
      return json(503, { error: 'API authorization or budget service unavailable. No provider call was made.' })
    }
    if (!object(reservation)) return json(503, { error: 'Invalid API reservation response. No provider call was made.' })
    if (reservation.state === 'unauthorized') return json(401, { error: 'API key is invalid, revoked or no longer eligible.' })
    if (reservation.state === 'denied') return json(429, { error: String(reservation.reason ?? 'Shared daily API allowance exhausted.') })
    if (reservation.state !== 'reserved' || typeof reservation.request_id !== 'string') {
      deps.log('capper_api_invalid_reservation')
      return json(503, { error: 'Invalid API reservation response. No provider call was made.' })
    }
    const requestId = reservation.request_id
    const url = new URL(body.path, hosts[body.product])
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value))
    let status = 502, providerStatus = 0, payload: unknown = null
    let error = ''
    try {
      const response = await deps.fetch(url, {
        method: 'GET', headers: { 'x-apisports-key': deps.providerKey },
        redirect: 'error', signal: AbortSignal.timeout(20_000),
      })
      providerStatus = response.status
      payload = await readJson(response.body, 2_097_152)
      if (!object(payload)) throw new Error('Invalid provider response')
      status = response.ok ? 200 : response.status >= 400 && response.status <= 599 ? response.status : 502
      if (!response.ok) error = 'Provider rejected the request.'
      else if (payload.errors !== undefined && (!object(payload.errors) && !Array.isArray(payload.errors)
        || Object.keys(payload.errors).length > 0)) {
        status = 502; error = 'Provider reported an API error.'
      }
    } catch {
      error = 'Provider request failed, timed out, redirected or returned an invalid/oversized response. Its outcome may be uncertain; do not retry automatically.'
      deps.log('capper_api_provider_failed', requestId)
    }
    try {
      const finished = await deps.rpc('finish_capper_api_call', { p_request_id: requestId, p_status: providerStatus })
      if (finished !== true) throw new Error('Completion not recorded')
    } catch {
      deps.log('capper_api_audit_failed', requestId)
      return json(503, { request_id: requestId, error: 'Provider call was attempted, but its audit could not be completed. The allowance was consumed; do not retry automatically.' })
    }
    return json(status, { request_id: requestId, product: body.product, path: body.path,
      provider_status: providerStatus, ...(error ? { error } : {}), data: payload })
  }
}
