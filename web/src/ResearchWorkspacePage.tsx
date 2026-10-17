import { useEffect, useState } from 'react'
import { modelCompetitions } from './sportModels'
import { fetchPerformance, fetchWorkspace, requestRefresh, score, type PerformanceRow, type ResearchGame, type Workspace } from './researchWorkspace'
import './ResearchWorkspace.css'

const date = (value: string) => new Date(value).toLocaleString()
const number = (value: number | null, digits = 1) => value === null ? 'Unavailable' : value.toFixed(digits)
const errorMessage = (error: unknown) => error instanceof Error ? error.message
  : typeof error === 'object' && error !== null && 'message' in error ? String(error.message) : 'Research request failed.'
const tabs = ['Matchup', 'Players', 'Odds', 'Performance'] as const
type Tab = typeof tabs[number]

function TeamForm({ team, name, history }: { team: string | null; name: string; history: ResearchGame[] }) {
  const games = history.filter(row => team !== null && (row.home_id === team || row.away_id === team)).slice(0, 10)
  const completed = games.flatMap(row => {
    const home = score(row.home_score), away = score(row.away_score)
    return home === null || away === null ? [] : [{ ...row, own: row.home_id === team ? home : away, opponent: row.home_id === team ? away : home }]
  })
  return <article className="research-card"><h3>{name}</h3>
    <p>Last {completed.length} cached scored games before selected kickoff</p>
    <p>{completed.filter(row => row.own > row.opponent).length} wins / {completed.filter(row => row.own < row.opponent).length} losses / {completed.filter(row => row.own === row.opponent).length} ties</p>
    <p>Scored {number(completed.length ? completed.reduce((sum, row) => sum + row.own, 0) / completed.length : null)} / allowed {number(completed.length ? completed.reduce((sum, row) => sum + row.opponent, 0) / completed.length : null)} per game</p>
    <ul>{games.map(row => <li key={row.game_id}>{new Date(row.start_at).toLocaleDateString()}: {row.away_name} {number(score(row.away_score), 0)} at {row.home_name} {number(score(row.home_score), 0)}</li>)}</ul>
  </article>
}

