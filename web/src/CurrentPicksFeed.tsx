import { useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { capperSlug } from './capperIdentity'
import { MemberAccessNotice } from './MemberAccess'
import { useMemberAccess } from './memberAccessContext'
import { supabase } from './supabaseClient'
import PickInsightEditor from './PickInsightEditor'

type CurrentPick = {
  id: number
  created_at: string
  sport: string
  capper: string
  avatar_url: string | null
  selection: string
  analysis: string
  odds: number
  units: number
}

function parsePicks(value: unknown): CurrentPick[] {
  if (!Array.isArray(value)) throw new Error('Unexpected current-picks response.')
  return value.map((row: unknown) => {
    if (!row || typeof row !== 'object' || !('id' in row) || typeof row.id !== 'number'
      || !('created_at' in row) || typeof row.created_at !== 'string' || !Number.isFinite(Date.parse(row.created_at))
      || !('sport' in row) || typeof row.sport !== 'string'
      || !('capper' in row) || typeof row.capper !== 'string'
      || !('selection' in row) || typeof row.selection !== 'string'
      || !('analysis' in row) || typeof row.analysis !== 'string'
      || !('odds' in row) || typeof row.odds !== 'number' || !Number.isFinite(row.odds)
      || !('units' in row) || typeof row.units !== 'number' || !Number.isFinite(row.units)
      || !('avatar_url' in row) || (row.avatar_url !== null && typeof row.avatar_url !== 'string')) {
      throw new Error('Unexpected current-picks record.')
    }
    return { id: row.id, created_at: row.created_at, sport: row.sport, capper: row.capper,
      selection: row.selection, analysis: row.analysis, odds: row.odds, units: row.units, avatar_url: row.avatar_url }
  })
}

export default function CurrentPicksFeed({ capper }: { capper?: string }) {
  const filterId = useId()
  const { access, refresh } = useMemberAccess()
  const [loaded, setLoaded] = useState<{ access: typeof access; capper?: string; picks: CurrentPick[] } | null>(null)
  const [error, setError] = useState('')
  const [sport, setSport] = useState('All')
  const [author, setAuthor] = useState('All')
  const [visibleCount, setVisibleCount] = useState(50)
  const [owner, setOwner] = useState<{ access: typeof access; name: string | null; all: boolean; error: string } | null>(null)

  useEffect(() => {
    if (access?.state !== 'active') return
    let cancelled = false
    void (async () => {
      try {
        const [{ data, error: ownerError }, adminResult] = await Promise.all([
          supabase.rpc('owned_capper_page'), supabase.rpc('website_can_edit_all_cappers'),
        ])
        if (ownerError) throw ownerError
        if (adminResult.error) throw adminResult.error
        if (data !== null && typeof data !== 'string') throw new Error('Unexpected page ownership response.')
        if (!cancelled) setOwner({ access, name: data, all: adminResult.data === true, error: '' })
      } catch (failure) {
        const detail = failure && typeof failure === 'object' && 'message' in failure && typeof failure.message === 'string'
          ? ` ${failure.message}` : ''
        if (!cancelled) setOwner({ access, name: null, all: false, error: `Insight editing could not be checked.${detail}` })
      }
    })()
    return () => { cancelled = true }
  }, [access])

  useEffect(() => {
    if (access?.state !== 'active') return
    let cancelled = false
    void (async () => {
      try {
        const picks: CurrentPick[] = []
        for (let offset = 0; ; offset += 1000) {
          const { data, error: queryError } = await supabase.rpc('member_current_picks', { p_capper: capper ?? null })
            .range(offset, offset + 999)
          if (queryError) throw queryError
          const page = parsePicks(data)
          picks.push(...page)
          if (cancelled) return
          if (page.length < 1000) break
        }
        if (!cancelled) {
          setLoaded({ access, capper, picks })
          setError('')
        }
      } catch {
        if (!cancelled) {
          setLoaded({ access, capper, picks: [] })
          setError('Current picks could not be loaded. Access may have changed. Retry the access check.')
        }
      }
    })()
    return () => { cancelled = true }
  }, [access, capper])

  const ready = loaded?.access === access && loaded?.capper === capper
  const picks = ready ? loaded.picks : []
  const sports = ['All', ...new Set(picks.map((pick) => pick.sport).sort())]
  const authors = ['All', ...new Set(picks.map((pick) => pick.capper).sort())]
  const filtered = picks.filter((pick) => (sport === 'All' || pick.sport === sport) && (author === 'All' || pick.capper === author))
  return <section className={`current-picks${capper ? ' current-picks-embedded' : ''}`} aria-label={capper ? `${capper} current picks` : 'Current picks'}>
    <header className="section-heading">
      <div><p className="eyebrow">HIGHROLLER board</p>{capper ? <h2>Current picks.</h2> : <h1>Expert picks.</h1>}</div>
      <p>Published, unsettled official plays. No result or daily pick count is guaranteed. Publication time is not an event start time.</p>
    </header>
    <MemberAccessNotice />
    {access?.state === 'active' && <>
      {owner?.access === access && owner.error && <p role="alert">{owner.error}</p>}
      <div className="current-picks-toolbar">
        <div className="current-picks-filter"><label htmlFor={`${filterId}-sport`}>Sport</label><select id={`${filterId}-sport`} value={sport} onChange={(event) => { setSport(event.target.value); setVisibleCount(50) }}>
          {[...new Set([...sports, sport])].map((name) => <option key={name}>{name}</option>)}
        </select></div>
        {!capper && <div className="current-picks-filter"><label htmlFor={`${filterId}-capper`}>Capper</label><select id={`${filterId}-capper`} value={author} onChange={(event) => { setAuthor(event.target.value); setVisibleCount(50) }}>
          {[...new Set([...authors, author])].map((name) => <option key={name}>{name}</option>)}
        </select></div>}
        <button className="account-secondary-button" type="button" onClick={refresh}>Refresh picks & access</button>
        <span role="status">{ready && !error ? `${filtered.length} open plays` : 'Checking current picks...'}</span>
      </div>
      {ready && error ? <p className="account-message" role="alert">{error}</p>
        : !ready ? <p className="account-state">Loading current picks...</p>
        : filtered.length === 0 ? <p className="account-state">No published open picks{sport !== 'All' || author !== 'All' ? ' match these filters' : capper ? ` from ${capper}` : ''}. <Link to="/results">Browse settled results</Link>.</p>
        : <div className="current-picks-grid">{filtered.slice(0, visibleCount).map((pick) => <article className="current-pick-card" key={pick.id}>
          <header><Link to={`/cappers/${capperSlug(pick.capper)}`}>{pick.capper}</Link><span>{pick.sport} · Open</span></header>
          <h3>{pick.selection}</h3>
          <p className="current-pick-line">{pick.odds > 0 ? '+' : ''}{pick.odds} <span>Risk: {pick.units}u</span></p>
          <p className="current-pick-time">Published <time dateTime={pick.created_at}>{new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/New_York' }).format(new Date(pick.created_at))}</time> ET</p>
          {pick.analysis.trim() && <details className="current-pick-insight">
            <summary>Capper insight</summary>
            <p className="current-pick-analysis">{pick.analysis}</p>
          </details>}
          {owner?.access === access && (owner.all || owner.name === pick.capper) && <PickInsightEditor
            key={pick.id} playId={pick.id} insight={pick.analysis}
            onSaved={(value) => setLoaded((current) => current && current.access === access
              ? { ...current, picks: current.picks.map((item) => item.id === pick.id ? { ...item, analysis: value } : item) }
              : current)}
          />}
          <Link to={`/cappers/${capperSlug(pick.capper)}`}>Capper page & settled record</Link>
        </article>)}</div>}
      {ready && !error && filtered.length > visibleCount && <button className="account-secondary-button" onClick={() => setVisibleCount((count) => count + 50)}>Show more picks</button>}
    </>}
  </section>
}
