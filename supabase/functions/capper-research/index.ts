import { createResearchHandler } from './handler.ts'
import { pullTeamData } from './pull.ts'

const url = Deno.env.get('SUPABASE_URL')
const anon = Deno.env.get('SUPABASE_ANON_KEY')
if (!url || !anon) throw new Error('Supabase configuration is required.')
Deno.serve(createResearchHandler({
  apiKey: Deno.env.get('OPENAI_API_KEY') ?? '',
  model: Deno.env.get('OPENAI_RESEARCH_MODEL') ?? 'gpt-4.1-mini',
  fetch,
  log: event => console.error(event),
  pull: (body, rpc) => pullTeamData(body, rpc, {
    providerKey: Deno.env.get('API_SPORTS_KEY') ?? '',
    fetch,
    log: event => console.error(event),
    serviceRpc: async (name, args) => {
      const serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')
      if (!serviceKey) throw new Error('Service configuration unavailable')
      const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
        method: 'POST', headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(args), signal: AbortSignal.timeout(10_000), redirect: 'error',
      })
      if (!response.ok) throw new Error('Pull persistence unavailable')
      return await response.json()
    },
  }),
  authorize: async token => {
    const headers = { apikey: anon, Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const user = await fetch(`${url}/auth/v1/user`, { headers, signal: AbortSignal.timeout(10_000), redirect: 'error' })
    if (user.status === 401 || user.status === 403) return null
    if (!user.ok) throw new Error('Auth unavailable')
    return async (name, args) => {
      const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
        method: 'POST', headers, body: JSON.stringify(args), signal: AbortSignal.timeout(10_000), redirect: 'error',
      })
      const result = await response.json()
      if (!response.ok) throw result
      return result
    }
  },
}))
