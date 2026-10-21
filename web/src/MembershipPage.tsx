import { ArrowRight, Check } from 'lucide-react'
import { Link } from 'react-router-dom'
import { membershipPlans, membershipPrices } from './membershipPlans'

const accessTypes = ['Seven-day trial', 'Paid subscription']

const features = [
  { name: 'Public settled-play record', values: ['Included', 'Included'] },
  { name: 'Website current-picks feed', values: ['Published open plays by sport and capper with verified access', 'Published open plays by sport and capper with verified access'] },
  { name: 'Personalized sports & capper follows', values: ['Included', 'Included'] },
  { name: 'Weekly recap', values: ['Included', 'Included'] },
  { name: 'Member analysis & Discord community', values: ['7-day trial on the HIGHROLLER subscription', 'HIGHROLLER access during your verified subscription period'] },
  { name: 'Interactive cached stats tools', values: ['Member stats dashboard: searchable game calendar, last 5/10 team comparisons, player thresholds and saved teams/players. NFL Matchup Lab and Data Models remain team-only.', 'Member stats dashboard: searchable game calendar, last 5/10 team comparisons, player thresholds and saved teams/players. NFL Matchup Lab and Data Models remain team-only.'] },
  { name: 'Player & driver stats', values: ['Cached current/previous season reports and optional game detail where supported', 'Cached current/previous season reports and optional game detail where supported'] },
  { name: 'On-demand refresh', values: ['When activated: same 5 API requests per person/day and shared quota/cooldown as paid members; currently disabled', 'When activated: 5 API requests per person/day; season lookups may use multiple requests. Shared quota and cooldown; currently disabled'] },
  { name: 'Member Bet Vault submissions', values: ['Verified eligible trial access required; available where enabled', 'Verified paid access required; available where enabled'] },
]

export default function MembershipPage() {
  return <section className="membership-page">
    <Link to="/" className="capper-back-link"><ArrowRight size={16} /> Home</Link>
    <header className="membership-page-heading">
      <p className="eyebrow">Membership is live</p>
      <h1>One membership. Your seat.</h1>
      <p>HIGHROLLER is live: one membership with three billing options: {membershipPrices}. Every option includes a seven-day free trial for eligible first-time subscribers. After the trial, your selected plan bills automatically and renews at its selected interval unless canceled. ROOKIE is our free community role, not a paid tier. For adults 21+ (or the higher local legal age).</p>
      <p>Already have access? <Link to="/account">Sign in with the Discord account linked in Whop</Link>, complete age verification, and <Link to="/picks">check the current-picks board</Link>. Expired access or a stale membership sync does not grant premium access.</p>
    </header>
    <section className="membership-plan-grid" aria-label="HIGHROLLER subscription and introductory trial">
      {membershipPlans.map((plan) => <article className={`membership-plan${plan.name === 'Monthly' ? ' is-featured' : ''}`} key={plan.name}>
        <p className="eyebrow">HIGHROLLER | Seven days free if eligible</p>
        <h2>{plan.name}</h2>
        <p className="membership-price">{plan.price}<span> / {plan.duration}</span></p>
        <p>All HIGHROLLER membership benefits. Eligible first-time subscribers start with seven days free, then {plan.price} is billed every {plan.duration}. Automatically renews until canceled. Cancel before the trial ends to avoid the first charge.</p>
        <a className="button membership-interest" href={`https://whop.com/checkout/${plan.planId}`}>Start {plan.name.toLowerCase()} trial <ArrowRight size={16} /></a>
      </article>)}
    </section>
    <section className="membership-breakdown" aria-label="Payment and trial details">
      <div className="profile-section-heading"><p className="eyebrow">Simple access</p><h2>Seven days free. Choose your billing interval.</h2></div>
      <p className="membership-disclaimer">The trial is part of your selected HIGHROLLER subscription, not a separate free membership. Eligible first-time subscribers receive one seven-day trial with the same available features and usage limits as paid subscribers; choosing another billing option does not create another trial. Unless canceled before the trial ends, Whop automatically bills the full selected interval price: {membershipPrices}, in USD. Each plan renews at that interval until canceled. Three-month and annual prices are charged in full, not in monthly installments. Manage or cancel your subscription in Whop. Review the next billing date, eligibility, taxes and any checkout fees before confirming.</p>
      <p className="membership-disclaimer">Read the <Link to="/terms">Terms</Link>, <Link to="/privacy">Privacy notice</Link>, and <Link to="/refunds">Refund policy</Link>. Refund requests for access failures or duplicate charges should be made within seven days; losing picks do not qualify by themselves. Applicable legal rights and Whop rules still apply.</p>
      <p className="membership-disclaimer">After checkout, <a href="https://discord.com/invite/mwxRsWUp5W">join our Discord server</a> using the account linked to Whop and follow the access instructions. Joining the server alone does not grant HIGHROLLER access.</p>
    </section>
    <section className="membership-breakdown" aria-label="Free ROOKIE community">
      <div className="profile-section-heading"><p className="eyebrow">Always free</p><h2>ROOKIE community.</h2></div>
      <p className="membership-disclaimer">Verified Discord members receive ROOKIE access to How to join, Bankroll management, Announcements, Merchandise, Parlays / Promo plays, Free chat, and Winning slips. Free members can view published premium results without seeing unsettled premium selections.</p>
      <p className="membership-disclaimer">Our goal is one staff-selected free play per day when a suitable play is available. No daily play count or outcome is guaranteed. ROOKIE access remains after a paid pass or trial expires.</p>
    </section>
    <section className="membership-breakdown">
      <div className="profile-section-heading"><p className="eyebrow">Feature breakdown</p><h2>Trial and paid access.</h2></div>
      <div className="membership-table-wrap"><table className="membership-table">
        <thead><tr><th>Feature</th>{accessTypes.map((name) => <th key={name}>{name}</th>)}</tr></thead>
        <tbody>{features.map((feature) => <tr key={feature.name}>
          <th scope="row">{feature.name}</th>
          {feature.values.map((value, index) => <td key={`${feature.name}-${accessTypes[index]}`}>{value === 'Included' ? <span className="membership-included"><Check size={15} /> Included</span> : value}</td>)}
        </tr>)}</tbody>
      </table></div>
      <p className="membership-disclaimer">Enrollment is open through Whop. Premium features require verified membership and linked Discord identity; individual tools retain their availability and usage limits. First-time trial checks use verified Whop and Discord identities. IP tracking alone is not proof of a new member. PLAYMAKER is the capper role, not a membership tier. No result, daily pick count, or profit is promised.</p>
      <p className="membership-disclaimer">Player reports currently target NFL/NCAA football, basketball, soccer, and Formula 1 driver standings, subject to provider coverage. Current and previous seasons are shown where available; previous-season data is reused after its initial load. Basketball totals cover available league game logs, not guaranteed complete history. Name suggestions use cached players filtered by sport and league. Reports are not available for every sport, league, or player.</p>
    </section>
  </section>
}