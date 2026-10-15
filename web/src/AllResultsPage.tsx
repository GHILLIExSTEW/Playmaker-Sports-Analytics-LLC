import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'

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

type LoadState = 'loading' | 'ready' | 'error' | 'configuration'

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/New_York',
  }).format(new Date(value))
}

function formatUnits(value: number): string {
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value))}u`
}

function formatNetUnits(value: number): string {
  const amount = Number(value)
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(amount))}u`
}

export default function AllResultsPage({
  results,
  totalResults,
  sports,
  sport,
  loadState,
  onSportChange,
  onRetry,
}: {
  results: Result[]
  totalResults: number
  sports: string[]
  sport: string
  loadState: LoadState
  onSportChange: (sport: string) => void
  onRetry: () => void
}) {
  return (
    <section className="results-page results-section">
      <div className="section-heading">
        <div><Link to="/#results" className="capper-back-link"><ArrowRight size={16} /> Home</Link><p className="eyebrow">The complete public ledger</p><h1>Settled results.</h1></div>
        <p>Review settled team and capper picks, including posted odds, units risked, results, and net units. Filter the ledger by sport.</p>
      </div>
      <div className="results-toolbar">
        <div className="filter-group" aria-label="Filter all settled results by sport">
          {sports.map((item) => <button className={sport === item ? 'active' : ''} type="button" key={item} onClick={() => onSportChange(item)}>{item}</button>)}
        </div>
        <span aria-live="polite">{loadState === 'ready' ? `${results.length} of ${totalResults} settled plays` : loadState === 'loading' ? 'Loading results' : loadState === 'configuration' ? 'Supabase setup required' : 'Results unavailable'}</span>
        {loadState === 'error' && <button className="results-retry" type="button" onClick={onRetry}>Retry</button>}
      </div>
      {loadState === 'configuration' && <p className="data-notice">Supabase is not configured for this deployment.</p>}
      {loadState === 'error' && <p className="data-notice">The public results could not be loaded. Check the Supabase project and try again.</p>}
      <div className="results-table" role="table" aria-label="All settled results">
        <div className="result-row result-head" role="row"><span>Date</span><span>Play</span><span>Capper</span><span>Risk</span><span>Result</span><span>Net</span></div>
        {results.map((result, index) => (
          <div className="result-row" role="row" key={`${result.settled_at}-${result.capper}-${index}`}>
            <span className="result-date">{formatDate(result.settled_at)}<small>{result.sport}</small></span>
            <span className="result-selection">{result.selection}<small>{result.odds > 0 ? '+' : ''}{result.odds}</small></span>
            <span>{result.capper}</span><span>{formatUnits(result.units)}</span>
            <span><mark className={`status status-${result.status}`}>{result.status}</mark></span>
            <span className={result.net_units > 0 ? 'net-positive' : result.net_units < 0 ? 'net-negative' : ''}>{formatNetUnits(result.net_units)}</span>
          </div>
        ))}
        {loadState === 'loading' && <div className="results-empty">Loading the official record…</div>}
        {loadState === 'ready' && results.length === 0 && <div className="results-empty">No settled results{sport !== 'All' ? ` for ${sport}` : ''} yet.</div>}
      </div>
    </section>
  )
}