import { createResearchHandler } from './handler.ts'

const url = Deno.env.get('SUPABASE_URL')
const anon = Deno.env.get('SUPABASE_ANON_KEY')
if (!url || !anon) throw new Error('Supabase configuration is required.')
Deno.serve(createResearchHandler({
  apiKey: Deno.env.get('OPENAI_API_KEY') ?? '',
  model: Deno.env.get('OPENAI_RESEARCH_MODEL') ?? 'gpt-4.1-mini',
  fetch,
  log: event => console.error(event),
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
