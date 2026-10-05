import { lazy, Suspense, useEffect, useState } from 'react'
import {
  ArrowRight, Menu,
  MessageCircle, X,
} from 'lucide-react'
import { BrowserRouter, Link, useLocation } from 'react-router-dom'
import { getCapperAvatarUrl } from './capperAvatars'
import { supabase } from './supabaseClient'
import { sportsCatalog } from './sportsCatalog'
import SportsDropdown from './SportsDropdown'
import type { PolicyKind } from './PolicyPage'
import './App.css'

type ResultStatus = 'win' | 'loss' | 'void' | 'partial'
type Result = {
  created_at: string
  settled_at: string
  sport: string
  capper: string
  selection: string
  odds: number
  units: number
  status: ResultStatus
  net_units: number
  avatar_url?: string | null
}
type LoadState = 'loading' | 'ready' | 'error' | 'configuration'
type CapperSummary = { name: string; slug: string; avatar_url?: string | null; plays: number; wins: number; losses: number; net_units: number }

const resultsPageSize = 1000
const localBrandLogoUrl = '/playmaker-mark-transparent.webp'
const brandLogoUrl = import.meta.env.VITE_BRAND_LOGO_URL || 'https://lhsevzucmmzetpshpffv.supabase.co/storage/v1/object/public/website-assets/brand/playmaker-mark-transparent-512.webp'
const localHeroArtUrl = '/playmaker-arch-transparent.webp'
const heroArtUrl = 'https://lhsevzucmmzetpshpffv.supabase.co/storage/v1/object/public/website-assets/brand/playmaker-arch-transparent-1024.webp'
const CapperAnalyticsCharts = lazy(() => import('./CapperAnalyticsCharts'))
const AllResultsPage = lazy(() => import('./AllResultsPage'))
const NflPage = lazy(() => import('./NflPage'))
const NflHomePreview = lazy(() => import('./NflHomePreview'))
const MemberAccountPage = lazy(() => import('./MemberAccountPage'))
const MemberPublicPage = lazy(() => import('./MemberPublicPage'))
const MemberHomeFeed = lazy(() => import('./MemberHomeFeed'))
const MembershipPage = lazy(() => import('./MembershipPage'))
const PolicyPage = lazy(() => import('./PolicyPage'))
const SportPage = lazy(() => import('./SportPage'))

async function fetchPublicResults(): Promise<Result[]> {
  if (!supabase) throw new Error('Public Supabase configuration is missing.')

  const results: Result[] = []
  for (let offset = 0; ; offset += resultsPageSize) {
    const { data, error } = await supabase.rpc('public_settled_results').range(offset, offset + resultsPageSize - 1)
    if (error) throw error
    if (!Array.isArray(data)) throw new Error('Unexpected results response.')
    results.push(...data as unknown as Result[])
    if (data.length < resultsPageSize) return results
  }
}

function summarizeCappers(results: Result[]): CapperSummary[] {
  const summaries = new Map<string, CapperSummary>()
  for (const result of results) {
    const summary = summaries.get(result.capper) ?? {
      name: result.capper,
      slug: capperSlug(result.capper),
      avatar_url: result.avatar_url ?? getCapperAvatarUrl(result.capper),
      plays: 0,
      wins: 0,
      losses: 0,
      net_units: 0,
    }
    summary.plays += 1
    summary.net_units += Number(result.net_units)
    summary.avatar_url ??= result.avatar_url ?? getCapperAvatarUrl(result.capper)
    if (result.status === 'win') summary.wins += 1
    if (result.status === 'loss') summary.losses += 1
    summaries.set(result.capper, summary)
  }
  return [...summaries.values()].sort((first, second) => second.net_units - first.net_units)
}

function capperSlug(name: string): string {
  return name.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'capper'
}

function generatedAvatarUrl(name: string): string {
  return `https://api.dicebear.com/9.x/initials/svg?seed=${encodeURIComponent(name)}&backgroundColor=0b6843,d94f45,6746bf`
}

function CapperAvatar({ name, avatarUrl, large = false }: { name: string; avatarUrl?: string | null; large?: boolean }) {
  const [failed, setFailed] = useState(false)
  const initials = name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()
  if (failed) return <span className={`capper-avatar-fallback${large ? ' is-large' : ''}`} aria-label={`${name} avatar`}>{initials}</span>
  return <img className={`capper-avatar-image${large ? ' is-large' : ''}`} src={avatarUrl || generatedAvatarUrl(name)} alt={`${name} avatar`} onError={() => setFailed(true)} />
}

