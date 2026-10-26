import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

type TrendPoint = { date: string; cumulative_net_units: number }
type SportPoint = { sport: string; plays: number; wins: number; losses: number; net_units: number }

function formatUnits(value: number): string {
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value))}u`
}

export default function CapperAnalyticsCharts({ trend, bySport }: { trend: TrendPoint[]; bySport: SportPoint[] }) {
  return <>
    <section className="capper-chart-panel">
      <div className="profile-section-heading"><p className="eyebrow">Cumulative net units</p><h2>Results over time.</h2></div>
      <div className="capper-chart">
        {trend.length ? <ResponsiveContainer width="100%" height="100%">
          <LineChart data={trend} margin={{ top: 12, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 4" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tickFormatter={(value) => formatUnits(Number(value))} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={56} />
            <Tooltip formatter={(value) => [formatUnits(Number(value)), 'Net units']} />
            <ReferenceLine y={0} stroke="var(--muted)" />
            <Line type="monotone" dataKey="cumulative_net_units" name="Cumulative net units" stroke="var(--green)" strokeWidth={3} dot={{ r: 3, fill: 'var(--green)' }} activeDot={{ r: 5 }} />
          </LineChart>
        </ResponsiveContainer> : <p className="results-empty">Performance graph appears when this capper has settled results.</p>}
      </div>
    </section>
    <section className="capper-chart-panel">
      <div className="profile-section-heading"><p className="eyebrow">Net units by sport</p><h2>Record by sport.</h2></div>
      <div className="capper-chart">
        {bySport.length ? <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bySport} margin={{ top: 12, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 4" vertical={false} />
            <XAxis dataKey="sport" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis tickFormatter={(value) => formatUnits(Number(value))} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={56} />
            <Tooltip formatter={(value) => [formatUnits(Number(value)), 'Net units']} />
            <ReferenceLine y={0} stroke="var(--muted)" />
            <Bar dataKey="net_units" name="Net units" fill="var(--green)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer> : <p className="results-empty">Sport graph appears when this capper has settled results.</p>}
      </div>
    </section>
  </>
}