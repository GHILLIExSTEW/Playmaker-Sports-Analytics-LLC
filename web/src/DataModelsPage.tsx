import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { completedModelGameCount, projectMatchup, type ModelEvent } from './modelMath'
import { fetchModelEvents, modelCompetitions } from './sportModels'
import { finalEventStatuses } from './sportEvents'

function easternTime(value: string): string {
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/New_York',
  }).format(new Date(value))
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

export default function DataModelsPage() {
  const [selectedCompetition, setSelectedCompetition] = useState(modelCompetitions[0].key)
  const [events, setEvents] = useState<ModelEvent[]>([])
  const [unresolved, setUnresolved] = useState(0)
  const [selectedEvent, setSelectedEvent] = useState('')
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const competition = modelCompetitions.find((item) => item.key === selectedCompetition) ?? modelCompetitions[0]

  useEffect(() => {
    let cancelled = false
    void fetchModelEvents(competition).then((data) => {
      if (cancelled) return
      setEvents(data.events)
      setUnresolved(data.unresolved)
      setSelectedEvent('')
      setError('')
      setState('ready')
    }).catch((failure: unknown) => {
      if (cancelled) return
      setError(failure instanceof Error ? failure.message : 'Please retry.')
      setState('error')
    })
    return () => { cancelled = true }
  }, [competition, retry])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const upcoming = events
    .filter((event) => !finalEventStatuses.has(event.status_code.toUpperCase())
      && Date.parse(event.start_at) > now && event.home_name && event.away_name)
    .sort((first, second) => Date.parse(first.start_at) - Date.parse(second.start_at))
  const matchup = upcoming.find((event) => event.event_id === selectedEvent) ?? upcoming[0]
  const projection = matchup ? projectMatchup(events, matchup) : null
  const historicalGames = matchup ? completedModelGameCount(events, matchup.start_at) : 0

  return <main className="data-models-page">
    <Link to="/" className="capper-back-link">Home</Link>
    <header className="data-models-heading">
      <p className="eyebrow">Playmaker research · Experimental</p>
      <h1>Sports data models.</h1>
      <p>Score-history baselines for upcoming games across professional and college sports. Estimates are experimental and are not betting recommendations.</p>
    </header>

    <nav className="model-competition-tabs" aria-label="Choose a competition">
      {modelCompetitions.map((item) => <button
        key={item.key}
        type="button"
        className={item.key === competition.key ? 'active' : ''}
        aria-pressed={item.key === competition.key}
        onClick={() => {
          if (item.key !== selectedCompetition) {
            setState('loading')
            setSelectedCompetition(item.key)
          }
        }}
      >{item.name}</button>)}
    </nav>

    <section className="model-matchup" aria-label={`${competition.name} model`}>
      <div className="model-matchup-toolbar">
        <div><p className="eyebrow">Score-only baseline</p><h2>{competition.name} projections</h2></div>
        {state === 'ready' && upcoming.length > 0 && <label className="model-matchup-select">
          Matchup
          <select value={matchup?.event_id ?? ''} onChange={(event) => setSelectedEvent(event.target.value)}>
            {upcoming.map((event) => <option key={event.event_id} value={event.event_id}>
              {event.away_name} at {event.home_name} · {easternTime(event.start_at)} ET
            </option>)}
          </select>
        </label>}
      </div>
      {state === 'loading' && <p role="status">Loading {competition.name} model data…</p>}
      {state === 'error' && <p role="alert">Model data could not be loaded. {error} <button type="button" onClick={() => { setState('loading'); setRetry((value) => value + 1) }}>Retry</button></p>}
      {state === 'ready' && unresolved > 0 && <p role="status">{unresolved} provider games excluded because their teams are unresolved. They are not used in projections or history samples.</p>}
      {state === 'ready' && !matchup && <p>No upcoming {competition.name} games are available in the cache.</p>}
      {state === 'ready' && matchup && !projection && <div className="model-empty-state">
        <h3>Not enough history for a projection yet.</h3>
        <p>{historicalGames} completed games are available before this matchup. The baseline requires at least 3 scored games for each team and 30 scored games in the competition.</p>
        <p>Historical seasons are backfilled by the data sync. Check again after the sync completes.</p>
      </div>}
      {state === 'ready' && matchup && projection && <article className="model-projection-card">
        <p className="model-game-label">{matchup.away_name} at {matchup.home_name}</p>
        <div className="model-scoreline" aria-label="Projected score">
          <div><span>{matchup.away_name}</span><strong>{projection.awayScore.toFixed(1)}</strong></div>
          <div><span>{matchup.home_name}</span><strong>{projection.homeScore.toFixed(1)}</strong></div>
        </div>
        <div className="model-win-probabilities">
          <div><span>{matchup.home_name} estimated win</span><strong>{percent(projection.homeWinProbability)}</strong></div>
          <div><span>{matchup.away_name} estimated win</span><strong>{percent(1 - projection.homeWinProbability)}</strong></div>
        </div>
        <p className="model-sample">Recent team samples (up to 10 scored games): {matchup.home_name} {projection.homeSample} · {matchup.away_name} {projection.awaySample}. League margin sample: {projection.leagueSample}.</p>
        <p className="model-sample">Source data last synced: {easternTime(projection.lastUpdated)} ET.</p>
      </article>}
    </section>

    <section className="model-methodology">
      <p className="eyebrow">How this baseline works</p>
      <h2>Past scores only. No hidden inputs.</h2>
      <p>Projected scores blend each team's average points scored across its 10 most recent completed games with its opponent's average allowed. Win probability applies the historical competition scoring-margin distribution to that projected margin. Only games before the selected start time are used; the previous and current seasons are included when available.</p>
      <p>It does not account for injuries, player availability, home advantage, weather, lineups, sportsbook odds, market movement, or tie/draw probability. Estimates have not been calibrated or independently validated. Samples and source sync time are shown so missing or stale data is visible.</p>
      <Link to="/nfl/lab">Open NFL matchup history and odds research</Link>
    </section>
  </main>
}
