import { createHandler } from './handler.ts'

const url = Deno.env.get('SUPABASE_URL')
const serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')
const providerKey = Deno.env.get('API_SPORTS_KEY')
if (!url || !serviceKey || !providerKey) throw new Error('Capper API requires Supabase service credentials and API_SPORTS_KEY.')

Deno.serve(createHandler({
  providerKey,
  fetch,
  log: (event, requestId) => console.error(event, requestId ?? ''),
  rpc: async (name, args) => {
    const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
      method: 'POST', headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(args), signal: AbortSignal.timeout(10_000), redirect: 'error',
    })
    if (!response.ok) throw new Error(`Capper API RPC failed: ${response.status}`)
    return await response.json()
  },
}))
