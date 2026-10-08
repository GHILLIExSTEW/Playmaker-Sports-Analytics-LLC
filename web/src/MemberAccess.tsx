import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from './supabaseClient'
import { MemberAccessContext, useMemberAccess, type MemberAccess, type AccessSnapshot } from './memberAccessContext'

function parseAccess(value: unknown): MemberAccess {
  if (!value || typeof value !== 'object' || !('state' in value)) throw new Error('Unexpected membership response.')
  if (value.state === 'active') {
    if (!('kind' in value) || !['paid', 'trial', 'owner'].includes(String(value.kind))
      || !('expires_at' in value) || (value.expires_at !== null
        && (typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at))))) {
      throw new Error('Unexpected membership response.')
    }
    if (value.kind !== 'paid' && value.kind !== 'trial' && value.kind !== 'owner') throw new Error('Unexpected membership kind.')
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
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)
  const refresh = useCallback(() => {
    generation.current += 1
    setSnapshot({ access: null, error: '' })
    setRevision(generation.current)
  }, [])

  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange(refresh)
    const timer = window.setInterval(refresh, 30_000)
    window.addEventListener('focus', refresh)
    return () => {
      subscription.unsubscribe()
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
    }
  }, [refresh])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const { data, error } = await supabase.rpc('website_member_access')
        if (error) throw error
        const access = parseAccess(data)
        if (!cancelled && generation.current === revision) setSnapshot({ access, error: '' })
      } catch {
        if (!cancelled && generation.current === revision) setSnapshot({ access: null, error: 'Membership access could not be verified. Retry or contact support.' })
      }
    })()
    return () => { cancelled = true }
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
    case 'launch-pending': return <p className="account-state">Website premium access is awaiting launch validation. Checkout remains closed. <Link to="/membership">View membership details</Link>.</p>
    case 'membership-required': return <p className="account-state">No current verified HIGHROLLER access was found. Use the same Discord account linked in Whop. Paid passes and eligible trials need a fresh membership sync; roles alone do not grant website access. <Link to="/membership">Membership details</Link> or <a href="mailto:support@playmakersportsanalytics.com">contact support</a>.</p>
    case 'active': return <p className="account-state">HIGHROLLER {access.kind === 'owner' ? 'owner access' : access.kind === 'trial' ? 'trial access' : 'paid access'}{access.expires_at ? ` through ${new Date(access.expires_at).toLocaleString()}` : ''}. <Link to="/picks">Browse current picks</Link>.</p>
  }
}
