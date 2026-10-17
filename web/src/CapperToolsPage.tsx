import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, Sparkles, CloudSun, Activity, Upload, ArrowLeft, Copy, Check } from 'lucide-react'
import { supabase } from './supabaseClient'
import { useMemberAccess } from './memberAccessContext'
import { MemberAccessNotice } from './MemberAccess'
import { findMemberGames, localDay, type GameStats } from './memberStats'
import { cachedGames, object, playerLogs, score } from './researchWorkspace'
import { modelCompetitions } from './sportModels'
import './CapperTools.css'

type Report = GameStats & { injuries: Record<string, unknown>[]; weather: Record<string, unknown> | null; odds: Record<string, unknown> | null }
const text = (value: unknown) => value === null || value === undefined ? 'Unavailable' : String(value)
const when = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'Unknown date'
const errorText = (error: unknown) => error instanceof Error ? error.message : object(error) && typeof error.message === 'string' ? error.message : 'Research request failed.'
function parseReport(value: unknown): Report {
  if (!object(value) || typeof value.generated_at !== 'string' || !Array.isArray(value.injuries) || !value.injuries.every(object)
    || !(value.weather === null || object(value.weather)) || !(value.odds === null || object(value.odds))) throw new Error('Invalid research report.')
  return { game: cachedGames([value.game])[0], history: cachedGames(value.history), players: playerLogs(value.players),
    generated_at: value.generated_at, injuries: value.injuries, weather: value.weather, odds: value.odds }
}
function Freshness({ at, hours }: { at: unknown; hours: number }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer) }, [])
  const stale = typeof at !== 'string' || !Number.isFinite(Date.parse(at)) || now - Date.parse(at) > hours * 3_600_000
  return <p className={`tools-freshness${stale ? ' is-stale' : ''}`}>{stale ? 'May be stale · ' : 'Cached · '}{when(at)}</p>
}
function Matchup({ report }: { report: Report }) {
  const [notes, setNotes] = useState(''), [reportDate, setReportDate] = useState('')
  const [image, setImage] = useState<File | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [analysis, setAnalysis] = useState(''), [generatedAt, setGeneratedAt] = useState(''), [copied, setCopied] = useState(false)
  const [player, setPlayer] = useState(''), [stat, setStat] = useState(''), [threshold, setThreshold] = useState('')
  const [action, setAction] = useState(''), [reviewed, setReviewed] = useState(true), [extracted, setExtracted] = useState(false)
  const { game, weather, odds } = report
  const logs = report.players.filter(row => row.playerId === player && Date.parse(row.date) < Date.parse(game.start_at))
  const players = [...new Map(report.players.map(row => [row.playerId, row])).values()]
  const stats = [...new Set(logs.flatMap(row => Object.keys(row.stats)))].sort()
  const values = logs.flatMap(row => row.stats[stat] === undefined ? [] : [row.stats[stat]])
  const line = threshold.trim() ? Number(threshold) : NaN
  const ai = async (mode: 'analyze' | 'extract') => {
    setBusy(true); setError(''); setCopied(false); setAction(mode)
    try {
      let imageData: string | undefined
      if (mode === 'extract') {
        if (!image || image.size > 4_194_304 || !['image/png', 'image/jpeg', 'image/webp'].includes(image.type)) throw new Error('Choose a PNG, JPEG or WebP screenshot up to 4 MiB.')
        imageData = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('Screenshot could not be read.'))
          reader.onerror = () => reject(new Error('Screenshot could not be read.'))
          reader.readAsDataURL(image)
        })
      }
      const { data: session, error: sessionError } = await supabase.auth.getSession()
      if (sessionError) throw sessionError
      if (!session.session) throw new Error('Sign in with Discord before using AI.')
      const response = await fetch(`${import.meta.env.VITE_SUPABASE_URL || 'https://lhsevzucmmzetpshpffv.supabase.co'}/functions/v1/capper-research`, {
        method: 'POST', headers: { Authorization: `Bearer ${session.session.access_token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: mode, competition: game.competition, game_id: game.game_id, notes, report_date: reportDate, ...(imageData ? { image: imageData } : {}) }),
        signal: AbortSignal.timeout(75_000),
      })
      const result: unknown = await response.json()
      if (!response.ok) throw new Error(object(result) && typeof result.error === 'string' ? result.error : 'AI request failed. Do not retry automatically.')
      if (!object(result) || typeof result.text !== 'string' || typeof result.generated_at !== 'string') throw new Error('AI returned an invalid response. Do not retry automatically.')
      if (mode === 'extract') {
        if (result.text.length > 6000) throw new Error('Extracted text exceeds the notes limit. Crop to a smaller screenshot before trying again.')
        setNotes(result.text); setReviewed(false); setExtracted(true); setImage(null); setAnalysis(''); setGeneratedAt('')
      }
      else { setAnalysis(result.text); setGeneratedAt(result.generated_at) }
    } catch (reason) { setError(`${errorText(reason)} No automatic retry was made.`) }
    finally { setBusy(false); setAction('') }
  }
  return <>
    <section className="tools-matchup">
      <span className="tools-kicker">YOUR MATCHUP</span>
      <h2>{game.away_name}<span>at</span>{game.home_name}</h2>
      <p>{when(game.start_at)} · {game.status}</p>
      <p>Score: {score(game.away_score) ?? '—'} – {score(game.home_score) ?? '—'}</p>
      <Freshness at={game.synced_at} hours={24} />
    </section>
    <p className="tools-disclosure">Cached research, not a live feed. Missing information is not a clean injury report. Current reports are not historical pregame evidence.</p>
    <div className="tools-grid">
      <section className="tools-card"><h3><Activity size={18} /> Recent form</h3>
        <p className="tools-muted">Last 5 cached completed, scored games before this matchup. Samples may be incomplete.</p>
        {[{ id: game.away_id, name: game.away_name }, { id: game.home_id, name: game.home_name }].map(team => {
          const games = report.history.filter(row => team.id && [row.home_id, row.away_id].includes(team.id))
            .flatMap(row => {
              const home = score(row.home_score), away = score(row.away_score)
              return home === null || away === null ? [] : [{ ...row, own: row.home_id === team.id ? home : away, opponent: row.home_id === team.id ? away : home }]
            }).slice(0, 5)
          return <div className="tools-form-team" key={team.name}><strong>{team.name}</strong>
            <p>{games.length ? `${games.filter(row => row.own > row.opponent).length}W · ${games.filter(row => row.own < row.opponent).length}L · ${games.filter(row => row.own === row.opponent).length}T · ${(games.reduce((sum, row) => sum + row.own, 0) / games.length).toFixed(1)} scored/game` : 'No scored history available.'}</p>
            <details><summary>Game results</summary><ul>{games.map(row => <li key={row.game_id}>{when(row.start_at)} · {row.away_name} {score(row.away_score)} at {row.home_name} {score(row.home_score)}</li>)}</ul></details>
          </div>
        })}
      </section>
      <section className="tools-card"><h3><CloudSun size={18} /> Game weather</h3>
        {weather ? <><p className="tools-metric">{text(weather.temperature_f)}°F</p><p>Wind {text(weather.wind_mph)} mph · Precipitation {text(weather.precipitation_mm)} mm</p>
          <p>{text(weather.source)} · Open-Meteo · Three-hour game window</p><Freshness at={weather.fetched_at} hours={3} />
          <p className="tools-muted">Cached home-venue estimate. Neutral-site location and roof status are unverified.</p></>
          : <p>Unavailable for this game. Indoor venues and missing venue/forecast coverage may have no weather record.</p>}
      </section>
      <section className="tools-card"><h3>Availability</h3><p className="tools-muted">Source: API-Sports · Currently connected injury feed: NFL.</p>
        {!report.injuries.length ? <p>No verified injury records available for this matchup. This does not mean no injuries.</p>
          : <ul className="tools-injuries">{report.injuries.map((row, index) => <li key={index}><strong>{text(row.player_name)}</strong> · {row.team_id === game.home_id ? game.home_name : game.away_name}
            <p>{text(row.status)} · {text(row.description)}</p><p>Report date: {text(row.reported_on)}</p><Freshness at={row.fetched_at} hours={6} /></li>)}</ul>}
      </section>
      <section className="tools-card"><h3>Market snapshot</h3>
        {odds ? <><p>Consensus home spread: {text(odds.home_spread)}</p><p>Total: {text(odds.total)}</p><p>{text(odds.books)} contributing books · API-Sports</p><Freshness at={odds.fetched_at} hours={6} />
          <p className="tools-muted">Consensus reference, not a current executable sportsbook line.</p></> : <p>No cached market available. Add your own sportsbook line below.</p>}
      </section>
    </div>
    <details className="tools-card"><summary>Player stats &amp; thresholds</summary>
      {!players.length ? <p>No supported player logs cached for this matchup.</p> : <>
        <div className="tools-fields"><label>Player<select value={player} onChange={event => { setPlayer(event.target.value); setStat('') }}><option value="">Choose player</option>{players.map(row => <option key={row.playerId} value={row.playerId}>{row.player} · {row.team}</option>)}</select></label>
          <label>Statistic<select value={stat} onChange={event => setStat(event.target.value)}><option value="">Choose statistic</option>{stats.map(name => <option key={name}>{name}</option>)}</select></label>
          <label>Your threshold<input type="number" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></label></div>
        <p>{values.length} cached observations · Average {values.length ? (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1) : 'Unavailable'}
          {Number.isFinite(line) && <> · Over {values.filter(v => v > line).length} / Under {values.filter(v => v < line).length} / Push {values.filter(v => v === line).length}</>}</p>
        <p className="tools-muted">Before kickoff only. Missing values are not zero. Your threshold is a comparison, not live prop odds.</p>
        <ul>{logs.map(row => <li key={`${row.game}:${row.playerId}`}>{when(row.date)} · {stat || 'Choose statistic'}: {text(row.stats[stat])} · Cached {when(row.updated)}</li>)}</ul>
      </>}
    </details>
    <section className="tools-card tools-ai"><span className="tools-kicker">MAKE SENSE OF IT</span><h3><Sparkles size={20} /> Matchup breakdown</h3>
      <p>Add your line or injury notes, then let Playmaker explain the matchup using the available evidence.</p>
      <label>Your line, source, and notes <span className="tools-muted">(optional · user supplied, unverified)</span>
        <textarea disabled={busy} rows={4} maxLength={6000} placeholder="Purdue +3.5 at my sportsbook. Injury update from team report…" value={notes} onChange={event => { setNotes(event.target.value); setAnalysis(''); setGeneratedAt('') }} /></label>
      <label>Source report date (optional)<input disabled={busy} type="date" value={reportDate} onChange={event => { setReportDate(event.target.value); setAnalysis(''); setGeneratedAt('') }} /></label>
      <details className="tools-upload"><summary><Upload size={16} /> Read a screenshot</summary>
        <p>Upload only sports information you can share. It is sent to OpenAI for extraction, not saved to our database. Remove private details first.</p>
        <input aria-label="Sports screenshot" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { setImage(event.target.files?.[0] ?? null); setError('') }} />
        <button disabled={busy || !image} onClick={() => void ai('extract')}>{busy && action === 'extract' ? 'Reading screenshot…' : 'Read screenshot'}</button>
      </details>
      {extracted && <label className="tools-review"><input type="checkbox" disabled={busy} checked={reviewed} onChange={event => setReviewed(event.target.checked)} /> I reviewed and corrected the extracted notes.</label>}
      <button className="tools-primary" disabled={busy || !reviewed} onClick={() => void ai('analyze')}><Sparkles size={18} /> {busy && action === 'analyze' ? 'Building your breakdown…' : 'Analyze matchup'}</button>
      <p className="tools-muted">50 AI actions per capper / 500 shared per UTC day; wait 15 seconds between actions. Extraction and analysis each count. No automatic retries. Cached browsing uses no AI calls.</p>
      {busy && <p role="status">Working on your request. Keep this page open.</p>}
      {error && <p role="alert" className="tools-error">{error}</p>}
      {analysis && <section className="tools-analysis"><h3>Your breakdown</h3><p className="tools-muted">AI-generated · {when(generatedAt)} · Verify facts before acting. Not a guaranteed pick.</p><div className="tools-analysis-text">{analysis}</div>
        <button onClick={() => void navigator.clipboard.writeText(analysis).then(() => setCopied(true)).catch(() => setError('Copy failed. Select the breakdown text to copy it manually.'))}>{copied ? <Check size={16} /> : <Copy size={16} />} {copied ? 'Copied' : 'Copy breakdown'}</button></section>}
    </section>
  </>
}

function Tools() {
  const [competition, setCompetition] = useState('nfl'), [query, setQuery] = useState(''), [date, setDate] = useState(localDay())
  const [search, setSearch] = useState({ competition: 'nfl', query: '', date: localDay(), offset: 0 })
  const [results, setResults] = useState<Awaited<ReturnType<typeof findMemberGames>> | null>(null)
  const [report, setReport] = useState<Report | null>(null), [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const updateSearch = (next: typeof search) => { setResults(null); setReport(null); setSelected(''); setError(''); setSearch(next) }
  const openGame = (id: string) => { setReport(null); setError(''); setSelected(id) }
  useEffect(() => {
    let active = true
    findMemberGames(search.competition, search.query, search.date, search.date, search.offset)
      .then(value => { if (active) setResults(value) }).catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [search])
  useEffect(() => {
    if (!selected) return
    let active = true
    Promise.resolve(supabase.rpc('capper_tools_game', { p_competition: search.competition, p_game_id: selected }))
      .then(({ data, error }) => { if (error) throw error; const value = parseReport(data); if (active) setReport(value) })
      .catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [selected, search.competition])
  return <>
    {!selected ? <>
      <form className="tools-card tools-search" onSubmit={event => { event.preventDefault(); updateSearch({ competition, query, date, offset: 0 }) }}>
        <div className="tools-leagues" aria-label="Sport">{modelCompetitions.map(league => <button key={league.key} type="button" aria-pressed={competition === league.engineKey}
          onClick={() => { setCompetition(league.engineKey); updateSearch({ competition: league.engineKey, query, date, offset: 0 }) }}>{league.name.replace("NCAA Men's Basketball", 'College hoops').replace('NCAA Football', 'College football')}</button>)}</div>
        <div className="tools-fields"><label>Team or matchup<input type="search" maxLength={100} placeholder="Search a team…" value={query} onChange={event => setQuery(event.target.value)} /></label>
          <label>Game date<input type="date" value={date} onChange={event => setDate(event.target.value)} /></label>
          <button className="tools-primary" type="submit"><Search size={18} /> Find games</button></div>
        <button type="button" className="tools-text-button" onClick={() => { setDate(''); updateSearch({ competition, query, date: '', offset: 0 }) }}>Search all cached dates</button>
      </form>
      {!results && !error && <p role="status">Finding matchups…</p>}
      {results && <section><div className="tools-results-heading"><h2>Pick your matchup</h2><span>{results.total} games</span></div>
        {!results.games.length && <div className="tools-card"><p>No cached matchups found. Try another date or search all cached dates.</p></div>}
        <div className="tools-game-list">{results.games.map(game => <button className="tools-game" key={game.game_id} onClick={() => openGame(game.game_id)}>
          <span><strong>{game.away_name}</strong><span className="tools-muted"> at </span><strong>{game.home_name}</strong></span>
          <span className="tools-muted">{when(game.start_at)} · {game.status}</span><span className="tools-game-open">Research matchup →</span>
        </button>)}</div>
        <div className="tools-pagination"><button disabled={search.offset === 0} onClick={() => updateSearch({ ...search, offset: Math.max(0, search.offset - 20) })}>Previous</button>
          <span>{results.total ? search.offset + 1 : 0}–{Math.min(search.offset + 20, results.total)} of {results.total}</span>
          <button disabled={search.offset + 20 >= results.total} onClick={() => updateSearch({ ...search, offset: search.offset + 20 })}>Next</button></div>
      </section>}
    </> : <><button className="tools-back" onClick={() => { setSelected(''); setReport(null); setError('') }}><ArrowLeft size={16} /> Back to games</button>
      {!report && !error && <p role="status">Opening your research…</p>}{report && <Matchup key={`${report.game.competition}:${report.game.game_id}`} report={report} />}</>}
    {error && <p className="tools-error" role="alert">{error} <button onClick={() => updateSearch({ ...search })}>Reload games</button></p>}
  </>
}
export default function CapperToolsPage() {
  const { access } = useMemberAccess()
  const [allowed, setAllowed] = useState<boolean | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  useEffect(() => {
    if (access?.state !== 'active') return
    let active = true
    Promise.resolve(supabase.rpc('capper_tools_access')).then(({ data, error }) => {
      if (error) throw error
      if (typeof data !== 'boolean') throw new Error('Invalid capper approval response.')
      if (active) setAllowed(data)
    }).catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [access, revision])
  return <main className="capper-tools">
    <header className="tools-hero"><div><span className="tools-kicker">PLAYMAKER RESEARCH</span><h1>Your next matchup.<br /><em>A clearer picture.</em></h1><p>Pick a game. Check the facts. Get a grounded breakdown.</p></div><span className="tools-hero-icon" aria-hidden="true"><Sparkles size={44} /></span></header>
    <div className="tools-toplinks"><span>Private tools · Approved cappers</span><Link to="/account">Account</Link></div>
    {access && access.state !== 'active' ? <MemberAccessNotice /> : error ? <p role="alert">{error} <button onClick={() => { setError(''); setRevision(v => v + 1) }}>Retry access check</button></p>
      : !access || allowed === null ? <p role="status">Checking your access…</p> : !allowed ? <section className="tools-card"><h2>Invitation required</h2><p>Ask Playmaker to approve your capper account. Sign in with Discord and complete age verification first. No API key is needed on your phone.</p></section> : <Tools />}
  </main>
}
