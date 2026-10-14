import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchModelBacktest, fetchModelProjections, modelCompetitions, type ModelBacktest, type ModelProjection,
} from './sportModels'

function easternTime(value: string): string {
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/New_York',
  }).format(new Date(value))
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function signed(value: number): string {
  const text = value.toFixed(1)
  return text.startsWith('-') ? text : `+${text}`
}

function MarketPanel({ game }: { game: ModelProjection }) {
  if (game.market_books === null) {
    return <p className="model-sample">Market: no sportsbook consensus is stored for this game yet.</p>
  }
  const edge = game.market_edge
  return <div className="model-market" aria-label="Market comparison">
    <h3>Market comparison · {game.market_books} books</h3>
    <dl>
      {game.market_spread !== null && <div><dt>Spread</dt><dd>{game.home_name} {signed(game.market_spread)}</dd></div>}
      {game.market_total !== null && <div><dt>Total</dt><dd>{game.market_total.toFixed(1)}</dd></div>}
      {game.market_home_probability !== null && <div><dt>{game.home_name} no-vig</dt><dd>{percent(game.market_home_probability)}</dd></div>}
      {edge !== null && <div><dt>Model edge</dt><dd>{edge >= 0 ? game.home_name : game.away_name} +{(Math.abs(edge) * 100).toFixed(1)} pts</dd></div>}
      {game.cover_probability !== null && <div><dt>{game.home_name} cover</dt><dd>{percent(game.cover_probability)}</dd></div>}
      {game.over_probability !== null && <div><dt>Over</dt><dd>{percent(game.over_probability)}</dd></div>}
    </dl>
    {game.market_fetched_at && <p className="model-sample">Odds fetched {easternTime(game.market_fetched_at)} ET.</p>}
  </div>
}

function InputsPanel({ game }: { game: ModelProjection }) {
  const details = game.details
  const items: string[] = []
  const [homeRest, awayRest] = details.rest_days ?? [null, null]
  if (homeRest !== null || awayRest !== null) {
    items.push(`Rest: ${game.away_name} ${awayRest ?? '-'}d · ${game.home_name} ${homeRest ?? '-'}d`)
  }
  const [homeTravel, awayTravel] = details.travel_km ?? [null, null]
  if (awayTravel) items.push(`${game.away_name} travel ${Math.round(awayTravel).toLocaleString('en-US')} km`)
  if (homeTravel) items.push(`${game.home_name} travel ${Math.round(homeTravel).toLocaleString('en-US')} km`)
  const adjustments = [...(details.margin_adjustments ?? []), ...(details.total_adjustments ?? [])]
  if (adjustments.length) items.push(`Adjustments: ${adjustments.map(([name, value]) => `${name} ${signed(value)}`).join(', ')}`)
  if (details.weather) {
    const weather = details.weather
    items.push(`Weather (${weather.source}): ${Math.round(weather.temperature_f)}°F, wind ${Math.round(weather.wind_mph)} mph, precipitation ${weather.precipitation_mm.toFixed(1)} mm`)
  }
  const injuries = details.injuries ?? []
  return <div className="model-inputs" aria-label="Model inputs">
    <h3>Inputs</h3>
    <ul>{(items.length ? items : ['No rest, travel, efficiency or weather adjustment applied.']).map((item) => <li key={item}>{item}</li>)}</ul>
    {injuries.length > 0 && <>
      <h3>Injury adjustment (heuristic)</h3>
      <ul>{injuries.map((injury) => <li key={`${injury.team_id}-${injury.player}`}>
        {injury.team_id === game.home_id ? game.home_name : game.away_name}: {injury.player} {injury.position ?? ''} {injury.status} (−{injury.points.toFixed(1)} pts)
      </li>)}</ul>
    </>}
  </div>
}

function TrackRecord({ report, name }: { report: ModelBacktest | null; name: string }) {
  if (!report) {
    return <p className="model-sample">No walk-forward backtest has been stored for {name} yet.</p>
  }
  const model = report.model ?? {}
  const baseline = report.baseline ?? {}
  const row = (label: string, value?: number | null, old?: number | null, format = (v: number) => v.toFixed(3)) =>
    <tr key={label}><th scope="row">{label}</th><td>{value == null ? '–' : format(value)}</td><td>{old == null ? '–' : format(old)}</td></tr>
  return <div className="model-track-record">
    <p className="model-sample">{report.games.toLocaleString('en-US')} holdout games{report.window ? ` from ${easternTime(report.window.start)} to ${easternTime(report.window.end)} ET` : ''}. Lower Brier, log loss and MAE are better.</p>
    <table>
      <thead><tr><th scope="col">Metric</th><th scope="col">Ratings model</th><th scope="col">Old score-only baseline</th></tr></thead>
      <tbody>
        {row('Brier score', model.brier, baseline.brier)}
        {row('Log loss', model.log_loss, baseline.log_loss)}
        {row('Winner accuracy', model.accuracy, baseline.accuracy, percent)}
        {row('Margin MAE', model.margin_mae, baseline.margin_mae, (v) => v.toFixed(2))}
        {row('Total MAE', model.total_mae, baseline.total_mae, (v) => v.toFixed(2))}
      </tbody>
    </table>
    {report.calibration && report.calibration.length > 0 && <table aria-label="Calibration">
      <thead><tr><th scope="col">Favorite probability</th><th scope="col">Games</th><th scope="col">Predicted</th><th scope="col">Actual</th></tr></thead>
      <tbody>{report.calibration.map((bucket) => <tr key={bucket.low}>
        <th scope="row">{percent(bucket.low)}–{percent(bucket.high)}</th><td>{bucket.games}</td>
        <td>{percent(bucket.predicted)}</td><td>{percent(bucket.actual)}</td>
      </tr>)}</tbody>
    </table>}
  </div>
}

