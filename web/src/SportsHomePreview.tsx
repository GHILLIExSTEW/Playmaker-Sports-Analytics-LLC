import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fetchSportEvents, finalEventStatuses, formatEventScore, type SportEvent } from './sportEvents'
import { sportsCatalog, type SportCatalogItem } from './sportsCatalog'

type Feed = { state: 'loading' | 'ready' | 'error'; events: SportEvent[] }

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York',
  }).format(new Date(value))
}

export default function SportsHomePreview() {
  const [selectedSport, setSelectedSport] = useState<SportCatalogItem>(sportsCatalog[0])
  const [feeds, setFeeds] = useState<Record<string, Feed>>({})
  const [now, setNow] = useState(() => Date.now())
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    let cancelled = false
    for (const sport of sportsCatalog.filter((item) => item.feed !== 'unavailable')) {
      void fetchSportEvents(sport).then((events) => {
        if (!cancelled) setFeeds((current) => ({ ...current, [sport.slug]: { state: 'ready', events } }))
      }).catch(() => {
        if (!cancelled) setFeeds((current) => ({ ...current, [sport.slug]: { state: 'error', events: [] } }))
      })
    }
    return () => { cancelled = true }
  }, [retry])

  const feed = feeds[selectedSport.slug]
  const events = feed?.events ?? []
  const upcoming = events.filter((event) => !finalEventStatuses.has(event.status_code.toUpperCase()) && Date.parse(event.start_at) >= now)
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))
  const inProgress = events.filter((event) => !finalEventStatuses.has(event.status_code.toUpperCase()) && Date.parse(event.start_at) <= now && Date.parse(event.start_at) >= now - 5 * 60 * 60 * 1000)
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))
  const recent = events.filter((event) => finalEventStatuses.has(event.status_code.toUpperCase()) && Date.parse(event.start_at) <= now)
    .sort((a, b) => Date.parse(b.start_at) - Date.parse(a.start_at))
  const chosen = [...inProgress, ...upcoming, ...recent].slice(0, 3)
  const oldestSync = events.length ? events.reduce((oldest, event) => Date.parse(event.synced_at) < Date.parse(oldest) ? event.synced_at : oldest, events[0].synced_at) : null

  return <section className="nfl-preview" aria-label="Sports schedule and scores">
    <div className="nfl-preview-heading">
      <div><p className="eyebrow">All sports · API-Sports</p><h2>Schedule &amp; scores.</h2></div>
      <Link className="nfl-view-link" to={`/sports/${selectedSport.slug}`}>Open {selectedSport.name} center <ArrowRight size={17} /></Link>
    </div>
    <div className="filter-group sports-preview-filters" aria-label="Choose a sport">
      {sportsCatalog.map((sport) => <button key={sport.slug} type="button" className={selectedSport.slug === sport.slug ? 'active' : ''} aria-pressed={selectedSport.slug === sport.slug} onClick={() => setSelectedSport(sport)}>{sport.name}</button>)}
    </div>
    <h3>{selectedSport.name}</h3>
    <div aria-live="polite">
      {selectedSport.feed === 'unavailable' ? <p className="nfl-state">A schedule feed is not connected for {selectedSport.name} yet.</p>
        : !feed || feed.state === 'loading' ? <p className="nfl-state">Loading cached {selectedSport.name} data...</p>
        : feed.state === 'error' ? <p className="nfl-state" role="alert">{selectedSport.name} data could not be loaded. <button type="button" className="results-retry" onClick={() => { setFeeds({}); setRetry((value) => value + 1) }}>Retry sports data</button></p>
        : chosen.length === 0 ? <p className="nfl-state">No current {selectedSport.name} events are available in the cached schedule.</p>
        : <>
          <p className="nfl-state">Cached schedule and scores, not a real-time feed. Times Eastern. Oldest cached event sync: {oldestSync && formatTime(oldestSync)}.</p>
          <div className="nfl-preview-grid">{chosen.map((event) => <article className="nfl-game-card" key={event.event_id}>
            <div className="nfl-game-meta"><span>{event.league_name || selectedSport.name}</span><time dateTime={event.start_at}>{formatTime(event.start_at)}</time></div>
            <h3>{event.event_name}</h3>
            {event.away_name && <TeamLine name={event.away_name} logo={event.away_logo} score={event.away_score} />}
            {event.home_name && <TeamLine name={event.home_name} logo={event.home_logo} score={event.home_score} />}
            <span className="nfl-game-status">{event.status}</span>
          </article>)}</div>
        </>}
    </div>
  </section>
}

function TeamLine({ name, logo, score }: { name: string; logo: string | null; score: unknown }) {
  return <div className="nfl-team-line">
    {logo && <img src={logo} alt="" loading="lazy" />}
    <span>{name}</span><strong>{formatEventScore(score)}</strong>
  </div>
}
