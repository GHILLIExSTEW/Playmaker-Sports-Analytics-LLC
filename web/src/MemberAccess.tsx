import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from './supabaseClient'
import { MemberAccessContext, useMemberAccess, type MemberAccess, type AccessSnapshot } from './memberAccessContext'

function parseAccess(value: unknown): MemberAccess {
  if (!value || typeof value !== 'object' || !('state' in value)) throw new Error('Unexpected membership response.')
  if (value.state === 'active') {
    if (!('kind' in value) || !['paid', 'trial', 'owner', 'operator', 'research'].includes(String(value.kind))
      || !('expires_at' in value) || (value.expires_at !== null
        && (typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at))))) {
      throw new Error('Unexpected membership response.')
    }
    if (value.kind !== 'paid' && value.kind !== 'trial' && value.kind !== 'owner' && value.kind !== 'operator' && value.kind !== 'research') throw new Error('Unexpected membership kind.')
    return { state: 'active', kind: value.kind, expires_at: value.expires_at }
  }
  switch (value.state) {
    case 'signed-out': case 'age-required': case 'discord-required': case 'launch-pending': case 'membership-required':
      return { state: value.state }
    default: throw new Error('Unexpected membership state.')
  }
}

export function MemberAccessProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<AccessSnapshot>({ access: null, error: '' })
  const [revision, setRevision] = useState<number | null>(null)
  const generation = useRef(0)
  const identity = useRef<string | null>(null)
  const storageKey = 'playmaker-tab-access'
  const refresh = useCallback(() => {
    generation.current += 1
    sessionStorage.removeItem(storageKey)
    setSnapshot({ access: null, error: '' })
    setRevision(generation.current)
  }, [])

  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      const user = session?.user.id ?? 'signed-out'
      if (event === 'INITIAL_SESSION' && identity.current === null) {
        identity.current = user
        const saved = sessionStorage.getItem(storageKey)
        if (saved) {
          try {
            const cached = JSON.parse(saved)
            const access = parseAccess(cached.access)
            if (cached.user === user && access.state === 'active'
              && (!access.expires_at || Date.parse(access.expires_at) > Date.now())) {
              setSnapshot({ access, error: '' })
              return
            }
          } catch {
            // Invalid session cache must be reverified against the server.
          }
        }
        refresh()
      } else if (user !== identity.current || event === 'SIGNED_OUT' || event === 'USER_UPDATED') {
        identity.current = user
        refresh()
      }
    })
    return () => subscription.unsubscribe()
  }, [refresh])

  useEffect(() => {
    if (revision === null) return
    let cancelled = false
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 10_000)
    void (async () => {
      try {
        const { data, error } = await supabase.rpc('website_member_access').abortSignal(controller.signal)
        if (error) throw error
        const access = parseAccess(data)
        if (!cancelled && generation.current === revision) {
          if (access.state === 'active') sessionStorage.setItem(storageKey, JSON.stringify({ user: identity.current, access }))
          setSnapshot({ access, error: '' })
        }
      } catch {
        if (!cancelled && generation.current === revision) setSnapshot({ access: null, error: 'Membership access could not be verified. Retry or contact support.' })
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

  const expiresAt = snapshot.access?.state === 'active' ? snapshot.access.expires_at : null
  useEffect(() => {
    if (!expiresAt) return
    const timer = window.setTimeout(refresh, Math.max(0, Math.min(Date.parse(expiresAt) - Date.now(), 2_147_483_647)))
    return () => window.clearTimeout(timer)
  }, [expiresAt, refresh])

  return <MemberAccessContext.Provider value={{ ...snapshot, refresh }}>{children}</MemberAccessContext.Provider>
}

export function MemberAccessNotice() {
  const { access, error, refresh } = useMemberAccess()
  if (error) return <div className="account-state"><p role="alert">{error}</p><button className="account-secondary-button" onClick={refresh}>Retry access check</button></div>
  if (!access) return <p className="account-state" role="status">Checking member access...</p>
  switch (access.state) {
    case 'signed-out': return <p className="account-state">Current picks are for HIGHROLLER members. <Link to="/account">Sign in with Discord</Link> to check access.</p>
    case 'age-required': return <p className="account-state"><Link to="/account">Complete age verification</Link> before accessing current picks. Members must be 21+ or the higher local legal age.</p>
    case 'discord-required': return <p className="account-state">A verified Discord sign-in is required. <Link to="/account">Sign out and continue with Discord</Link> using the account linked to your Whop membership.</p>
    case 'launch-pending': return <p className="account-state">Membership is live, but website premium access is currently unavailable. <button className="account-secondary-button" onClick={refresh}>Retry access check</button> or <a href="mailto:support@playmakersportsanalytics.com">contact support</a>. <Link to="/membership">View membership details</Link>.</p>
    case 'membership-required': return <p className="account-state">No current verified HIGHROLLER access was found. Use the same Discord account linked in Whop. Subscriptions and eligible trials need a fresh membership sync. OPERATOR picks access requires the bot to verify your role membership. <Link to="/membership">Join HIGHROLLER or view membership details</Link> or <a href="mailto:support@playmakersportsanalytics.com">contact support</a>.</p>
    case 'active': return <p className="account-state">{access.kind === 'research' ? 'Complimentary member and research access by approved exception' : access.kind === 'operator' ? 'HIGHROLLER team access — complimentary, with no scheduled expiry while you hold the OPERATOR role' : `HIGHROLLER ${access.kind === 'owner' ? 'owner access' : access.kind === 'trial' ? 'trial access' : 'paid access'}`}{access.expires_at ? ` through ${new Date(access.expires_at).toLocaleString()}` : ''}. <Link to="/picks">Browse current picks</Link>.</p>
  }
}

export function OperatorResearchAccess({ children }: { children: ReactNode }) {
  const { access, error, refresh } = useMemberAccess()
  if (error) return <main className="account-state"><p role="alert">{error}</p><button className="account-secondary-button" onClick={refresh}>Retry access check</button></main>
  if (!access) return <main className="account-state"><p role="status">Checking research access...</p></main>
  if (access.state === 'active' && (access.kind === 'operator' || access.kind === 'owner' || access.kind === 'research')) return children
  return <main className="account-state">
    <h1>Team research access required.</h1>
    <p>Sports insights, Data Models and the user guide require a verified Discord OPERATOR or Owner role, or an approved research exception. Paid and trial membership do not include these tools.</p>
    <Link to="/account">Sign in with Discord or verify your account</Link>
    <button className="account-secondary-button" onClick={refresh}>Retry access check</button>
  </main>
}
