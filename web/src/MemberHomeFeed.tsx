import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { supabase } from './supabaseClient'

type FeedResult = {
  settled_at: string
  sport: string
  capper: string
  selection: string
  odds: number
  units: number
  status: 'win' | 'loss' | 'void' | 'partial'
  net_units: number
}

type FeedState = 'loading' | 'signed-out' | 'unverified' | 'no-favorites' | 'ready' | 'error'

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' }).format(new Date(value))
}

function formatUnits(value: number): string {
  const amount = Number(value)
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(amount))}u`
}

export default function MemberHomeFeed({ results }: { results: FeedResult[] }) {
  const [state, setState] = useState<FeedState>('loading')
  const [feedResults, setFeedResults] = useState<FeedResult[]>([])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const { data: sessionData, error: sessionError } = await supabase.auth.getSession()
        if (sessionError) throw sessionError
        const userId = sessionData.session?.user.id
        if (!userId) {
          if (!cancelled) setState('signed-out')
          return
        }

        const { data: profile, error: profileError } = await supabase
          .from('member_profiles')
          .select('age_verified_at')
          .eq('user_id', userId)
          .maybeSingle()
        if (profileError) throw profileError
        if (!profile?.age_verified_at) {
          if (!cancelled) setState('unverified')
          return
        }

        const [favoriteSportsResponse, favoriteCappersResponse, sportsResponse] = await Promise.all([
          supabase.from('member_favorite_sports').select('sport_id').eq('user_id', userId),
          supabase.from('member_favorite_cappers').select('sport_id,capper_name').eq('user_id', userId),
          supabase.rpc('public_favorite_sports'),
        ])
        if (favoriteSportsResponse.error) throw favoriteSportsResponse.error
        if (favoriteCappersResponse.error) throw favoriteCappersResponse.error
        if (sportsResponse.error) throw sportsResponse.error

        const sportNames = new Set((sportsResponse.data ?? [])
          .filter((sport: { id: number }) => (favoriteSportsResponse.data ?? []).some((favorite) => favorite.sport_id === sport.id))
          .map((sport: { id: number; name: string }) => sport.name))
        const capperNames = new Set((favoriteCappersResponse.data ?? []).map((favorite) => favorite.capper_name))
        if (cancelled) return
        if (!sportNames.size && !capperNames.size) {
          setFeedResults([])
          setState('no-favorites')
          return
        }
        setFeedResults(results.filter((result) => sportNames.has(result.sport) || capperNames.has(result.capper)).slice(0, 10))
        setState('ready')
      } catch {
        if (!cancelled) setState('error')
      }
    })()
    return () => { cancelled = true }
  }, [results])

  return <section className="personalized-feed" aria-labelledby="personalized-feed-title">
    <div className="personalized-feed-heading">
      <div><p className="eyebrow">Your board</p><h2 id="personalized-feed-title">Picked for you.</h2></div>
      <Link to="/account">Manage favorites <ArrowRight size={16} /></Link>
    </div>
    {state === 'loading' && <p className="account-state">Loading your favorites…</p>}
    {state === 'signed-out' && <p className="account-state">Sign in to build a feed from your favorite sports and cappers. <Link to="/account">Set up your account <ArrowRight size={15} /></Link></p>}
    {state === 'unverified' && <p className="account-state">Verify your age to use saved favorites. <Link to="/account">Verify account <ArrowRight size={15} /></Link></p>}
    {state === 'no-favorites' && <p className="account-state">Choose favorite sports and cappers to personalize this feed. <Link to="/account">Choose favorites <ArrowRight size={15} /></Link></p>}
    {state === 'error' && <p className="account-state">Your personalized feed is temporarily unavailable.</p>}
    {state === 'ready' && (feedResults.length ? <div className="personalized-feed-list">
      {feedResults.map((result, index) => <article className="personalized-feed-item" key={`${result.settled_at}-${result.capper}-${index}`}>
        <span className="personalized-feed-date">{formatDate(result.settled_at)} · {result.sport}</span>
        <strong>{result.selection}</strong>
        <span>{result.capper} · {result.units}u at {result.odds > 0 ? '+' : ''}{result.odds}</span>
        <mark className={`status status-${result.status}`}>{result.status}</mark>
        <b className={result.net_units > 0 ? 'net-positive' : result.net_units < 0 ? 'net-negative' : ''}>{formatUnits(result.net_units)}</b>
      </article>)}
    </div> : <p className="account-state">No settled plays match your favorites yet. <Link to="/results">Browse all results <ArrowRight size={15} /></Link></p>)}
  </section>
}
