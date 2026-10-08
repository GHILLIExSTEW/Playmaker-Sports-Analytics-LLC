import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchNflGames, type NflGame } from './nflData'
import { downloadResearchCsv, oddsResearch, teamResearch, validateResearchGames, type TeamResearch } from './nflResearch'

function timestamp(value: string) {
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/New_York' }).format(new Date(value))
}
function TeamPanel({ name, stats }: { name: string; stats: TeamResearch }) {
  return <article className="nfl-lab-team">
    <h3>{name}</h3>
    <dl>
      <dt>Completed-game sample</dt><dd>{stats.games}</dd>
      <dt>Record (W-L-T)</dt><dd>{stats.games ? `${stats.wins}-${stats.losses}-${stats.ties}` : 'No sample'}</dd>
      <dt>Points scored / game</dt><dd>{stats.pointsFor?.toFixed(1) ?? 'Unavailable'}</dd>
      <dt>Points allowed / game</dt><dd>{stats.pointsAgainst?.toFixed(1) ?? 'Unavailable'}</dd>
      <dt>Last 5 (newest first)</dt><dd>{stats.recent}</dd>
      <dt>Home record</dt><dd>{stats.homeRecord === '0-0-0' ? 'No sample' : stats.homeRecord}</dd>
      <dt>Away record</dt><dd>{stats.awayRecord === '0-0-0' ? 'No sample' : stats.awayRecord}</dd>
      <dt>Days between kickoffs</dt><dd>{stats.daysBetween?.toFixed(1) ?? 'Unavailable'}</dd>
    </dl>
  </article>
}

