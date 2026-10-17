import { ChevronDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import { sportsCatalog } from './sportsCatalog'

export default function SportsDropdown({ onNavigate }: { onNavigate: () => void }) {
  return <details className="sports-dropdown">
    <summary>Sports <ChevronDown size={15} /></summary>
    <div className="sports-dropdown-panel">
      <Link to="/stats" onClick={(event) => { event.currentTarget.closest('details')?.removeAttribute('open'); onNavigate() }}>Member stats (HIGHROLLER)</Link>
      <Link to="/research" onClick={(event) => { event.currentTarget.closest('details')?.removeAttribute('open'); onNavigate() }}>Capper research workspace (team only)</Link>
      <Link to="/models" onClick={(event) => { event.currentTarget.closest('details')?.removeAttribute('open'); onNavigate() }}>Data Models (team only)</Link>
      <Link to="/nfl/lab" onClick={(event) => { event.currentTarget.closest('details')?.removeAttribute('open'); onNavigate() }}>NFL matchup lab (team only)</Link>
      {sportsCatalog.map((sport) => <Link
        to={`/sports/${sport.slug}`}
        key={sport.slug}
        onClick={(event) => {
          event.currentTarget.closest('details')?.removeAttribute('open')
          onNavigate()
        }}
      >{sport.name}</Link>)}
    </div>
  </details>
}