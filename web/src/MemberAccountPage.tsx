import { useEffect, useState, type FormEvent } from 'react'
import type { Session } from '@supabase/supabase-js'
import { ArrowRight, Copy, Download, LogOut, ShieldCheck, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from './supabaseClient'
import { MemberAccessNotice } from './MemberAccess'
import { useMemberAccess } from './memberAccessContext'

type SportOption = { id: number; api_slug: string; name: string }
type CapperOption = { sport_id: number; sport: string; capper_name: string }
type MemberProfile = {
  age_verified_at: string | null
  created_at: string
  display_name: string
  public_handle: string
  avatar_url: string | null
  public_profile_enabled: boolean
  timezone: string
  discord_alerts_enabled: boolean
  email_alerts_enabled: boolean
}
type FavoriteSport = { sport_id: number }
type FavoriteCapper = { sport_id: number; capper_name: string }
type PageState = 'loading' | 'ready' | 'error'

function capperKey(sportId: number, capperName: string): string {
  return `${sportId}::${capperName}`
}

export default function MemberAccountPage() {
  const navigate = useNavigate()
  const { access, refresh: refreshAccess } = useMemberAccess()
  const [session, setSession] = useState<Session | null>(null)
  const [authReady, setAuthReady] = useState(false)
  const [profile, setProfile] = useState<MemberProfile | null>(null)
  const [sports, setSports] = useState<SportOption[]>([])
  const [cappers, setCappers] = useState<CapperOption[]>([])
  const [favoriteSportIds, setFavoriteSportIds] = useState<number[]>([])
  const [favoriteCapperKeys, setFavoriteCapperKeys] = useState<string[]>([])
  const [displayName, setDisplayName] = useState('')
  const [publicHandle, setPublicHandle] = useState('')
  const [avatarUrl, setAvatarUrl] = useState('')
  const [publicProfileEnabled, setPublicProfileEnabled] = useState(false)
  const [timezone, setTimezone] = useState('America/New_York')
  const [discordAlertsEnabled, setDiscordAlertsEnabled] = useState(false)
  const [emailAlertsEnabled, setEmailAlertsEnabled] = useState(false)
  const [birthDate, setBirthDate] = useState('')
  const [pageState, setPageState] = useState<PageState>('loading')
  const [busyKey, setBusyKey] = useState('')
  const [message, setMessage] = useState('')
  const [retryCount, setRetryCount] = useState(0)
  const signedInUserId = session?.user.id
  const sessionMetadata = session?.user.user_metadata

  useEffect(() => {
    let cancelled = false
    supabase.auth.getSession().then(({ data, error }) => {
      if (cancelled) return
      if (error) setMessage('Could not check your sign-in session. Try again.')
      setSession(data.session)
      setAuthReady(true)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      if (!nextSession) {
        setProfile(null)
        setSports([])
        setCappers([])
        setFavoriteSportIds([])
        setFavoriteCapperKeys([])
        setPageState('ready')
      } else {
        setPageState('loading')
      }
    })
    return () => {
      cancelled = true
      subscription.unsubscribe()
    }
  }, [])

  useEffect(() => {
    if (!authReady || !signedInUserId) return
    let cancelled = false
    void (async () => {
      try {
        const [profileResult, sportsResult, cappersResult] = await Promise.all([
          supabase.from('member_profiles').select('age_verified_at,created_at,display_name,public_handle,avatar_url,public_profile_enabled,timezone,discord_alerts_enabled,email_alerts_enabled').eq('user_id', signedInUserId).maybeSingle(),
          supabase.rpc('public_favorite_sports'),
          supabase.rpc('public_favorite_cappers'),
        ])
        if (profileResult.error) throw profileResult.error
        if (sportsResult.error) throw sportsResult.error
        if (cappersResult.error) throw cappersResult.error
        if (cancelled) return
        const nextProfile = profileResult.data as MemberProfile | null
        setProfile(nextProfile)
        const userName = String(sessionMetadata?.full_name || sessionMetadata?.name || sessionMetadata?.username || 'Playmaker Member')
        setDisplayName(nextProfile?.display_name && nextProfile.display_name !== 'Playmaker Member' ? nextProfile.display_name : userName)
        setPublicHandle(nextProfile?.public_handle || '')
        setAvatarUrl(nextProfile?.avatar_url || String(sessionMetadata?.avatar_url || sessionMetadata?.picture || ''))
        setPublicProfileEnabled(Boolean(nextProfile?.public_profile_enabled))
        setTimezone(nextProfile?.timezone || 'America/New_York')
        setDiscordAlertsEnabled(Boolean(nextProfile?.discord_alerts_enabled))
        setEmailAlertsEnabled(Boolean(nextProfile?.email_alerts_enabled))
        setSports((sportsResult.data ?? []) as SportOption[])
        setCappers((cappersResult.data ?? []) as CapperOption[])

        if (nextProfile?.age_verified_at) {
          const [favoriteSportsResult, favoriteCappersResult] = await Promise.all([
            supabase.from('member_favorite_sports').select('sport_id').eq('user_id', signedInUserId),
            supabase.from('member_favorite_cappers').select('sport_id,capper_name').eq('user_id', signedInUserId),
          ])
          if (favoriteSportsResult.error) throw favoriteSportsResult.error
          if (favoriteCappersResult.error) throw favoriteCappersResult.error
          if (cancelled) return
          setFavoriteSportIds(((favoriteSportsResult.data ?? []) as FavoriteSport[]).map((row) => row.sport_id))
          setFavoriteCapperKeys(((favoriteCappersResult.data ?? []) as FavoriteCapper[]).map((row) => capperKey(row.sport_id, row.capper_name)))
        }
        if (!cancelled) {
          setMessage('')
          setPageState('ready')
        }
      } catch {
        if (!cancelled) {
          setMessage('Your account settings could not be loaded. Check the account migration and try again.')
          setPageState('error')
        }
      }
    })()
    return () => { cancelled = true }
  }, [authReady, retryCount, sessionMetadata, signedInUserId])

  async function signInWithDiscord() {
    setMessage('')
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'discord',
      options: { redirectTo: `${window.location.origin}/account` },
    })
    if (error) setMessage('Discord sign-in could not start. Check the configured OAuth provider.')
  }

  async function verifyAge(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!birthDate) {
      setMessage('Enter your date of birth to verify age.')
      return
    }
    setBusyKey('age')
    setMessage('')
    try {
      const { data, error } = await supabase.rpc('verify_member_age', { p_birth_date: birthDate })
      if (error) throw error
      if (data !== true) {
        setMessage('You must be at least 21 to create a member account.')
        return
      }
      setPageState('loading')
      setProfile((current) => current ? { ...current, age_verified_at: new Date().toISOString() } : current)
      setRetryCount((count) => count + 1)
      refreshAccess()
    } catch {
      setMessage('Age verification could not be completed. Try again.')
    } finally {
      setBusyKey('')
      setBirthDate('')
    }
  }

  async function toggleSport(sportId: number) {
    if (!session) return
    const isFavorite = favoriteSportIds.includes(sportId)
    const key = `sport:${sportId}`
    setBusyKey(key)
    setFavoriteSportIds((current) => isFavorite ? current.filter((id) => id !== sportId) : [...current, sportId])
    setMessage('Saving sport preference…')
    try {
      const result = isFavorite
        ? await supabase.from('member_favorite_sports').delete().eq('user_id', session.user.id).eq('sport_id', sportId)
        : await supabase.from('member_favorite_sports').insert({ user_id: session.user.id, sport_id: sportId })
      if (result.error) throw result.error
      setMessage('Sport preference saved.')
    } catch {
      setFavoriteSportIds((current) => isFavorite ? [...current, sportId] : current.filter((id) => id !== sportId))
      setMessage('Could not save that sport. Confirm your age verification is complete and retry.')
    } finally {
      setBusyKey('')
    }
  }

  async function toggleCapper(capper: CapperOption) {
    if (!session) return
    const key = capperKey(capper.sport_id, capper.capper_name)
    const isFavorite = favoriteCapperKeys.includes(key)
    setBusyKey(`capper:${key}`)
    setFavoriteCapperKeys((current) => isFavorite ? current.filter((item) => item !== key) : [...current, key])
    setMessage('Saving capper preference…')
    try {
      const result = isFavorite
        ? await supabase.from('member_favorite_cappers').delete().eq('user_id', session.user.id).eq('sport_id', capper.sport_id).eq('capper_name', capper.capper_name)
        : await supabase.from('member_favorite_cappers').insert({ user_id: session.user.id, sport_id: capper.sport_id, capper_name: capper.capper_name })
      if (result.error) throw result.error
      setMessage('Capper preference saved.')
    } catch {
      setFavoriteCapperKeys((current) => isFavorite ? [...current, key] : current.filter((item) => item !== key))
      setMessage('Could not save that capper. Confirm your age verification is complete and retry.')
    } finally {
      setBusyKey('')
    }
  }

  async function signOut() {
    const { error } = await supabase.auth.signOut()
    if (error) setMessage('Could not sign out. Try again.')
  }

  async function saveProfile() {
    if (!session) return
    const normalizedHandle = publicHandle.trim().toLowerCase()
    setBusyKey('profile')
    setMessage('Saving profile…')
    try {
      const { data, error } = await supabase.from('member_profiles').update({
        display_name: displayName.trim(),
        public_handle: normalizedHandle,
        avatar_url: avatarUrl.trim() || null,
        public_profile_enabled: publicProfileEnabled,
        timezone,
        discord_alerts_enabled: discordAlertsEnabled,
        email_alerts_enabled: emailAlertsEnabled,
      }).eq('user_id', session.user.id).select('age_verified_at,created_at,display_name,public_handle,avatar_url,public_profile_enabled,timezone,discord_alerts_enabled,email_alerts_enabled').single()
      if (error) throw error
      setProfile(data as MemberProfile)
      setPublicHandle(normalizedHandle)
      setMessage('Profile settings saved.')
    } catch {
      setMessage('Profile could not be saved. Check that your handle is unique and uses 3–30 letters, numbers, or hyphens.')
    } finally {
      setBusyKey('')
    }
  }

  async function copyPublicProfileLink() {
    if (!publicHandle) return
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/members/${publicHandle}`)
      setMessage('Public profile link copied.')
    } catch {
      setMessage('Could not copy the profile link from this browser.')
    }
  }

  function exportAccountData() {
    const data = {
      exported_at: new Date().toISOString(),
      display_name: displayName,
      public_handle: publicHandle,
      public_profile_enabled: publicProfileEnabled,
      timezone,
      discord_alerts_enabled: discordAlertsEnabled,
      email_alerts_enabled: emailAlertsEnabled,
      age_verified: Boolean(profile?.age_verified_at),
      favorite_sports: sports.filter((item) => favoriteSportIds.includes(item.id)).map(({ api_slug, name }) => ({ api_slug, name })),
      favorite_cappers: cappers.filter((item) => favoriteCapperKeys.includes(capperKey(item.sport_id, item.capper_name))).map(({ sport, capper_name }) => ({ sport, capper_name })),
    }
    const blobUrl = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a')
    anchor.href = blobUrl
    anchor.download = 'playmaker-account-export.json'
    anchor.click()
    URL.revokeObjectURL(blobUrl)
  }

  async function deleteAccount() {
    if (!window.confirm('Permanently delete your Playmaker member account and saved preferences? This does not delete Discord records or official plays.')) return
    setBusyKey('delete')
    const { error } = await supabase.rpc('delete_member_account')
    if (error) {
      setBusyKey('')
      setMessage('Account deletion failed. Try again or contact support.')
      return
    }
    await supabase.auth.signOut()
    navigate('/')
  }

  if (!authReady || (session && pageState === 'loading')) {
    return <main className="account-page"><p className="account-state">Loading account…</p></main>
  }

  if (!session) {
    return <main className="account-page">
      <section className="account-panel account-sign-in">
        <p className="eyebrow">Member account</p>
        <h1>Your picks.<br />Your sports.</h1>
        <p>Sign in with Discord to save favorite sports and cappers.</p>
        {message && <p className="account-message" role="alert">{message}</p>}
        <button className="button button-primary" type="button" onClick={signInWithDiscord}>Continue with Discord</button>
      </section>
    </main>
  }

  if (pageState === 'error') {
    return <main className="account-page">
      <section className="account-panel">
        <p className="eyebrow">Member account</p>
        <h1>Account settings<br />unavailable.</h1>
        <p className="account-message" role="alert">{message}</p>
        <button className="button button-primary" type="button" onClick={() => { setPageState('loading'); setRetryCount((count) => count + 1) }}>Retry</button>
        <button className="account-sign-out" type="button" onClick={signOut}>Sign out</button>
      </section>
    </main>
  }

  if (!profile?.age_verified_at) {
    return <main className="account-page">
      <section className="account-panel account-age-panel">
        <p className="eyebrow"><ShieldCheck size={16} /> Age verification</p>
        <h1>Members must<br />be 21+.</h1>
        <p>Enter your date of birth to confirm you meet the age requirement. We store only the verification timestamp, not your birth date.</p>
        <form className="age-verification-form" onSubmit={verifyAge}>
          <label htmlFor="member-birth-date">Date of birth</label>
          <input id="member-birth-date" type="date" value={birthDate} onChange={(event) => setBirthDate(event.target.value)} autoComplete="bday" required />
          {message && <p className="account-message" role="alert">{message}</p>}
          <button className="button button-primary" type="submit" disabled={busyKey === 'age'}>{busyKey === 'age' ? 'Verifying…' : 'Verify age & continue'}</button>
        </form>
        <button className="account-sign-out" type="button" onClick={signOut}>Sign out</button>
      </section>
    </main>
  }

  const capperGroups = sports.map((sport) => ({
    sport,
    cappers: cappers.filter((capper) => capper.sport_id === sport.id),
  })).filter((group) => group.cappers.length)

  return <main className="account-page">
    <header className="account-page-heading">
      <div><p className="eyebrow">Member profile</p><h1>Account settings.</h1><p>Manage your profile, privacy, alerts, and followed picks.</p></div>
      <div className="account-header-actions">
        <button className="account-secondary-button" type="button" onClick={exportAccountData}><Download size={16} /> Export data</button>
        <button className="account-sign-out" type="button" onClick={signOut}><LogOut size={16} /> Sign out</button>
      </div>
    </header>
    {message && <p className="account-message" role="status">{message}</p>}
    <section className="account-status-grid" aria-label="Account status">
      <div><span>Discord</span><strong>Connected</strong><small>{String(session.user.user_metadata?.full_name || session.user.user_metadata?.name || session.user.user_metadata?.username || 'Discord member')}</small></div>
      <div><span>Age verification</span><strong>Verified 21+</strong><small>Date of birth is not retained</small></div>
      <div><span>Membership</span><strong>{access?.state === 'active' ? `HIGHROLLER ${access.kind === 'trial' ? 'trial' : access.kind === 'owner' ? 'owner' : 'paid'}` : access?.state === 'launch-pending' ? 'Launch pending' : access?.state === 'membership-required' ? 'No verified active pass' : 'Access check required'}</strong><small>Verified against server-side entitlements, not Discord roles</small></div>
    </section>
    <section className="account-panel" aria-label="Membership access">
      <MemberAccessNotice />
      <button className="account-secondary-button" type="button" onClick={refreshAccess}>Recheck membership access</button>
    </section>
    <form className="account-preferences account-profile-form" onSubmit={(event) => { event.preventDefault(); void saveProfile() }}>
      <div className="account-section-heading"><p className="eyebrow">Profile</p><h2>Make it yours.</h2></div>
      <div className="account-profile-fields">
        <label>Display name<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={1} maxLength={60} required /></label>
        <label>Public handle<input value={publicHandle} onChange={(event) => setPublicHandle(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))} minLength={3} maxLength={30} pattern="[a-z0-9][a-z0-9-]{2,29}" required /><small>Letters, numbers, and hyphens only.</small></label>
        <label className="account-profile-wide">Avatar image URL<input type="url" value={avatarUrl} onChange={(event) => setAvatarUrl(event.target.value)} placeholder="Use your Discord avatar" /></label>
        <label>Time zone<select value={timezone} onChange={(event) => setTimezone(event.target.value)}>
          <option value="America/New_York">Eastern Time</option>
          <option value="America/Chicago">Central Time</option>
          <option value="America/Denver">Mountain Time</option>
          <option value="America/Los_Angeles">Pacific Time</option>
          <option value="UTC">UTC</option>
        </select></label>
        <label className="account-choice account-privacy-choice"><input type="checkbox" checked={publicProfileEnabled} onChange={(event) => setPublicProfileEnabled(event.target.checked)} /><span>Make my profile public</span></label>
      </div>
      <div className="account-profile-actions">
        <button className="button button-primary" type="submit" disabled={busyKey === 'profile'}>{busyKey === 'profile' ? 'Saving…' : 'Save profile'}</button>
        {publicProfileEnabled && publicHandle && <><Link className="account-secondary-button" to={`/members/${publicHandle}`}>View public profile <ArrowRight size={15} /></Link><button className="account-secondary-button" type="button" onClick={copyPublicProfileLink}><Copy size={15} /> Copy profile link</button></>}
      </div>
    </form>
    <section className="account-preferences account-notifications">
      <div className="account-section-heading"><p className="eyebrow">Notifications</p><h2>Choose your alerts.</h2></div>
      <p className="account-state">These preferences are saved, but alert delivery is not enabled yet.</p>
      <div className="account-sports-list">
        <label className="account-choice"><input type="checkbox" checked={discordAlertsEnabled} disabled={Boolean(busyKey)} onChange={(event) => setDiscordAlertsEnabled(event.target.checked)} /><span>Discord alerts for followed picks</span></label>
        <label className="account-choice"><input type="checkbox" checked={emailAlertsEnabled} disabled={Boolean(busyKey)} onChange={(event) => setEmailAlertsEnabled(event.target.checked)} /><span>Email alerts for followed picks</span></label>
      </div>
      <button className="button button-primary" type="button" disabled={busyKey === 'profile'} onClick={() => void saveProfile()}>{busyKey === 'profile' ? 'Saving…' : 'Save alert preferences'}</button>
    </section>
    <section className="account-preferences">
      <div className="account-section-heading"><p className="eyebrow">01 · Sports</p><h2>Pick your sports.</h2></div>
      <div className="account-sports-list">
        {sports.map((sport) => <label className="account-choice" key={sport.id}>
          <input type="checkbox" checked={favoriteSportIds.includes(sport.id)} disabled={Boolean(busyKey)} onChange={() => toggleSport(sport.id)} />
          <span>{sport.name}</span>
        </label>)}
      </div>
    </section>
    <section className="account-preferences">
      <div className="account-section-heading"><p className="eyebrow">02 · Cappers</p><h2>Choose by sport.</h2></div>
      {capperGroups.length ? capperGroups.map((group) => <div className="account-capper-group" key={group.sport.id}>
        <h3>{group.sport.name}</h3>
        <div className="account-cappers-list">
          {group.cappers.map((capper) => {
            const key = capperKey(capper.sport_id, capper.capper_name)
            return <label className="account-choice" key={key}>
              <input type="checkbox" checked={favoriteCapperKeys.includes(key)} disabled={Boolean(busyKey)} onChange={() => toggleCapper(capper)} />
              <span>{capper.capper_name}</span>
            </label>
          })}
        </div>
      </div>) : <p className="account-state">Capper choices appear as public settled records are published.</p>}
    </section>
    <section className="account-danger-zone">
      <div><p className="eyebrow">Privacy controls</p><h2>Delete your account.</h2><p>This permanently removes your member profile and saved preferences. It does not delete official plays or Discord data.</p></div>
      <button className="account-delete-button" type="button" disabled={busyKey === 'delete'} onClick={deleteAccount}><Trash2 size={16} /> Delete member account</button>
    </section>
  </main>
}
