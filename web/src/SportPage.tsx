import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fetchSportEvents, finalEventStatuses, formatEventScore, type SportEvent } from './sportEvents'
import { sportsCatalog } from './sportsCatalog'

type Result = {
  settled_at: string
  sport: string
  capper: string
  selection: string
  odds: number
  units: number
  status: 'win' | 'loss' | 'void' | 'partial'
  net_units: number
}
type EventView = 'schedule' | 'scores'

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/New_York' }).format(new Date(value))
}

function formatKickoff(value: string): string {
  return new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' }).format(new Date(value))
}

function easternDateKey(value: string | number): string {
  const parts = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/New_York' }).formatToParts(new Date(value))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

function formatScheduleGroupLabel(sportSlug: string, event: SportEvent): string {
  const round = event.round_name?.trim()
  if (round && sportSlug === 'ncaa' && /^\d+$/.test(round)) return `Week ${round}`
  return round || formatDate(event.start_at)
}

function eventSeasonLabel(sportName: string, event: SportEvent): string {
  if (sportName === 'NCAA') return String(new Date(event.start_at).getFullYear())
  return event.season || ''
}

function signedUnits(value: number): string {
  const amount = Number(value)
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(amount))}u`
}

export default function SportPage({ slug, results, loadState }: { slug: string; results: Result[]; loadState: 'loading' | 'ready' | 'error' | 'configuration' }) {
  const sport = sportsCatalog.find((item) => item.slug === slug)
  if (!sport) return <main className="sport-page"><p className="eyebrow">Sports</p><h1>Sport not found.</h1><Link to="/">Return home <ArrowRight size={16} /></Link></main>

  return <SportPageContent sport={sport} results={results} loadState={loadState} />
}

function SportPageContent({ sport, results, loadState }: { sport: (typeof sportsCatalog)[number]; results: Result[]; loadState: 'loading' | 'ready' | 'error' | 'configuration' }) {
  const [events, setEvents] = useState<SportEvent[]>([])
  const [eventView, setEventView] = useState<EventView>('schedule')
  const [selectedLeague, setSelectedLeague] = useState('')
  const [selectedGroup, setSelectedGroup] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const [eventState, setEventState] = useState<'loading' | 'ready' | 'unavailable' | 'error'>(sport.feed === 'unavailable' ? 'unavailable' : 'loading')
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (sport.feed === 'unavailable') return
    let cancelled = false
    void (async () => {
      try {
        const rows = await fetchSportEvents(sport)
        if (cancelled) return
        setEvents(rows)
        setEventState('ready')
      } catch {
        if (!cancelled) setEventState('error')
      }
    })()
    return () => { cancelled = true }
  }, [sport])

  const sportResultNames = sport.slug === 'american-football' ? new Set(['nfl', 'american football']) : new Set([sport.name.toLowerCase()])
  const sportResults = results.filter((result) => sportResultNames.has(result.sport.trim().toLowerCase()))
  const wins = sportResults.filter((result) => result.status === 'win').length
  const losses = sportResults.filter((result) => result.status === 'loss').length
  const net = sportResults.reduce((sum, result) => sum + Number(result.net_units), 0)
  const todayKey = easternDateKey(now)
  const upcomingEvents = events.filter((event) => !finalEventStatuses.has(event.status_code.toUpperCase()) && easternDateKey(event.start_at) >= todayKey).sort((first, second) => Date.parse(first.start_at) - Date.parse(second.start_at))
  const liveEvents = events.filter((event) => !finalEventStatuses.has(event.status_code.toUpperCase()) && easternDateKey(event.start_at) === todayKey && Date.parse(event.start_at) <= now).sort((first, second) => Date.parse(second.start_at) - Date.parse(first.start_at))
  const finalEvents = events.filter((event) => finalEventStatuses.has(event.status_code.toUpperCase())).sort((first, second) => Date.parse(second.start_at) - Date.parse(first.start_at))
  const scoreEvents = [...liveEvents, ...finalEvents]
  const leagueGroups = Array.from(upcomingEvents.reduce((groups, event) => {
    const label = event.league_name?.trim() || sport.name
    const leagueEvents = groups.get(label) ?? []
    leagueEvents.push(event)
    groups.set(label, leagueEvents)
    return groups
  }, new Map<string, SportEvent[]>()).entries()).map(([label, leagueEvents]) => ({ label, events: leagueEvents }))
  const activeLeague = leagueGroups.some((group) => group.label === selectedLeague) ? selectedLeague : leagueGroups[0]?.label || ''
  const activeLeagueEvents = leagueGroups.find((group) => group.label === activeLeague)?.events || []
  const scheduleGroups = Array.from(activeLeagueEvents.reduce((groups, event) => {
    const label = formatScheduleGroupLabel(sport.slug, event)
    const groupEvents = groups.get(label) ?? []
    groupEvents.push(event)
    groups.set(label, groupEvents)
    return groups
  }, new Map<string, SportEvent[]>()).entries()).map(([label, groupEvents]) => ({ label, events: groupEvents }))
  const activeGroup = scheduleGroups.some((group) => group.label === selectedGroup) ? selectedGroup : scheduleGroups[0]?.label || ''
  const activeGroupEvents = scheduleGroups.find((group) => group.label === activeGroup)?.events || []

  return <main className="sport-page">
    <Link to="/" className="capper-back-link"><ArrowRight size={16} /> Home</Link>
    <header className="sport-page-heading">
      <p className="eyebrow">Sports center</p><h1>{sport.name}.</h1>
      <p>Schedules, scores, and the official settled-play record for {sport.name}.</p>
    </header>
    {sport.slug === 'american-football' && <Link className="sport-feature-link" to="/nfl">Open NFL schedule, scores &amp; standings <ArrowRight size={16} /></Link>}
    {sport.feed === 'nfl' && <Link className="sport-feature-link" to="/nfl/lab">Open NFL matchup lab &amp; odds calculator <ArrowRight size={16} /></Link>}
    <section className="sport-api-events">
      <div className="profile-section-heading"><p className="eyebrow">Schedule & scores</p><h2>{sport.name} events.</h2></div>
      {eventState === 'loading' && <p className="results-empty">Loading {sport.name} schedule…</p>}
      {eventState === 'unavailable' && <p className="results-empty">A schedule feed is not connected for {sport.name} yet.</p>}
      {eventState === 'error' && <p className="results-empty">{sport.name} schedule is temporarily unavailable.</p>}
      {eventState === 'ready' && <>
        <nav className="sport-event-tabs" aria-label={`${sport.name} data views`}>
          <button className={eventView === 'schedule' ? 'active' : ''} type="button" onClick={() => setEventView('schedule')}>Schedule</button>
          <button className={eventView === 'scores' ? 'active' : ''} type="button" onClick={() => setEventView('scores')}>Scores</button>
        </nav>
        {eventView === 'schedule' && (scheduleGroups.length ? <div className="sport-event-list">
          {leagueGroups.length > 1 && <div className="sport-event-league-tabs" role="tablist" aria-label={`${sport.name} leagues`}>
            {leagueGroups.map((group) => <button key={group.label} className={activeLeague === group.label ? 'active' : ''} type="button" role="tab" aria-selected={activeLeague === group.label} onClick={() => { setSelectedLeague(group.label); setSelectedGroup('') }}>
              <span>{group.label}</span><small>{group.events.length} events</small>
            </button>)}
          </div>}
          <div className="sport-event-group-tabs" role="tablist" aria-label={`${sport.name} schedule groups`}>
            {scheduleGroups.map((group) => <button key={group.label} className={activeGroup === group.label ? 'active' : ''} type="button" role="tab" aria-selected={activeGroup === group.label} onClick={() => setSelectedGroup(group.label)}>
              <span>{group.label}</span><small>{group.events.length} events</small>
            </button>)}
          </div>
          <div className="sport-events-grid" role="tabpanel" aria-label={`${activeGroup} schedule`}>
            {activeGroupEvents.map((event) => <SportEventCard event={event} sportName={sport.name} key={event.event_id} />)}
          </div>
        </div> : <p className="results-empty">No upcoming {sport.name} events are available in the current schedule window.</p>)}
        {eventView === 'scores' && (scoreEvents.length ? <div className="sport-events-grid">{scoreEvents.map((event) => <SportEventCard event={event} sportName={sport.name} key={event.event_id} />)}</div> : <p className="results-empty">No live or final {sport.name} scores are available yet.</p>)}
      </>}
    </section>
    <div className="sport-record-metrics">
      <div><span>Settled plays</span><strong>{loadState === 'ready' ? sportResults.length : '—'}</strong></div>
      <div><span>Record</span><strong>{loadState === 'ready' ? `${wins}-${losses}` : '—'}</strong></div>
      <div><span>Net units</span><strong className={net > 0 ? 'net-positive' : net < 0 ? 'net-negative' : ''}>{loadState === 'ready' ? signedUnits(net) : '—'}</strong></div>
    </div>
    <section className="sport-results">
      <div className="profile-section-heading"><p className="eyebrow">The settled ledger</p><h2>{sport.name} results.</h2></div>
      {loadState === 'loading' && <p className="results-empty">Loading {sport.name} results…</p>}
      {loadState === 'error' && <p className="results-empty">Results are temporarily unavailable.</p>}
      {loadState === 'configuration' && <p className="results-empty">Results aren’t connected yet.</p>}
      {loadState === 'ready' && sportResults.length === 0 && <p className="results-empty">No official settled {sport.name} plays have been published yet.</p>}
      {sportResults.length > 0 && <div className="results-table" role="table" aria-label={`${sport.name} settled plays`}>
        <div className="result-row result-head" role="row"><span>Date</span><span>Play</span><span>Capper</span><span>Odds</span><span>Result</span><span>Net</span></div>
        {sportResults.map((result, index) => <div className="result-row" role="row" key={`${result.settled_at}-${index}`}>
          <span className="result-date">{formatDate(result.settled_at)}</span>
          <span className="result-selection">{result.selection}<small>{result.odds > 0 ? '+' : ''}{result.odds}</small></span>
          <span>{result.capper}</span><span>{result.units}u</span>
          <span><mark className={`status status-${result.status}`}>{result.status}</mark></span>
          <span className={result.net_units > 0 ? 'net-positive' : result.net_units < 0 ? 'net-negative' : ''}>{signedUnits(result.net_units)}</span>
        </div>)}
      </div>}
    </section>
  </main>
}

function SportEventTeam({ name, logo, score }: { name: string; logo: string | null; score: unknown }) {
  return <div className="nfl-team-line">
    {logo && <img src={logo} alt="" loading="lazy" />}
    <span>{name}</span>
    <strong>{formatEventScore(score)}</strong>
  </div>
}

function SportEventCard({ event, sportName }: { event: SportEvent; sportName: string }) {
  const isFinal = finalEventStatuses.has(event.status_code.toUpperCase())
  return <article className="sport-event-card">
    <div className="sport-event-meta"><span>{event.league_name || sportName}</span><time dateTime={event.start_at}>{formatKickoff(event.start_at)}</time></div>
    <h3>{event.event_name}</h3>
    {(event.home_name || event.away_name) && <div className="sport-event-teams">
      {event.away_name && <SportEventTeam name={event.away_name} logo={event.away_logo} score={event.away_score} />}
      {event.home_name && <SportEventTeam name={event.home_name} logo={event.home_logo} score={event.home_score} />}
    </div>}
    <div className="sport-event-footer"><span>{event.venue?.name || eventSeasonLabel(sportName, event)}</span><span className={`nfl-game-status${isFinal ? ' is-final' : ''}`}>{event.status}</span></div>
  </article>
}