function Performance({ rows }: { rows: PerformanceRow[] }) {
  const [asOf] = useState(() => Date.now())
  const [capper, setCapper] = useState('All'), [sport, setSport] = useState('All'), [market, setMarket] = useState('All')
  const [days, setDays] = useState(30)
  const filtered = rows.filter(row => (capper === 'All' || row.capper === capper) && (sport === 'All' || row.sport === sport)
    && (market === 'All' || row.market === market) && (!days || Date.parse(row.settled_at) >= asOf - days * 86400000))
  const groups = [...new Set(filtered.map(row => row.capper))].sort()
  const summary = (items: PerformanceRow[]) => {
    const net = items.reduce((sum, row) => sum + row.net_units, 0)
    const stake = items.filter(row => row.status !== 'void').reduce((sum, row) => sum + row.units, 0)
    return { net, stake, roi: stake > 0 ? 100 * net / stake : null }
  }
  const totals = summary(filtered)
  return <section><h2>Capper performance</h2>
    <p>Published, settled capper plays only; one record per bet, not per parlay leg. Latest 10,000 results. Voids excluded from ROI stake. Partial results retain the ledger's net-unit settlement convention.</p>
    <div className="research-controls">
      {(['Capper', 'Sport', 'Market'] as const).map((label, index) => {
        const key = (['capper', 'sport', 'market'] as const)[index]
        const selected = [capper, sport, market][index], setter = [setCapper, setSport, setMarket][index]
        return <label key={key}>{label}<select aria-label={label} value={selected} onChange={event => setter(event.target.value)}>
          <option>All</option>{[...new Set(rows.map(row => row[key]))].sort().map(value => <option key={value}>{value}</option>)}
        </select></label>
      })}
      <label>Settled window<select value={days} onChange={event => setDays(Number(event.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={0}>All cached results</option></select></label>
    </div>
    <p>{filtered.length} bets / {number(totals.stake, 2)}u stake / {number(totals.net, 2)}u net / {number(totals.roi)}% ROI</p>
    <div className="research-table"><table><thead><tr><th>Capper</th><th>W-L-Partial-Void</th><th>Stake (u)</th><th>Net (u)</th><th>ROI</th></tr></thead>
      <tbody>{groups.map(name => {
        const items = filtered.filter(row => row.capper === name), result = summary(items)
        return <tr key={name}><th>{name}</th><td>{['win','loss','partial','void'].map(status => items.filter(row => row.status === status).length).join('-')}</td><td>{number(result.stake, 2)}</td><td>{number(result.net, 2)}</td><td>{number(result.roi)}%</td></tr>
      })}</tbody></table></div>
    {!filtered.length && <p>No published capper results match these filters.</p>}
    <h3>Recent trend</h3><div className="research-table"><table><thead><tr><th>Settled</th><th>Capper</th><th>Sport / market</th><th>Result</th><th>Net (u)</th></tr></thead><tbody>
      {filtered.slice(0, 30).map((row, index) => <tr key={index}><td>{date(row.settled_at)}</td><td>{row.capper}</td><td>{row.sport} / {row.market}</td><td>{row.status}</td><td>{number(row.net_units, 2)}</td></tr>)}
    </tbody></table></div>
  </section>
}

export default function ResearchWorkspacePage() {
  const [competition, setCompetition] = useState('nfl'), [gameId, setGameId] = useState('')
  const [tab, setTab] = useState<Tab>('Matchup'), [revision, setRevision] = useState(0)
  const [data, setData] = useState<Workspace | null>(null), [performance, setPerformance] = useState<PerformanceRow[] | null>(null)
  const [error, setError] = useState(''), [performanceError, setPerformanceError] = useState('')
  const [loading, setLoading] = useState(true), [requesting, setRequesting] = useState(false), [notice, setNotice] = useState('')
  const [player, setPlayer] = useState(''), [stat, setStat] = useState(''), [threshold, setThreshold] = useState('')
  const [split, setSplit] = useState('all'), [oddsMarket, setOddsMarket] = useState('')
  useEffect(() => {
    let active = true
    fetchWorkspace(competition, gameId || null).then(value => { if (active) { setData(value); setLoading(false) } })
      .catch(reason => { if (active) { setError(errorMessage(reason)); setLoading(false) } })
    return () => { active = false }
  }, [competition, gameId, revision])
  useEffect(() => {
    if (tab !== 'Performance') return
    let active = true
    fetchPerformance().then(value => { if (active) { setPerformance(value); setPerformanceError('') } })
      .catch(reason => { if (active) setPerformanceError(errorMessage(reason)) })
    return () => { active = false }
  }, [tab, revision])
  useEffect(() => {
    if (!data?.refreshes.some(row => row.state === 'queued' || row.state === 'running')) return
    const timer = window.setTimeout(() => setRevision(value => value + 1), 5000)
    return () => window.clearTimeout(timer)
  }, [data])
  const selected = data?.games.find(row => row.game_id === gameId)
  const reload = () => { setLoading(true); setError(''); setRevision(value => value + 1) }
  const refresh = async (kind: string) => {
    setRequesting(true); setNotice(''); setError('')
    try { await requestRefresh(competition, gameId, kind); setNotice('Refresh queued. The bot worker must be running; status appears below.'); setRevision(value => value + 1) }
    catch (reason) { setError(errorMessage(reason)) }
    finally { setRequesting(false) }
  }
  const players = data?.players ?? []
  const playerKey = (row: typeof players[number]) => `${row.playerId}:${row.team}`
  const names = [...new Map(players.map(row => [playerKey(row), `${row.player} (${row.team})`])).entries()]
  const logs = players.filter(row => playerKey(row) === player && (!selected || Date.parse(row.date) < Date.parse(selected.start_at))
    && (!selected || split === 'all' || (split === 'head-to-head'
      ? data?.history.some(game => game.game_id === row.game && [game.home_id, game.away_id].includes(selected.home_id) && [game.home_id, game.away_id].includes(selected.away_id))
      : data?.history.some(game => game.game_id === row.game && (split === 'home' ? game.home_name : game.away_name) === row.team))))
  const stats = [...new Set(logs.flatMap(row => Object.keys(row.stats)))].sort()
  const values = logs.flatMap(row => row.stats[stat] === undefined ? [] : [row.stats[stat]])
  const line = threshold.trim() ? Number(threshold) : NaN
  const lines = (data?.lines ?? []).filter(row => !oddsMarket || row.market === oddsMarket)
  const h2h = selected ? data?.history.filter(row => [row.home_id, row.away_id].includes(selected.home_id) && [row.home_id, row.away_id].includes(selected.away_id)).slice(0, 10) ?? [] : []
  return <section className="research-workspace">
    <header><p className="eyebrow">Team research</p><h1>Capper research workspace</h1>
      <p>Cached research across six leagues. Provider coverage and model estimates are not guarantees. The game selector shows up to 200 games from the past seven days through the next 14 days.</p></header>
    <div className="research-tabs" role="tablist" aria-label="Research sections">{tabs.map(value =>
      <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{value}</button>)}</div>
    {tab !== 'Performance' && <div className="research-controls">
      <label>League<select aria-label="League" value={competition} onChange={event => {
        setCompetition(event.target.value); setGameId(''); setData(null); setLoading(true); setError(''); setPlayer(''); setStat(''); setOddsMarket(''); setNotice('')
      }}>{modelCompetitions.map(item => <option key={item.engineKey} value={item.engineKey}>{item.name}</option>)}</select></label>
      <label>Game<select aria-label="Game" value={gameId} disabled={loading} onChange={event => {
        setGameId(event.target.value); setData(null); setLoading(true); setError(''); setPlayer(''); setStat(''); setOddsMarket(''); setNotice('')
      }}><option value="">Select a matchup</option>{data?.games.map(row => <option key={row.game_id} value={row.game_id}>{date(row.start_at)} - {row.away_name} at {row.home_name}</option>)}</select></label>
      <button onClick={reload} disabled={loading}>Reload cache</button>
    </div>}
    {error && <p role="alert">{error} <button onClick={reload}>Retry cached lookup</button></p>}
    {notice && <p role="status">{notice}</p>}
    {loading && tab !== 'Performance' && <p role="status">Loading cached research...</p>}
    {!loading && !error && tab !== 'Performance' && !data?.games.length && <p>No cached games in this league's recent/upcoming window. No live API calls were made.</p>}
    {tab === 'Performance' ? performanceError ? <p role="alert">{performanceError} <button onClick={reload}>Retry</button></p>
      : performance ? <Performance rows={performance} /> : <p role="status">Loading capper results...</p>
      : selected && data && <>
        <h2>{selected.away_name} at {selected.home_name}</h2><p>{date(selected.start_at)} / {selected.status} / Game cache: {date(selected.synced_at)}</p>
        <p>Live refresh: five-minute event/type cooldown, one request per user per minute, plus existing provider/member daily limits. Non-football odds reserve five requests upfront and scan at most five league pages; unused reservations are not refunded. Other refreshes reserve one. Failed or unavailable calls can still consume budget. No automatic provider retries.</p>
        <button disabled={requesting || (tab === 'Players' && ['baseball:1','hockey:57'].includes(competition))} onClick={() => void refresh(tab === 'Players' ? 'players' : tab === 'Odds' ? 'odds' : 'game')}>Request live {tab === 'Players' ? 'player stats' : tab === 'Odds' ? 'odds' : 'game'} refresh</button>
        {tab === 'Matchup' && <section>
          <div className="research-grid"><TeamForm team={selected.home_id} name={selected.home_name} history={data.history} /><TeamForm team={selected.away_id} name={selected.away_name} history={data.history} /></div>
          <h3>Head-to-head</h3>{h2h.length ? <ul>{h2h.map(row => <li key={row.game_id}>{date(row.start_at)}: {row.away_name} {number(score(row.away_score), 0)} at {row.home_name} {number(score(row.home_score), 0)}</li>)}</ul> : <p>No cached head-to-head games before selected kickoff.</p>}
          <h3>Model snapshot</h3>{data.projection ? <p>Projected score {number(score(data.projection.away_score))} - {number(score(data.projection.home_score))}; home win probability {number(score(data.projection.home_probability) === null ? null : Number(data.projection.home_probability) * 100)}%. Generated {date(String(data.projection.generated_at))}.</p> : <p>No model projection available.</p>}
          <h3>Injuries</h3><p>Injury ingestion currently covers NFL only, on the scheduled model-input refresh. No records does not mean a healthy roster. Game refresh does not refresh injuries.</p>
          {data.injuries.length ? <ul>{data.injuries.map((row, index) => <li key={index}>{String(row.player_name)}: {String(row.status)} - {String(row.description ?? 'No description')} / Updated {date(String(row.fetched_at))}</li>)}</ul> : <p>No current cached injury reports.</p>}
        </section>}
        {tab === 'Players' && <section><h3>Player logs and prop thresholds</h3>
          <p>Cached snapshots from up to ten team games; only games before the selected kickoff enter averages. This is not a complete season sample or a live player-prop line feed.</p>
          {competition === 'baseball:1' || competition === 'hockey:57' ? <p>Individual player logs are unavailable with the configured provider adapter for this league.</p>
            : !players.length ? <p>No cached player statistics. Select a recent completed game and request a player refresh to populate logs. Upcoming games may not have statistics.</p> : <>
              <div className="research-controls"><label>Player<select aria-label="Player" value={player} onChange={event => { setPlayer(event.target.value); setStat('') }}><option value="">Select a player</option>{names.map(([key,name]) => <option key={key} value={key}>{name}</option>)}</select></label>
                <label>Split<select value={split} onChange={event => setSplit(event.target.value)}><option value="all">All cached games</option><option value="home">Home</option><option value="away">Away</option><option value="head-to-head">Head-to-head</option></select></label>
                <label>Statistic<select aria-label="Statistic" value={stat} onChange={event => setStat(event.target.value)}><option value="">Select a statistic</option>{stats.map(value => <option key={value}>{value}</option>)}</select></label>
                <label>Prop threshold<input type="number" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></label></div>
              <p>{values.length} observations / average {number(values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null)}{Number.isFinite(line) && <> / Over {values.filter(value => value > line).length}, Under {values.filter(value => value < line).length}, Push {values.filter(value => value === line).length} at {line}</>}</p>
              <ul>{logs.map(row => <li key={`${row.game}:${row.playerId}`}>{date(row.date)}: {stat || 'Select statistic'} {number(row.stats[stat] ?? null)} / cache {date(row.updated)}</li>)}</ul>
            </>}
        </section>}
        {tab === 'Odds' && <section><h3>Sportsbook line comparison</h3><p>{data.oddsUpdated ? `Odds cached ${date(data.oddsUpdated)}.` : 'No book-by-book odds snapshot. Request a refresh; availability depends on provider subscription.'} Decimal odds; implied probability includes bookmaker margin. No de-vig or line-equivalence assumption.</p>
          <label>Market<select value={oddsMarket} onChange={event => setOddsMarket(event.target.value)}><option value="">All markets</option>{[...new Set(data.lines.map(row => row.market))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
          <p>The model's consensus edge is displayed separately below. Bookmaker-specific edges are not inferred across potentially different market settlement rules.</p>
          <div className="research-table"><table><thead><tr><th>Book</th><th>Market</th><th>Selection</th><th>Decimal</th><th>Implied</th></tr></thead><tbody>{lines.map((row,index) => <tr key={index}><td>{row.book}</td><td>{row.market}</td><td>{row.selection}</td><td>{row.decimal.toFixed(2)}</td><td>{(100 / row.decimal).toFixed(1)}%</td></tr>)}</tbody></table></div>
          {data.projection && <p>Model home-win probability: {number(score(data.projection.home_probability) === null ? null : Number(data.projection.home_probability) * 100)}%. Consensus home-market edge: {number(score(data.projection.market_edge) === null ? null : Number(data.projection.market_edge) * 100)} percentage points. This snapshot is separate from the bookmaker quotes above; compare timestamps before use.</p>}
        </section>}
        <h3>Refresh activity</h3>{data.refreshes.length ? <ul aria-live="polite">{data.refreshes.map(row => <li key={row.id}>{row.game_id} / {row.kind}: {row.state} ({date(row.requested_at)}){row.result && ` - ${row.result}`}</li>)}</ul> : <p>No refresh requests from this account.</p>}
      </>}
  </section>
}
