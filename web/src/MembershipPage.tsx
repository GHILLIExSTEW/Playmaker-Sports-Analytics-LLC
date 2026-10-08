import { ArrowRight, Check } from 'lucide-react'
import { Link } from 'react-router-dom'

const plans = [
  { name: 'Seven-day trial', price: '$0', duration: '7 days', summary: 'First-time members receive HIGHROLLER access with the same available features as paid members, including analytics and Member Bet Vault submissions. The trial expires without a charge or automatic paid conversion.', featured: false },
  { name: 'HIGHROLLER', price: '$19.99', duration: '30 days prepaid', summary: 'One paid membership for premium analysis, private Discord community access, analytics tools, and Member Bet Vault submissions as they become available. One-time payment; no automatic renewal.', featured: true },
]

const features = [
  { name: 'Public settled-play record', values: ['Included', 'Included'] },
  { name: 'Website current-picks feed', values: ['Published open plays by sport and capper; requires verified access after launch validation', 'Published open plays by sport and capper; requires verified access after launch validation'] },
  { name: 'Personalized sports & capper follows', values: ['Included', 'Included'] },
  { name: 'Weekly recap', values: ['Included', 'Included'] },
  { name: 'Member analysis & Discord community', values: ['7 days of HIGHROLLER access', '30 days of HIGHROLLER access'] },
  { name: 'Interactive cached stats tools', values: ['Matchups, recent form, schedules, results; pending live validation', 'Matchups, recent form, schedules, results; pending live validation'] },
  { name: 'Player & driver stats', values: ['Cached current/previous season reports and optional game detail where supported; pending deployment', 'Cached current/previous season reports and optional game detail where supported; pending deployment'] },
  { name: 'On-demand refresh', values: ['When activated: same 5 API requests per person/day and shared quota/cooldown as paid members; currently disabled', 'When activated: 5 API requests per person/day; season lookups may use multiple requests. Shared quota and cooldown; currently disabled'] },
  { name: 'Member Bet Vault submissions', values: ['Verified eligible trial access required; pending launch validation', 'Verified paid access required; pending launch validation'] },
]

export default function MembershipPage() {
  return <section className="membership-page">
    <Link to="/" className="capper-back-link"><ArrowRight size={16} /> Home</Link>
    <header className="membership-page-heading">
      <p className="eyebrow">Membership</p>
      <h1>One membership. Your seat.</h1>
      <p>Start with a full-access seven-day trial, then choose HIGHROLLER for $19.99 per 30-day pass. ROOKIE is our free community role, not a paid tier. For adults 21+ (or the higher local legal age). Checkout remains closed while launch review is completed.</p>
      <p>Already have access? <Link to="/account">Sign in with the Discord account linked in Whop</Link>, complete age verification, and <Link to="/picks">check the current-picks board</Link>. An expired pass or stale membership sync does not grant premium access.</p>
    </header>
    <section className="membership-plan-grid" aria-label="Membership plans awaiting launch">
      {plans.map((plan) => <article className={`membership-plan${plan.featured ? ' is-featured' : ''}`} key={plan.name}>
        <p className="eyebrow">{plan.featured ? 'One paid membership' : 'Try the community'}</p>
        <h2>{plan.name}</h2>
        <p className="membership-price">{plan.price}<span> / {plan.duration}</span></p>
        <p>{plan.summary}</p>
        <a className="button membership-interest" href={`mailto:support@playmakersportsanalytics.com?subject=${encodeURIComponent(`Playmaker ${plan.name} membership interest`)}`}>Get launch updates <ArrowRight size={16} /></a>
      </article>)}
    </section>
    <section className="membership-breakdown" aria-label="Payment and trial details">
      <div className="profile-section-heading"><p className="eyebrow">Simple access</p><h2>Try it. Choose it. No automatic charges.</h2></div>
      <p className="membership-disclaimer">The first-time trial lasts exactly seven days and does not automatically convert to paid access. Trial members receive the same available HIGHROLLER features and usage limits as paid members. HIGHROLLER costs $19.99 USD for exactly 30 days, not a calendar month. Paid access expires without automatic renewal; purchase another pass to continue. Any taxes or checkout fees must be disclosed before payment.</p>
      <p className="membership-disclaimer">ALL-STAR and historical longer-duration offers are retired from new sales. Existing purchases retain their purchased access period and benefits; this change does not shorten an existing pass or charge an existing member again.</p>
      <p className="membership-disclaimer">Read the draft <Link to="/terms">Terms</Link>, <Link to="/privacy">Privacy notice</Link>, and <Link to="/refunds">Refund policy</Link>. Refund requests for access failures or duplicate charges should be made within seven days; losing picks do not qualify by themselves. Applicable legal rights and Whop rules still apply.</p>
    </section>
    <section className="membership-breakdown" aria-label="Free ROOKIE community">
      <div className="profile-section-heading"><p className="eyebrow">Always free</p><h2>ROOKIE community.</h2></div>
      <p className="membership-disclaimer">Verified Discord members receive ROOKIE access to How to join, Bankroll management, Announcements, Merchandise, Parlays / Promo plays, Free chat, and Winning slips. Free members can view published premium results without seeing unsettled premium selections.</p>
      <p className="membership-disclaimer">Our goal is one staff-selected free play per day when a suitable play is available. No daily play count or outcome is guaranteed. ROOKIE access remains after a paid pass or trial expires.</p>
    </section>
    <section className="membership-breakdown">
      <div className="profile-section-heading"><p className="eyebrow">Feature breakdown</p><h2>Trial and paid access.</h2></div>
      <div className="membership-table-wrap"><table className="membership-table">
        <thead><tr><th>Feature</th>{plans.map((plan) => <th key={plan.name}>{plan.name}</th>)}</tr></thead>
        <tbody>{features.map((feature) => <tr key={feature.name}>
          <th scope="row">{feature.name}</th>
          {feature.values.map((value, index) => <td key={`${feature.name}-${plans[index].name}`}>{value === 'Included' ? <span className="membership-included"><Check size={15} /> Included</span> : value}</td>)}
        </tr>)}</tbody>
      </table></div>
      <p className="membership-disclaimer">Benefits, role mappings, policies, and payment approval must be confirmed before enrollment opens. First-time trial checks use verified Whop and Discord identities; provider-side role and repeat-signup controls still require live verification. IP tracking alone is not proof of a new member. PLAYMAKER is the capper role, not a membership tier. No result, daily pick count, or profit is promised.</p>
      <p className="membership-disclaimer">Player reports currently target NFL/NCAA football, basketball, soccer, and Formula 1 driver standings, subject to provider coverage. Current and previous seasons are shown where available; previous-season data is reused after its initial load. Basketball totals cover available league game logs, not guaranteed complete history. Name suggestions use cached players filtered by sport and league. Reports are not available for every sport, league, or player.</p>
    </section>
  </section>
}