export default function DataModelsPage() {
  const [selectedCompetition, setSelectedCompetition] = useState(modelCompetitions[0].key)
  const [games, setGames] = useState<ModelProjection[]>([])
  const [backtest, setBacktest] = useState<ModelBacktest | null>(null)
  const [selectedGame, setSelectedGame] = useState('')
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const competition = modelCompetitions.find((item) => item.key === selectedCompetition) ?? modelCompetitions[0]

  useEffect(() => {
    let cancelled = false
    void Promise.all([
      fetchModelProjections(competition),
      fetchModelBacktest(competition).catch(() => null),
    ]).then(([projections, report]) => {
      if (cancelled) return
      setGames(projections)
      setBacktest(report)
      setSelectedGame((selected) => projections.some((game) => game.game_id === selected) ? selected : '')
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

  const availableGames = games
    .filter((game) => Date.parse(game.start_at) + 60 * 60 * 1000 > now)
    .sort((first, second) => Date.parse(first.start_at) - Date.parse(second.start_at))
  const game = availableGames.find((item) => item.game_id === selectedGame) ?? availableGames[0]
  const margin = game ? game.home_score - game.away_score : 0

  return <main className="data-models-page">
    <Link to="/" className="capper-back-link">Home</Link>
    <header className="data-models-heading">
      <p className="eyebrow">Playmaker research · Experimental</p>
      <h1>Sports data models.</h1>
      <p>Opponent-adjusted ratings projections for upcoming games across professional and college sports, compared with the betting market and tracked with a walk-forward backtest. Estimates are experimental and are not betting recommendations.</p>
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
            setSelectedGame('')
            setSelectedCompetition(item.key)
          }
        }}
      >{item.name}</button>)}
    </nav>

    <section className="model-matchup" aria-label={`${competition.name} model`}>
      <div className="model-matchup-toolbar">
        <div><p className="eyebrow">Ratings model</p><h2>{competition.name} projections</h2></div>
        {state === 'ready' && availableGames.length > 0 && <label className="model-matchup-select">
          Matchup
          <select value={game?.game_id ?? ''} onChange={(event) => setSelectedGame(event.target.value)}>
            {availableGames.map((item) => <option key={item.game_id} value={item.game_id}>
              {item.away_name} at {item.home_name} · {easternTime(item.start_at)} ET
            </option>)}
          </select>
        </label>}
      </div>
      {state === 'loading' && <p role="status">Loading {competition.name} model data…</p>}
      {state === 'error' && <p role="alert">Model data could not be loaded. {error} <button type="button" onClick={() => { setState('loading'); setRetry((value) => value + 1) }}>Retry</button></p>}
      {state === 'ready' && !game && <div className="model-empty-state">
        <h3>No {competition.name} projections are available.</h3>
        <p>Projections are generated every 30 minutes for games in the next 14 days once each team has at least 3 scored games and the league has 30. Games with too little history are left out rather than given a manufactured probability.</p>
      </div>}
      {state === 'ready' && game && <article className="model-projection-card">
        <p className="model-game-label">{game.away_name} at {game.home_name}</p>
        <div className="model-scoreline" aria-label="Projected score">
          <div><span>{game.away_name}</span><strong>{game.away_score.toFixed(1)}</strong></div>
          <div><span>{game.home_name}</span><strong>{game.home_score.toFixed(1)}</strong></div>
        </div>
        <div className="model-win-probabilities">
          <div><span>{game.home_name} estimated win</span><strong>{percent(game.home_probability)}</strong></div>
          <div><span>{game.away_name} estimated win</span><strong>{percent(1 - game.home_probability)}</strong></div>
        </div>
        <p className="model-summary">Projected total {(game.home_score + game.away_score).toFixed(1)} · {margin >= 0 ? game.home_name : game.away_name} by {Math.abs(margin).toFixed(1)}</p>
        <MarketPanel game={game} />
        <InputsPanel game={game} />
        <p className="model-sample">Prior-game samples: {game.home_name} {game.home_sample} · {game.away_name} {game.away_sample}. League sample: {game.league_sample}.</p>
        <p className="model-sample">Source data last synced: {easternTime(game.data_updated_at)} ET · Projection generated {easternTime(game.generated_at)} ET.</p>
      </article>}
    </section>

    <section className="model-methodology">
      <p className="eyebrow">Track record</p>
      <h2>Walk-forward backtest.</h2>
      {state === 'ready' && <TrackRecord report={backtest} name={competition.name} />}
    </section>

    <section className="model-methodology">
      <p className="eyebrow">How this model works</p>
      <h2>Ratings, context and the market.</h2>
      <p>Each team carries offensive and defensive ratings that update after every game, adjusted for opponent strength and home advantage, with ratings partially regressed between seasons. Learned adjustments cover rest days, travel distance, outdoor weather (Open-Meteo) and, for the NFL, play-by-play efficiency (EPA) when it improves the backtest. NFL projections are reduced for players listed Out or Doubtful using fixed position weights. Hockey and baseball use a Poisson score model; football and basketball use a calibrated normal margin.</p>
      <p>Every game is projected using only information available before it started. Parameters are tuned on the earlier half of games and scored on the later half, compared with the old score-only baseline. Sportsbook consensus lines are shown for comparison and are not inputs to the projection. Injury, lineup and weather data can be incomplete, and estimates are not guaranteed.</p>
      {competition.key === 'nfl' && <Link to="/nfl/lab">Open NFL matchup history and odds research</Link>}
    </section>
  </main>
}
