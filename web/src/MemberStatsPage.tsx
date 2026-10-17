import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMemberAccess } from './memberAccessContext'
import { MemberAccessNotice } from './MemberAccess'
import { modelCompetitions } from './sportModels'
import { score, type ResearchGame } from './researchWorkspace'
import { fetchFavorites, fetchMemberGame, findMemberGames, localDay, saveFavorite, type Favorite, type GameStats } from './memberStats'
import './ResearchWorkspace.css'

const when = (value: string) => new Date(value).toLocaleString()
const display = (value: number | null) => value === null ? 'Unavailable' : value.toFixed(1)
const message = (error: unknown) => error instanceof Error ? error.message
  : typeof error === 'object' && error !== null && 'message' in error ? String(error.message) : 'Member statistics request failed.'
const sameFavorite = (a: Favorite, b: Favorite) => a.competition === b.competition && a.kind === b.kind && a.entity_id === b.entity_id

function TeamComparison({ team, name, history, count, split }: {
  team: string | null; name: string; history: ResearchGame[]; count: number; split: string
}) {
  const games = history.filter(row => team !== null && (row.home_id === team || row.away_id === team)
    && (split === 'all' || (split === 'home' ? row.home_id : row.away_id) === team))
    .flatMap(row => {
      const home = score(row.home_score), away = score(row.away_score)
      return home === null || away === null ? [] : [{ ...row,
        own: row.home_id === team ? home : away, opponent: row.home_id === team ? away : home }]
    }).slice(0, count)
  return <article className="research-card"><h3>{name}</h3>
    <p>{games.length} cached scored games / {games.filter(row => row.own > row.opponent).length} wins / {games.filter(row => row.own < row.opponent).length} losses / {games.filter(row => row.own === row.opponent).length} ties</p>
    <p>Scored {display(games.length ? games.reduce((sum, row) => sum + row.own, 0) / games.length : null)} / allowed {display(games.length ? games.reduce((sum, row) => sum + row.opponent, 0) / games.length : null)} per game</p>
    {!games.length && <p>No cached completed games for this team and split.</p>}
    <ul>{games.map(row => <li key={row.game_id}>{when(row.start_at)}: {row.away_name} {score(row.away_score)} at {row.home_name} {score(row.home_score)}</li>)}</ul>
  </article>
}

function GameDetails({ data, favoriteButton, preferredPlayer }: {
  data: GameStats; favoriteButton: (item: Favorite) => React.ReactNode; preferredPlayer: string
}) {
  const [count, setCount] = useState(10), [split, setSplit] = useState('all')
  const [player, setPlayer] = useState(preferredPlayer), [stat, setStat] = useState(''), [threshold, setThreshold] = useState('')
  const [playerSplit, setPlayerSplit] = useState('all')
  const game = data.game
  const players = [...new Map(data.players.map(row => [row.playerId, row])).values()]
  const selectedPlayer = players.find(row => row.playerId === player)
  const logs = data.players.filter(row => row.playerId === player && Date.parse(row.date) < Date.parse(game.start_at)
    && (playerSplit === 'all' || data.history.some(history => history.game_id === row.game
      && (playerSplit === 'home' ? history.home_name : history.away_name) === row.team)))
  const stats = [...new Set(logs.flatMap(row => Object.keys(row.stats)))].sort()
  const values = logs.flatMap(row => row.stats[stat] === undefined ? [] : [row.stats[stat]])
  const line = threshold.trim() ? Number(threshold) : NaN
  return <section>
    <h2>{game.away_name} at {game.home_name}</h2>
    <p>{when(game.start_at)} / {game.status} / Game cache: {when(game.synced_at)}</p>
    <p>Score: {score(game.away_score) ?? 'Unavailable'} - {score(game.home_score) ?? 'Unavailable'}</p>
    <div className="research-controls">
      {game.away_id && favoriteButton({ competition: game.competition, kind: 'team', entity_id: game.away_id, label: game.away_name })}
      {game.home_id && favoriteButton({ competition: game.competition, kind: 'team', entity_id: game.home_id, label: game.home_name })}
    </div>
    <h3>Team comparison</h3>
    <p>Only completed, scored games before the selected kickoff. Up to 100 recent combined team games are cached here; these are not guaranteed complete season samples.</p>
    <div className="research-controls">
      <label>Team sample<select value={count} onChange={event => setCount(Number(event.target.value))}><option value={5}>Last 5</option><option value={10}>Last 10</option></select></label>
      <label>Team split<select value={split} onChange={event => setSplit(event.target.value)}><option value="all">All games</option><option value="home">Home</option><option value="away">Away</option></select></label>
    </div>
    <div className="research-grid">
      <TeamComparison team={game.home_id} name={game.home_name} history={data.history} count={count} split={split} />
      <TeamComparison team={game.away_id} name={game.away_name} history={data.history} count={count} split={split} />
    </div>
    <h3>Player prop research</h3>
    <p>Enter your own threshold to compare cached game logs. This is not a live prop-odds feed, prediction or betting recommendation. Up to ten team-game snapshots; only logs before kickoff enter the sample. Missing statistics are not zero.</p>
    {['baseball:1', 'hockey:57'].includes(game.competition) ? <p>Individual player logs are unavailable with the configured provider adapter for this league.</p>
      : !players.length ? <p>No cached player logs for this matchup. Try another game; this dashboard cannot request a live refresh.</p> : <>
        <div className="research-controls">
          <label>Player<select aria-label="Player" value={player} onChange={event => { setPlayer(event.target.value); setStat('') }}>
            <option value="">Select a player</option>{players.map(row => <option key={row.playerId} value={row.playerId}>{row.player} ({row.team})</option>)}
          </select></label>
          <label>Player split<select value={playerSplit} onChange={event => { setPlayerSplit(event.target.value); setStat('') }}><option value="all">All cached games</option><option value="home">Home</option><option value="away">Away</option></select></label>
          <label>Statistic<select aria-label="Statistic" value={stat} onChange={event => setStat(event.target.value)}><option value="">Select a statistic</option>{stats.map(value => <option key={value}>{value}</option>)}</select></label>
          <label>Prop threshold<input type="number" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></label>
          {selectedPlayer && favoriteButton({ competition: game.competition, kind: 'player', entity_id: player, label: selectedPlayer.player })}
        </div>
        <p>{values.length} observations / average {display(values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null)}
          {Number.isFinite(line) && <> / Over {values.filter(value => value > line).length}, Under {values.filter(value => value < line).length}, Push {values.filter(value => value === line).length} at {line}</>}</p>
        <ul>{logs.map(row => <li key={`${row.game}:${row.team}:${row.playerId}`}>{when(row.date)} / {row.team}: {stat || 'Select statistic'} {display(row.stats[stat] ?? null)} / Cache {when(row.updated)}</li>)}</ul>
      </>}
  </section>
}