function BrandLogo({ className }: { className: string }) {
  const [source, setSource] = useState(brandLogoUrl)
  return <img
    className={className}
    src={source}
    alt="Playmaker Picks"
    onError={() => { if (source !== localBrandLogoUrl) setSource(localBrandLogoUrl) }}
  />
}

function HeroArtwork() {
  const [source, setSource] = useState(heroArtUrl)
  return <img
    className="hero-logo-art"
    src={source}
    alt="Arched Playmaker Picks logo"
    fetchPriority="high"
    onError={() => { if (source !== localHeroArtUrl) setSource(localHeroArtUrl) }}
  />
}

function buildCapperAnalytics(results: Result[]) {
  const chronological = [...results].sort((first, second) => Date.parse(first.settled_at) - Date.parse(second.settled_at))
  const daily = new Map<string, { date: string; daily_units: number }>()
  const bySport = new Map<string, { sport: string; plays: number; wins: number; losses: number; net_units: number }>()

  for (const result of chronological) {
    const date = formatDate(result.settled_at)
    const day = daily.get(date) ?? { date, daily_units: 0 }
    day.daily_units += Number(result.net_units)
    daily.set(date, day)

    const sport = bySport.get(result.sport) ?? { sport: result.sport, plays: 0, wins: 0, losses: 0, net_units: 0 }
    sport.plays += 1
    sport.net_units += Number(result.net_units)
    if (result.status === 'win') sport.wins += 1
    if (result.status === 'loss') sport.losses += 1
    bySport.set(result.sport, sport)
  }

  let cumulative = 0
  const trend = [...daily.values()].map((day) => {
    cumulative += day.daily_units
    return { ...day, cumulative_units: Math.round(cumulative * 100) / 100 }
  })
  return { trend, bySport: [...bySport.values()].sort((first, second) => second.net_units - first.net_units) }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/New_York',
  }).format(new Date(value))
}

function formatUnits(value: number): string {
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value))}u`
}

function formatNetUnits(value: number): string {
  const amount = Number(value)
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(amount))}u`
}

function App() {
  return <BrowserRouter><Website /></BrowserRouter>
}