export default function NflMatchupLab() {
  const [games, setGames] = useState<NflGame[]>([])
  const [selected, setSelected] = useState('')
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [odds, setOdds] = useState('-110')
  const [stake, setStake] = useState('100')
  const [probability, setProbability] = useState('')
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const data = validateResearchGames(await fetchNflGames())
        if (!cancelled) { setGames(data); setState('ready'); setError('') }
      } catch (failure) {
        if (cancelled) return
        const detail = failure && typeof failure === 'object' && 'message' in failure && typeof failure.message === 'string' ? failure.message : 'Please retry.'
        setError(`NFL research data could not be loaded. ${detail}`)
        setState('error')
      }
    })()
    return () => { cancelled = true }
  }, [retry])
  const matchups = games.filter((game) => ['NS', 'TBD', 'FT', 'AOT'].includes(game.status_short))
  const defaultGame = matchups.find((game) => ['NS', 'TBD'].includes(game.status_short)) ?? matchups.at(-1)
  const matchup = matchups.find((game) => String(game.game_id) === selected) ?? defaultGame
  const home = matchup ? teamResearch(games, matchup, matchup.home_team_id) : null
  const away = matchup ? teamResearch(games, matchup, matchup.away_team_id) : null
  let calculation: ReturnType<typeof oddsResearch> | null = null
  let calculationError = ''
  try { calculation = oddsResearch(odds, stake, probability) }
  catch (failure) { calculationError = failure instanceof Error ? failure.message : 'Check the calculator inputs.' }

  function exportMatchup() {
    if (!matchup || !home || !away) return
    downloadResearchCsv(`nfl-matchup-${matchup.game_id}.csv`,
      ['game_id','season','stage','kickoff_utc','team','side','completed_games','wins','losses','ties','points_for_per_game','points_against_per_game','recent_newest_first','home_record','away_record','previous_kickoff_utc','days_between_kickoffs','source_synced_at'],
      [[matchup.away_team_name, 'away', away] as const, [matchup.home_team_name, 'home', home] as const].map(([name, side, stats]) => [
        matchup.game_id, matchup.season, matchup.stage, matchup.kickoff_at, name, side,
        stats.games, stats.wins, stats.losses, stats.ties, stats.pointsFor, stats.pointsAgainst,
        stats.recent, stats.homeRecord, stats.awayRecord, stats.previousKickoff, stats.daysBetween, matchup.synced_at,
      ]))
  }
  return <main className="nfl-page nfl-matchup-lab">
    <Link to="/nfl">NFL scores & schedule</Link>
    <header className="nfl-page-heading"><p className="eyebrow">Research tools · No guaranteed outcomes</p><h1>NFL matchup lab.</h1>
      <p>Compare completed-game history and calculate what a price requires to break even. This is descriptive research, not a prediction model or betting recommendation.</p>
    </header>
    <section aria-label="Matchup research">
      <h2>Matchup research</h2>
      {state === 'loading' && <p role="status">Loading NFL research...</p>}
      {state === 'error' && <p role="alert">{error} <button type="button" onClick={() => { setState('loading'); setRetry((value) => value + 1) }}>Retry research</button></p>}
      {state === 'ready' && !matchup && <p>No eligible NFL matchups have synced yet.</p>}
      {state === 'ready' && matchup && home && away && <>
        <label className="nfl-lab-input">Choose matchup<select value={String(matchup.game_id)} onChange={(event) => setSelected(event.target.value)}>
          {matchups.map((game) => <option key={game.game_id} value={game.game_id}>{game.away_team_name} at {game.home_team_name} · {timestamp(game.kickoff_at)} ET</option>)}
        </select></label>
        <p>Season {matchup.season} · {matchup.stage ?? 'Stage unavailable'} · {matchup.week ?? 'Week unavailable'}</p>
        <p>Kickoff: {timestamp(matchup.kickoff_at)} ET. Matchup record synced: {timestamp(matchup.synced_at)} ET.</p>
        <p>Venue: {matchup.venue_name || 'Unavailable'}{matchup.venue_city ? `, ${matchup.venue_city}` : ''}</p>
        <p>Source: existing API-Sports NFL cache. History is limited to this season and stage, strictly before the selected kickoff; completed FT/AOT games with both scores only. Canceled games are excluded. Days between kickoffs is not a verified rest/injury assessment.</p>
        {(matchup.stage === null || matchup.home_team_id === null || matchup.away_team_id === null) && <p role="status">Stage or team identity is missing; affected history is unavailable.</p>}
        <p>Oldest cached game sync: {timestamp(games.reduce((oldest, game) => Date.parse(game.synced_at) < Date.parse(oldest) ? game.synced_at : oldest, matchup.synced_at))} ET. Data may be delayed or incomplete.</p>
        <div className="nfl-lab-teams"><TeamPanel name={matchup.away_team_name} stats={away} /><TeamPanel name={matchup.home_team_name} stats={home} /></div>
        <p>Injury reports, weather, player projections, live sportsbook odds, and odds movement are not available in this release. Historical matchup views are not point-in-time backtests: source scores may have been corrected later.</p>
        <div className="nfl-lab-actions"><button type="button" onClick={exportMatchup}>Export matchup CSV</button>
          <button type="button" onClick={() => downloadResearchCsv('nfl-season-games.csv',
            ['game_id','season','stage','week','kickoff_utc','away_team','home_team','away_score','home_score','status','synced_at'],
            games.map((game) => [game.game_id,game.season,game.stage,game.week,game.kickoff_at,game.away_team_name,game.home_team_name,game.away_score,game.home_score,game.status_short,game.synced_at]))}>Export season games CSV</button></div>
      </>}
    </section>
    <section className="nfl-lab-calculator" aria-label="Odds calculator">
      <h2>Odds & break-even calculator</h2>
      <div className="nfl-lab-inputs">
        <label className="nfl-lab-input">American odds<input inputMode="numeric" value={odds} onChange={(event) => setOdds(event.target.value)} /></label>
        <label className="nfl-lab-input">Stake (units or dollars)<input type="number" min="0.01" max="1000000" step="any" value={stake} onChange={(event) => setStake(event.target.value)} /></label>
        <label className="nfl-lab-input">Your estimated win probability (%) — optional<input type="number" min="0" max="100" step="any" value={probability} onChange={(event) => setProbability(event.target.value)} /></label>
      </div>
      {calculationError && <p role="alert">{calculationError}</p>}
      {calculation && <div aria-live="polite">
        <p>Decimal odds: <strong>{calculation.decimal.toFixed(4)}</strong></p>
        <p>Implied probability / break-even: <strong>{(calculation.implied * 100).toFixed(2)}%</strong></p>
        <p>Profit if won (excluding stake): <strong>{calculation.profit.toFixed(2)}</strong> · Total return: <strong>{(calculation.profit + calculation.stake).toFixed(2)}</strong></p>
        {calculation.expectedValue !== null && <p>Expected profit per bet using YOUR estimate: <strong>{calculation.expectedValue.toFixed(2)}</strong> in the same units as the stake. This is not a model forecast.</p>}
        <button type="button" onClick={() => downloadResearchCsv('nfl-odds-calculation.csv',
          ['american_odds','stake','decimal_odds','implied_probability','profit_if_won','user_probability','expected_profit'],
          [[calculation.odds,calculation.stake,calculation.decimal,calculation.implied,calculation.profit,calculation.probability,calculation.expectedValue]])}>Export calculation CSV</button>
      </div>}
      <p>Prices are entered manually. Implied probability includes the bookmaker margin; this is not a no-vig probability. Calculations assume a win/loss market with no push, void, fees or tax. Your estimated probability is an assumption, not verified evidence.</p>
    </section>
    <p>Informational research only. No model edge or guaranteed return is claimed. Do not wager money you cannot afford to lose.</p>
  </main>
}