function Dashboard() {
  const [competition, setCompetition] = useState('nfl')
  const [query, setQuery] = useState(''), [from, setFrom] = useState(() => localDay(-7)), [through, setThrough] = useState(() => localDay(14))
  const [search, setSearch] = useState(() => ({ query: '', from: localDay(-7), through: localDay(14) }))
  const [offset, setOffset] = useState(0), [revision, setRevision] = useState(0)
  const [results, setResults] = useState<{ games: ResearchGame[]; total: number } | null>(null)
  const [gameId, setGameId] = useState(''), [data, setData] = useState<GameStats | null>(null)
  const [error, setError] = useState(''), [gameError, setGameError] = useState('')
  const [favorites, setFavorites] = useState<Favorite[] | null>(null), [favoriteError, setFavoriteError] = useState(''), [saving, setSaving] = useState(false)
  const [preferredPlayer, setPreferredPlayer] = useState('')
  useEffect(() => {
    let active = true
    findMemberGames(competition, search.query, search.from, search.through, offset)
      .then(value => { if (active) setResults(value) })
      .catch(reason => { if (active) setError(message(reason)) })
    return () => { active = false }
  }, [competition, search, offset, revision])
  useEffect(() => {
    if (!gameId) return
    let active = true
    fetchMemberGame(competition, gameId).then(value => { if (active) setData(value) })
      .catch(reason => { if (active) setGameError(message(reason)) })
    return () => { active = false }
  }, [competition, gameId, revision])
  useEffect(() => {
    let active = true
    fetchFavorites().then(value => { if (active) setFavorites(value) })
      .catch(reason => { if (active) setFavoriteError(message(reason)) })
    return () => { active = false }
  }, [revision])
  const reload = () => { setResults(null); setData(null); setError(''); setGameError(''); setFavoriteError(''); setRevision(value => value + 1) }
  const resetGame = () => { setGameId(''); setData(null); setGameError(''); setPreferredPlayer('') }
  const applySearch = () => {
    setSearch({ query, from, through }); setOffset(0); setResults(null); setError(''); resetGame()
  }
  const toggle = async (item: Favorite) => {
    setSaving(true); setFavoriteError('')
    try { setFavorites(await saveFavorite(item, !favorites?.some(row => sameFavorite(row, item)))) }
    catch (reason) { setFavoriteError(message(reason)) }
    finally { setSaving(false) }
  }
  const favoriteButton = (item: Favorite) => <button key={`${item.kind}:${item.entity_id}`} disabled={saving || favorites === null}
    aria-pressed={favorites?.some(row => sameFavorite(row, item)) ?? false} onClick={() => void toggle(item)}>
    {favorites?.some(row => sameFavorite(row, item)) ? 'Unsave' : 'Save'} {item.label}
  </button>
  const openFavorite = (item: Favorite) => {
    setCompetition(item.competition); setQuery(item.kind === 'team' ? item.label : ''); setFrom(''); setThrough('')
    setSearch({ query: item.kind === 'team' ? item.label : '', from: '', through: '' }); setOffset(0); setResults(null); setError(''); resetGame()
    if (item.kind === 'player') setPreferredPlayer(item.entity_id)
  }
  return <section className="research-workspace">
    <header><p className="eyebrow">HIGHROLLER</p><h1>Member stats dashboard</h1>
      <p>Game calendar, team comparisons, player logs and your watchlist. Cached data only: searches and reloads spend no live API credits. Coverage and freshness vary. Models, edges and staff refresh tools are not included.</p>
      <Link to="/picks">Current picks</Link> / <Link to="/account">Account settings</Link>
    </header>
    <h2>Saved teams and players</h2>
    <p>Save up to 100 teams and players across six leagues. Team shortcuts search the calendar; player shortcuts select the league and preselect that player when you open a matchup with their cached logs.</p>
    {favoriteError && <p role="alert">{favoriteError} <button onClick={reload}>Retry watchlist</button></p>}
    {!favorites && !favoriteError ? <p role="status">Loading watchlist...</p> : favorites?.length === 0 ? <p>No saved teams or players yet.</p> : favorites &&
      <ul className="member-stats-favorites">{favorites.map(item => <li key={`${item.competition}:${item.kind}:${item.entity_id}`}>
        <button onClick={() => openFavorite(item)}>Find {item.label}</button>
        <span>{modelCompetitions.find(row => row.engineKey === item.competition)?.name} / {item.kind}</span>
        {favoriteButton(item)}
      </li>)}</ul>}
    <h2>Game calendar</h2>
    <form className="research-controls" onSubmit={event => { event.preventDefault(); applySearch() }}>
      <label>League<select value={competition} onChange={event => { setCompetition(event.target.value); setOffset(0); setResults(null); setError(''); resetGame() }}>
        {modelCompetitions.map(item => <option key={item.engineKey} value={item.engineKey}>{item.name}</option>)}
      </select></label>
      <label>Team, matchup or game ID<input type="search" maxLength={100} value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>From date<input type="date" value={from} max={through || undefined} onChange={event => setFrom(event.target.value)} /></label>
      <label>Through date<input type="date" value={through} min={from || undefined} onChange={event => setThrough(event.target.value)} /></label>
      <button type="submit">Search games</button><button type="button" onClick={reload}>Reload cache</button>
    </form>
    <p>Dates use your local timezone and include the whole Through day. Clear either date to remove that boundary. Search team names, matchup words, game ID or status.</p>
    {error ? <p role="alert">{error} <button onClick={reload}>Retry game search</button></p> : !results ? <p role="status">Searching cached games...</p> : <>
      <p role="status">{results.total ? `${offset + 1}-${Math.min(offset + 20, results.total)} of ${results.total} cached games` : 'No cached games match these filters. Try another name or date range.'}</p>
      {!!results.games.length && <fieldset className="research-games"><legend>Select a game</legend><div className="research-game-list">
        {results.games.map(row => <label key={row.game_id} className="research-game-option">
          <input type="radio" name="member-game" checked={gameId === row.game_id} onChange={() => { setGameId(row.game_id); setData(null); setGameError('') }} />
          <span>{row.away_name} at {row.home_name}<small>{when(row.start_at)} / {row.status} / ID {row.game_id}</small></span>
        </label>)}
      </div></fieldset>}
      <nav className="research-controls" aria-label="Game search pages">
        <button disabled={!offset} onClick={() => { setOffset(value => Math.max(0, value - 20)); setResults(null); setError('') }}>Previous games</button>
        <button disabled={offset + 20 >= results.total} onClick={() => { setOffset(value => value + 20); setResults(null); setError('') }}>Next games</button>
      </nav>
    </>}
    {gameError ? <p role="alert">{gameError} <button onClick={reload}>Retry game details</button></p> :
      gameId && !data ? <p role="status">Loading game details...</p> : data &&
      <GameDetails key={`${competition}:${gameId}:${preferredPlayer}`} data={data} favoriteButton={favoriteButton} preferredPlayer={preferredPlayer} />}
  </section>
}

export default function MemberStatsPage() {
  const { access } = useMemberAccess()
  return access?.state === 'active' ? <Dashboard /> : <main className="research-workspace">
    <h1>HIGHROLLER member stats</h1><MemberAccessNotice />
  </main>
}