function Website() {
  const location = useLocation()
  const routePath = location.pathname.replace(/\/+$/, '') || '/'
  const policyKind: PolicyKind | null = routePath === '/terms' ? 'terms' : routePath === '/privacy' ? 'privacy' : routePath === '/refunds' ? 'refunds' : null
  const sportRouteSlug = routePath.startsWith('/sports/') ? routePath.slice('/sports/'.length) : null
  const [menuOpen, setMenuOpen] = useState(false)
  const [sport, setSport] = useState('All')
  const [results, setResults] = useState<Result[]>([])
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [retryCount, setRetryCount] = useState(0)
  useEffect(() => {
    let cancelled = false
    fetchPublicResults()
      .then((loadedResults) => {
        if (!cancelled) {
          setResults(loadedResults)
          setLoadState('ready')
        }
      })
      .catch(() => {
        if (!cancelled) setLoadState('error')
      })

    return () => { cancelled = true }
  }, [retryCount])

  useEffect(() => {
    if (location.pathname !== '/') {
      window.scrollTo(0, 0)
      return
    }
    const sectionId = location.hash.slice(1)
    if (sectionId) requestAnimationFrame(() => document.getElementById(sectionId)?.scrollIntoView())
  }, [location.hash, location.pathname])

  useEffect(() => {
    const targets = document.querySelectorAll<HTMLElement>('.scroll-reveal')
    if (!('IntersectionObserver' in window)) {
      targets.forEach((target) => target.classList.add('is-visible'))
      return
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible')
          observer.unobserve(entry.target)
        }
      })
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' })
    targets.forEach((target) => observer.observe(target))
    return () => observer.disconnect()
  }, [routePath])

  const sports: string[] = ['All', ...Array.from(new Set(results.map((result) => result.sport)))]
  const visibleResults = sport === 'All' ? results : results.filter((result) => result.sport === sport)
  const cappers = summarizeCappers(results)
  const profileSlug = location.pathname.startsWith('/cappers/') ? decodeURIComponent(location.pathname.slice('/cappers/'.length).replace(/\/$/, '')) : null
  const memberHandle = routePath.startsWith('/members/') ? decodeURIComponent(routePath.slice('/members/'.length)) : null
  const selectedCapper = cappers.find((capper) => capper.slug === profileSlug)
  const selectedCapperName = selectedCapper?.name
  useEffect(() => {
    document.title = policyKind
      ? `${policyKind === 'terms' ? 'Terms of Service' : policyKind === 'privacy' ? 'Privacy Notice' : 'Refund Policy'} | Playmaker Picks`
      : selectedCapperName
      ? `${selectedCapperName} | Playmaker Picks`
      : routePath === '/results'
        ? 'Settled Results | Playmaker Picks'
        : routePath === '/account'
          ? 'Member Account | Playmaker Picks'
            : routePath === '/membership'
              ? 'Membership | Playmaker Picks'
              : sportRouteSlug
                ? `${sportsCatalog.find((item) => item.slug === sportRouteSlug)?.name || 'Sport'} | Playmaker Picks`
        : 'Playmaker Picks | Sports Analysis With Receipts'
    document.querySelector('meta[name="description"]')?.setAttribute(
      'content',
      selectedCapperName
        ? `${selectedCapperName}'s official settled-play record, performance graphs, and results.`
        : 'Verified sports analysis, transparent play tracking, and the Playmaker Picks community.',
    )
  }, [routePath, selectedCapperName, sportRouteSlug, policyKind])
  const capperResults = selectedCapper ? results.filter((result) => result.capper === selectedCapper.name) : []
  const capperAnalytics = buildCapperAnalytics(capperResults)
  const capperWins = capperResults.filter((result) => result.status === 'win').length
  const capperLosses = capperResults.filter((result) => result.status === 'loss').length
  const capperDecisions = capperWins + capperLosses
  const capperSports = ['All', ...Array.from(new Set(capperResults.map((result) => result.sport)))]
  const visibleCapperResults = sport === 'All' ? capperResults : capperResults.filter((result) => result.sport === sport)
  const closeMenu = () => setMenuOpen(false)
  const sectionHref = (section: string) => routePath === '/' ? `#${section}` : `/#${section}`
  const homepageResults = visibleResults.slice(0, 10)

  return (
    <div className="site-shell">
      <header className="site-header">
        <Link className="brand" to="/" aria-label="Playmaker Picks home" onClick={closeMenu}>
          <BrandLogo className="brand-logo" />
        </Link>
        <nav className={menuOpen ? 'main-nav is-open' : 'main-nav'} aria-label="Primary navigation">
          <a href="/results" onClick={closeMenu}>Results</a>
          <SportsDropdown onNavigate={closeMenu} />
          <a href="/account" onClick={closeMenu}>Account</a>
          <a href={sectionHref('cappers')} onClick={closeMenu}>Cappers</a>
          <Link to="/membership" onClick={closeMenu}>Membership</Link>
          <a href={sectionHref('method')} onClick={closeMenu}>Method</a>
          <a className="nav-community" href="https://discord.gg/mwxRsWUp5W" target="_blank" rel="noreferrer" onClick={closeMenu}>Join Discord <ArrowRight size={16} /></a>
        </nav>
        <button className="menu-button" type="button" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label="Toggle navigation">{menuOpen ? <X /> : <Menu />}</button>
      </header>

      <main id="top">
        {policyKind ? (
          <Suspense fallback={<section className="membership-page"><p className="account-state">Loading policy...</p></section>}>
            <PolicyPage kind={policyKind} />
          </Suspense>
        ) : memberHandle ? (
          <Suspense fallback={<section className="member-public-page"><p className="account-state">Loading public profile…</p></section>}>
            <MemberPublicPage handle={memberHandle} />
          </Suspense>
        ) : routePath === '/account' ? (
          <Suspense fallback={<section className="account-page"><p className="account-state">Loading account…</p></section>}>
            <MemberAccountPage />
          </Suspense>
        ) : routePath === '/membership' ? (
          <Suspense fallback={<section className="membership-page"><p className="account-state">Loading membership options…</p></section>}>
            <MembershipPage />
          </Suspense>
        ) : sportRouteSlug ? (
          <Suspense fallback={<section className="sport-page"><p className="account-state">Loading sport page…</p></section>}>
            <SportPage slug={sportRouteSlug} results={results} loadState={loadState} />
          </Suspense>
        ) : routePath === '/nfl' ? (
          <Suspense fallback={<section className="nfl-page"><p className="nfl-state">Loading NFL data…</p></section>}>
            <NflPage />
          </Suspense>
        ) : routePath === '/results' ? (
          <Suspense fallback={<section className="results-page results-section"><p className="results-empty">Loading the full results ledger…</p></section>}>
            <AllResultsPage
              results={visibleResults}
              totalResults={results.length}
              sports={sports}
              sport={sport}
              loadState={loadState}
              onSportChange={setSport}
              onRetry={() => { setLoadState('loading'); setRetryCount((count) => count + 1) }}
            />
          </Suspense>
        ) : selectedCapper ? (
          <section className="capper-home">
            <div className="capper-profile-heading">
              <Link to="/#cappers" className="capper-back-link"><ArrowRight size={16} /> All cappers</Link>
              <div className="capper-identity">
                <CapperAvatar name={selectedCapper.name} avatarUrl={selectedCapper.avatar_url} large />
                <div><p className="eyebrow">Public capper homepage</p><h1>{selectedCapper.name}</h1><p>Official results, performance trends, and sport-by-sport analysis.</p></div>
              </div>
            </div>
            <div className="capper-profile-metrics">
              <div><span>Settled plays</span><strong>{selectedCapper.plays}</strong></div>
              <div><span>Record</span><strong>{selectedCapper.wins}-{selectedCapper.losses}</strong></div>
              <div><span>Win rate</span><strong>{capperDecisions ? `${Math.round(capperWins / capperDecisions * 100)}%` : '—'}</strong></div>
              <div><span>Net units</span><strong className={selectedCapper.net_units > 0 ? 'net-positive' : selectedCapper.net_units < 0 ? 'net-negative' : ''}>{formatNetUnits(selectedCapper.net_units)}</strong></div>
            </div>
            <div className="capper-analytics-grid">
              <Suspense fallback={<div className="results-empty">Loading analytics graphs…</div>}>
                <CapperAnalyticsCharts trend={capperAnalytics.trend} bySport={capperAnalytics.bySport} />
              </Suspense>
            </div>
            <section className="capper-profile-results" id="capper-results">
              <div className="profile-section-heading"><p className="eyebrow">Verified history</p><h2>Every settled play.</h2></div>
              <div className="results-toolbar">
                <div className="filter-group" aria-label="Filter this capper's results by sport">
                  {capperSports.map((item) => <button className={sport === item ? 'active' : ''} type="button" key={item} onClick={() => setSport(item)}>{item}</button>)}
                </div>
                <span>{loadState === 'ready' ? `${capperResults.length} settled plays` : loadState === 'loading' ? 'Loading results' : 'Results unavailable'}</span>
              </div>
              <div className="results-table" role="table" aria-label={`${selectedCapper.name} settled results`}>
                <div className="result-row result-head" role="row"><span>Date</span><span>Play</span><span>Capper</span><span>Risk</span><span>Result</span><span>Net</span></div>
                {visibleCapperResults.map((result, index) => (
                  <div className="result-row" role="row" key={`${result.settled_at}-${index}`}>
                    <span className="result-date">{formatDate(result.settled_at)}<small>{result.sport}</small></span>
                    <span className="result-selection">{result.selection}<small>{result.odds > 0 ? '+' : ''}{result.odds}</small></span>
                    <span>{result.capper}</span><span>{formatUnits(result.units)}</span>
                    <span><mark className={`status status-${result.status}`}>{result.status}</mark></span>
                    <span className={result.net_units > 0 ? 'net-positive' : result.net_units < 0 ? 'net-negative' : ''}>{formatNetUnits(result.net_units)}</span>
                  </div>
                ))}
                {loadState === 'loading' && <div className="results-empty">Loading this capper's record…</div>}
                {loadState === 'error' && <div className="results-empty">The public record could not be loaded. Return to the homepage and retry.</div>}
                {loadState === 'ready' && visibleCapperResults.length === 0 && <div className="results-empty">No settled results{sport !== 'All' ? ` for ${sport}` : ''} yet.</div>}
              </div>
            </section>
          </section>
        ) : profileSlug ? (
          <section className="capper-not-found"><p className="eyebrow">Capper profile</p><h1>{loadState === 'loading' ? 'Loading record' : loadState === 'error' ? 'Record unavailable' : 'Profile not found'}</h1><Link to="/#cappers">Return to all cappers <ArrowRight size={16} /></Link></section>
        ) : routePath !== '/' ? (
          <section className="capper-not-found"><p className="eyebrow">Page not found</p><h1>This page isn't on the board.</h1><Link to="/">Return home <ArrowRight size={16} /></Link></section>
        ) : (
          <>
        <section className="hero-section hero-logo-hero">
          <div className="hero-logo-stage reveal">
            <h1 className="visually-hidden">Playmaker Picks</h1>
            <p className="hero-logo-kicker"><span className="live-dot" /> Independent sports analysis</p>
            <HeroArtwork />
            <p className="hero-logo-tagline">Official plays. Every result stays on the board.</p>
          </div>
          <a className="hero-scroll-cue" href="#results" aria-label="Scroll to official results">
            <span>Explore the official record</span>
          </a>
        </section>

        <Suspense fallback={<section className="personalized-feed"><p className="account-state">Loading your board…</p></section>}>
          <MemberHomeFeed results={results} />
        </Suspense>

        <section className="results-section scroll-reveal" id="results">
          <div className="section-heading">
            <div><p className="eyebrow">The settled ledger</p><h2>Built for receipts, not promises.</h2></div>
            <p>Official plays appear here after they settle, with the original published line, risk, capper, and final grade. Open plays and private account details are never returned by the public data endpoint.</p>
          </div>
          <div className="results-toolbar">
            <div className="filter-group" aria-label="Filter settled results by sport">
              {sports.map((item) => <button className={sport === item ? 'active' : ''} type="button" key={item} onClick={() => setSport(item)}>{item}</button>)}
            </div>
            <span aria-live="polite">{loadState === 'ready' ? `Showing ${homepageResults.length} of ${results.length} settled plays` : loadState === 'loading' ? 'Loading results' : loadState === 'configuration' ? 'Supabase setup required' : 'Results unavailable'}</span>
            {loadState === 'error' && <button className="results-retry" type="button" onClick={() => { setLoadState('loading'); setRetryCount((count) => count + 1) }}>Retry</button>}
          </div>
          {loadState === 'configuration' && <p className="data-notice">Add the public Supabase URL and anon key to <code>web/.env.local</code>, then apply the public-results SQL migration.</p>}
          {loadState === 'error' && <p className="data-notice">Live results could not be loaded. Check the Supabase public-results migration and project settings, then retry.</p>}
          <div className="results-table" role="table" aria-label="Verified settled results">
            <div className="result-row result-head" role="row"><span>Date</span><span>Play</span><span>Capper</span><span>Risk</span><span>Result</span><span>Net</span></div>
            {homepageResults.map((result, index) => (
              <div className="result-row" role="row" key={`${result.settled_at}-${result.capper}-${index}`}>
                <span className="result-date">{formatDate(result.settled_at)}<small>{result.sport}</small></span>
                <span className="result-selection">{result.selection}<small>{result.odds > 0 ? '+' : ''}{result.odds}</small></span>
                <span>{result.capper}</span><span>{formatUnits(result.units)}</span>
                <span><mark className={`status status-${result.status}`}>{result.status}</mark></span>
                <span className={result.net_units > 0 ? 'net-positive' : result.net_units < 0 ? 'net-negative' : ''}>{formatNetUnits(result.net_units)}</span>
              </div>
            ))}
            {loadState === 'ready' && visibleResults.length === 0 && <div className="results-empty">No settled results{sport !== 'All' ? ` for ${sport}` : ''} yet.</div>}
            {loadState === 'loading' && <div className="results-empty">Loading the official record…</div>}
          </div>
          <div className="results-view-all-wrap"><Link className="button results-view-all" to="/results">View all settled results <ArrowRight size={17} /></Link></div>
          <div className="proof-band">
            <div className="proof-copy">
              <p className="eyebrow">Built around the record</p><h3>One scoreboard.<br />Every angle.</h3>
              <p>Filter by capper, sport, date range, play type, and membership card. The same settled records power Discord recaps and the public site.</p>
              <a href="#method">Read our grading method <ArrowRight size={17} /></a>
            </div>
            <div className="proof-visual"><img src="/growth.png" alt="Playmaker Picks performance graphic" /></div>
          </div>
        </section>

        <Suspense fallback={<section className="nfl-preview"><p className="nfl-state">Loading NFL schedule…</p></section>}>
          <NflHomePreview />
        </Suspense>

        <section className="cappers-section scroll-reveal" id="cappers">
          <div className="section-heading compact">
            <div><p className="eyebrow">The room</p><h2>Know who made the call.</h2></div>
            <p>Capper summaries below are calculated from published, settled plays. Private member details are not included.</p>
          </div>
          <div className="capper-grid">
            {cappers.map((capper, index) => (
              <article className="capper-card" key={capper.name}>
                <CapperAvatar name={capper.name} avatarUrl={capper.avatar_url} /><span className="capper-index">0{index + 1}</span>
                <h3>{capper.name}</h3><strong>{capper.plays} settled plays · {capper.wins}-{capper.losses}</strong>
                <p>Verified net: <span className={capper.net_units > 0 ? 'net-positive' : capper.net_units < 0 ? 'net-negative' : ''}>{formatNetUnits(capper.net_units)}</span></p>
                <span className="capper-record-label">Calculated from the public settled record</span>
                <Link to={`/cappers/${capper.slug}`}>Open capper homepage <ArrowRight size={16} /></Link>
              </article>
            ))}
            {loadState === 'ready' && cappers.length === 0 && <p className="results-empty">Capper records will appear after official plays settle.</p>}
            {loadState !== 'ready' && <p className="results-empty">Capper records load with the settled results.</p>}
          </div>
        </section>

        <section className="membership-promo scroll-reveal" id="membership">
          <div><p className="eyebrow">Membership</p><h2>One membership. Your seat.</h2><p>Join the free ROOKIE community or try seven days of full HIGHROLLER access. Continue for $19.99 per 30-day pass. No automatic charges. Enrollment remains closed pending launch review.</p></div>
          <Link className="button membership-promo-link" to="/membership">See membership &amp; trial <ArrowRight size={17} /></Link>
        </section>

        <section className="method-section scroll-reveal" id="method">
          <div className="method-intro"><p className="eyebrow">How the board works</p><h2>Clarity before confidence.</h2></div>
          <div className="method-steps">
            <article><span>01</span><div><h3>Post</h3><p>Every play records its author, odds, units, and publish time before the event begins.</p></div></article>
            <article><span>02</span><div><h3>Track</h3><p>The original position stays visible while open. Corrections leave an audit trail.</p></div></article>
            <article><span>03</span><div><h3>Settle</h3><p>Wins, losses, voids, and partial results use one published grading method.</p></div></article>
            <article><span>04</span><div><h3>Review</h3><p>Public records roll into capper, sport, weekly, monthly, and all-time views.</p></div></article>
          </div>
        </section>

        <section className="community-section scroll-reveal" id="community">
          <div><p className="eyebrow">The clubhouse</p><h2>The card moves fast.<br />The record stays put.</h2></div>
          <div className="community-copy"><p>Discord carries live alerts and conversation. The website keeps the durable analysis, searchable discussion, and complete history.</p>
            <a className="button button-accent" href="https://discord.gg/mwxRsWUp5W" target="_blank" rel="noreferrer"><MessageCircle size={18} /> Join Discord</a>
          </div>
        </section>
          </>
        )}
      </main>

      <footer className="site-footer">
        <div className="footer-brand"><BrandLogo className="footer-logo" /><p><strong>Playmaker Picks</strong><small>Operated by Playmaker Sports Analytics, LLC.</small></p></div>
        <div className="footer-links"><Link to="/#method">Methodology</Link><Link to="/terms">Terms</Link><Link to="/privacy">Privacy</Link><Link to="/refunds">Refunds</Link><a href="mailto:support@playmakersportsanalytics.com">Support</a></div>
        <p className="disclaimer">Sports analysis and opinions for informational and entertainment purposes only. We do not accept or place wagers. No outcome or profit is guaranteed. Must be 21+.</p>
        <p className="copyright">© 2026 Playmaker Sports Analytics, LLC.</p>
      </footer>
    </div>
  )
}

export default App