import { useEffect, useId, useState } from 'react'
import { supabase } from './supabaseClient'

type PastPick = {
  settled_at: string
  sport: string
  capper: string
  selection: string
  odds: number
  units: number
  status: 'win' | 'loss' | 'void' | 'partial'
  net_units: number
  analysis: string
}

function parsePastPicks(value: unknown): PastPick[] {
  if (!Array.isArray(value)) throw new Error('Unexpected settled-results response.')
  return value.map((row: unknown) => {
    if (!row || typeof row !== 'object'
      || !('settled_at' in row) || typeof row.settled_at !== 'string' || !Number.isFinite(Date.parse(row.settled_at))
      || !('sport' in row) || typeof row.sport !== 'string'
      || !('capper' in row) || typeof row.capper !== 'string'
      || !('selection' in row) || typeof row.selection !== 'string'
      || !('odds' in row) || typeof row.odds !== 'number' || !Number.isFinite(row.odds)
      || !('units' in row) || !Number.isFinite(Number(row.units))
      || !('net_units' in row) || !Number.isFinite(Number(row.net_units))
      || !('status' in row) || typeof row.status !== 'string'
      || !['win', 'loss', 'void', 'partial'].includes(row.status)) {
      throw new Error('Unexpected settled-results record.')
    }
    const analysis = 'analysis' in row && typeof row.analysis === 'string' ? row.analysis : ''
    return { settled_at: row.settled_at, sport: row.sport, capper: row.capper,
      selection: row.selection, odds: row.odds, units: Number(row.units),
      status: row.status as PastPick['status'], net_units: Number(row.net_units), analysis }
  })
}

function formatNet(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(value))}u`
}

export default function PastPicksFeed() {
  const filterId = useId()
  const [picks, setPicks] = useState<PastPick[] | null>(null)
  const [error, setError] = useState('')
  const [sport, setSport] = useState('All')
  const [author, setAuthor] = useState('All')
  const [visibleCount, setVisibleCount] = useState(20)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const rows: PastPick[] = []
        for (let offset = 0; ; offset += 1000) {
          const { data, error: queryError } = await supabase.rpc('public_settled_results').range(offset, offset + 999)
          if (queryError) throw queryError
          const page = parsePastPicks(data)
          rows.push(...page)
          if (cancelled) return
          if (page.length < 1000) break
        }
        if (!cancelled) {
          setPicks(rows.filter((pick) => pick.analysis.trim() !== ''))
          setError('')
        }
      } catch {
        if (!cancelled) {
          setPicks([])
          setError('Past writeups could not be loaded right now.')
        }
      }
    })()
    return () => { cancelled = true }
  }, [])

  const ready = picks !== null
  const sports = ['All', ...new Set((picks ?? []).map((pick) => pick.sport).sort())]
  const authors = ['All', ...new Set((picks ?? []).map((pick) => pick.capper).sort())]
  const filtered = (picks ?? []).filter((pick) =>
    (sport === 'All' || pick.sport === sport) && (author === 'All' || pick.capper === author))
  return <section className="current-picks past-picks" aria-label="Past picks and writeups">
    <header className="section-heading">
      <div><p className="eyebrow">The writeup archive</p><h2>Past picks & writeups.</h2></div>
      <p>Browse settled plays that shipped with a capper writeup. The full public ledger lives on the results page.</p>
    </header>
    <div className="current-picks-toolbar">
      <div className="current-picks-filter"><label htmlFor={`${filterId}-sport`}>Sport</label><select id={`${filterId}-sport`} value={sport} onChange={(event) => { setSport(event.target.value); setVisibleCount(20) }}>
        {[...new Set([...sports, sport])].map((name) => <option key={name}>{name}</option>)}
      </select></div>
      <div className="current-picks-filter"><label htmlFor={`${filterId}-capper`}>Capper</label><select id={`${filterId}-capper`} value={author} onChange={(event) => { setAuthor(event.target.value); setVisibleCount(20) }}>
        {[...new Set([...authors, author])].map((name) => <option key={name}>{name}</option>)}
      </select></div>
      <span role="status">{ready && !error ? `${filtered.length} settled writeups` : 'Checking past writeups...'}</span>
    </div>
    {ready && error ? <p className="account-message" role="alert">{error}</p>
      : !ready ? <p className="account-state">Loading past writeups...</p>
      : filtered.length === 0 ? <p className="account-state">No settled plays with writeups{sport !== 'All' || author !== 'All' ? ' match these filters' : ''} yet.</p>
      : <div className="current-picks-grid">{filtered.slice(0, visibleCount).map((pick, index) => <article className="current-pick-card" key={`${pick.settled_at}-${pick.capper}-${index}`}>
        <header><span>{pick.capper}</span><span>{pick.sport} · <mark className={`status status-${pick.status}`}>{pick.status}</mark></span></header>
        <h3>{pick.selection}</h3>
        <p className="current-pick-line">{pick.odds > 0 ? '+' : ''}{pick.odds} <span>Risk: {pick.units}u · Net: {formatNet(pick.net_units)}</span></p>
        <p className="current-pick-time">Settled <time dateTime={pick.settled_at}>{new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'America/New_York' }).format(new Date(pick.settled_at))}</time> ET</p>
        <p className="current-pick-analysis">{pick.analysis}</p>
      </article>)}</div>}
    {ready && !error && filtered.length > visibleCount && <button className="account-secondary-button" onClick={() => setVisibleCount((count) => count + 20)}>Show more writeups</button>}
  </section>
}
