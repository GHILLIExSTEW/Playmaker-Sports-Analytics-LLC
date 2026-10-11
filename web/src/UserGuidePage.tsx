import { useEffect, useState } from 'react'
import { supabase } from './supabaseClient'

export default function UserGuidePage() {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 10_000)
    void (async () => {
      try {
        const { data, error } = await supabase.rpc('operator_user_guide').abortSignal(controller.signal)
        if (error) throw error
        if (typeof data !== 'string' || !data.trim()) throw new Error('The user guide response was invalid.')
        if (!cancelled) setContent(data)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message
          : cause && typeof cause === 'object' && 'message' in cause ? String(cause.message)
            : 'The user guide could not be loaded. Retry or contact support.')
      } finally {
        window.clearTimeout(timeout)
      }
    })()
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [revision])

  if (error) return <section className="user-guide-page">
    <p role="alert">User guide unavailable: {error}</p>
    <button className="account-secondary-button" onClick={() => { setError(''); setContent(null); setRevision((value) => value + 1) }}>Retry guide</button>
  </section>
  if (!content) return <p className="account-state" role="status">Loading user guide...</p>
  return <section className="user-guide-page">
    <p className="eyebrow">OPERATOR / Owner guide</p>
    {/* The protected RPC returns repository-owned HTML, never member-supplied content. */}
    <article className="user-guide-content" aria-label="Playmaker user guide" dangerouslySetInnerHTML={{ __html: content }} />
  </section>
}
