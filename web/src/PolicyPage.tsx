import { Link } from 'react-router-dom'

export type PolicyKind = 'terms' | 'privacy' | 'refunds'

const titles: Record<PolicyKind, string> = {
  terms: 'Terms of service',
  privacy: 'Privacy notice',
  refunds: 'Refund policy',
}

function SupportContact() {
  return <a href="mailto:support@playmakersportsanalytics.com">support@playmakersportsanalytics.com</a>
}

function LegalContact() {
  return <a href="mailto:legal@playmakersportsanalytics.com">legal@playmakersportsanalytics.com</a>
}

function Terms() {
  return <>
    <h2>Who we are and who may join</h2>
    <p>Playmaker Picks is operated by Playmaker Sports Analytics, LLC. Membership is for people aged 21 or older, or the higher age required where they live. Do not use the service where prohibited by applicable law.</p>
    <h2>Analysis, not wagering</h2>
    <p>We provide sports analysis, informational picks, and community discussion. PLAYMAKER identifies an approved capper, not a paid membership tier. We do not accept, place, transmit, or hold wagers. No win, outcome, daily pick count, or profit is guaranteed. Historical results do not predict future results. You are responsible for your decisions and compliance with local law; participation never requires placing a wager.</p>
    <h2>Prepaid membership</h2>
    <p>HIGHROLLER is our single paid membership: $19.99 USD for a one-time pass lasting exactly 30 days, not a calendar month. First-time eligible members may claim a free seven-day trial with the same available HIGHROLLER features, including analytics and Member Bet Vault submissions, subject to the same usage limits. The trial expires without an automatic charge or paid conversion. Paid passes also expire without automatic renewal; another purchase is required to continue access. ROOKIE is our free verified Discord community role; it and our public settled-play record do not require a paid pass.</p>
    <p>Trials require a provider-approved trial plan and a verified linked Discord identity. One trial is recorded per Whop buyer and Discord account for our seller; creating another membership, changing the linked identity, or extending the recorded trial window does not reset eligibility. Prior paid membership before the trial also disqualifies that identity. These checks are not a guarantee against people creating multiple accounts. IP addresses alone do not establish identity.</p>
    <p>ALL-STAR and historical 90-, 180-, and 365-day offers are retired from new sales. Existing purchases retain their purchased access period and benefits; the new offer does not shorten existing access or create another charge.</p>
    <p>Review the total price, exact expiration, included channels and features, and any taxes or checkout fees before paying. Access begins according to the purchase dates recorded by Whop. Included benefits and any deployment or usage limits must be stated in the offer; do not assume all channels or archives are included. Removing the Discord role or leaving the server does not by itself request a refund.</p>
    <h2>Discord and access verification</h2>
    <p>Checkout and HIGHROLLER role assignment use Whop and its Discord integration. Connect your own Discord account and follow Claim Access instructions. The bot separately verifies payment or eligible trial entitlement and identity for analytics and Member Vault access. ROOKIE, arbitrary free or complimentary access, refunded or expired passes, and otherwise unverified Whop access do not qualify for new vault submissions. Explicitly recorded owner grants are a separate exception, not proof of payment. Verification normally runs every five minutes and may be delayed by provider outages. ROOKIE community access remains after a trial or paid pass expires.</p>
    <h2>Member Bet Vault</h2>
    <p>Photo tickets require currently verified paid access, an eligible seven-day trial, or an explicitly recorded owner grant. A Discord role alone is not sufficient. Remove names, account identifiers, balances, barcodes, and other sensitive information before uploading. Original photos may briefly be visible in the Discord submission channel before removal; masking is not a guarantee that nobody can view or download them. If storage fails, the original may remain and you will receive a private warning.</p>
    <p>Photos are processed privately for extraction and review. After confirmation, the public card displays the submitter, matchup, units, and odds while selections remain hidden until settlement. Settled cards reveal sanitized selections and results, not the original photo. Review extracted details before confirmation. Unsupported or ambiguous tickets require moderator review; API outcome verification is not proof that a wager was placed. Already-confirmed tickets may settle after membership expires. Member results remain separate from official capper records.</p>
    <h2>Community conduct and content</h2>
    <p>Do not harass others, post unlawful material, impersonate another person, manipulate tickets or results, share someone else's private data, or rebroadcast copyrighted events without permission. Submit only content you are entitled to share. You authorize the processing and display needed to operate the vault as described above. Do not redistribute paid content without permission.</p>
    <p>We may restrict access for abuse, fraud, security risks, or rule violations. Contact support to dispute an access decision. Restrictions do not waive your statutory rights or entitle us to withhold a refund required by law.</p>
    <h2>Refunds, service issues, and changes</h2>
    <p>The <Link to="/refunds">refund policy</Link> explains the seven-day request window and exceptions. Contact <SupportContact /> for access problems. For legal questions, contact <LegalContact />. Material changes to offers or policies should be communicated before they take effect and must not override rights attached to an existing purchase.</p>
    <h2>Responsible participation</h2>
    <p>Never wager money needed for essential expenses or chase losses. Stop if gambling affects your finances or well-being. In the United States, call or text 1-800-MY-RESET for gambling support. Outside the United States, contact your local support service.</p>
  </>
}

function Privacy() {
  return <>
    <h2>Information we process</h2>
    <p>Playmaker Sports Analytics, LLC operates Playmaker Picks. Depending on how you use the service, we process account identifiers, email addresses, profile details, saved preferences, followed picks, Discord identifiers, support correspondence, membership dates and status, payment references, and uploaded ticket photos and extracted ticket details.</p>
    <p>Whop processes checkout and billing. Our integration reads membership and payment evidence, including refund or dispute status, to verify access. It also retains the Whop buyer ID, Discord ID, original trial dates, and trial membership reference to enforce one trial per identity after expiry or cancellation. Our application does not collect card numbers or CVCs, and this trial implementation does not collect or store IP addresses. Whop and Discord also process data under their own policies.</p>
    <h2>Why we use information</h2>
    <p>We use information to maintain accounts, deliver requested features, verify paid access, assign access through integrations, process tickets, resolve support or refund requests, audit changes, prevent abuse, and meet applicable obligations. Website account deletion does not automatically cancel or refund a Whop purchase.</p>
    <h2>Ticket images and visibility</h2>
    <p>Uploaded photos are stored in private Supabase storage and sent to the configured OpenAI image-processing service for extraction. Authorized review and moderation workflows can access ticket content. Strip sensitive details before submitting; metadata removal does not remove private information visible in the image.</p>
    <p>The original can briefly be visible in Discord before deletion, and failed processing may leave it there. Discord deletion cannot recall copies other users have downloaded. Public vault cards identify the submitter and show matchup, units, and odds; settled cards also show sanitized selections and results. Do not submit information you do not want disclosed through that flow.</p>
    <h2>Other providers and public profiles</h2>
    <p>We use Supabase for authentication, account data, and private records; AWS Amplify for website hosting; Whop for billing and membership; Discord for community access; OpenAI for ticket-image extraction; and API-Sports for event data. Public pages may load external images, exposing network information such as your IP address to their hosts. Providers may process information outside your location under their own terms and policies.</p>
    <p>You can choose whether your website member profile is public in account settings. Official capper records are public and separate from member profiles. We may disclose relevant information when legally required or necessary to address fraud, security incidents, or disputes.</p>
    <h2>Browser storage and security</h2>
    <p>Authentication uses browser storage to keep you signed in. Our service uses restricted database and storage access, but no system can guarantee absolute security. Do not share account credentials, API keys, or unredacted financial screenshots.</p>
    <h2>Retention and your requests</h2>
    <p>We have not finalized a retention schedule; this is a launch-review item. Deleting a website account removes the member profile and preferences through that workflow, not automatically Discord messages, vault records, official plays, payment-provider records, or audit history. Some records may need to be retained for legal, dispute, or security purposes.</p>
    <p>For access, correction, deletion, or other applicable privacy rights, contact <LegalContact />. We may verify identity before acting. We will explain what can be deleted and any required retention; we do not promise automatic deletion from independent providers. This service is not intended for people under 21. Contact us if an underage person has supplied data.</p>
  </>
}

function Refunds() {
  return <>
    <h2>Request within seven days</h2>
    <p>Contact <SupportContact /> within seven days of purchase to request a refund for a duplicate charge or a failure to deliver the access you purchased. Include your Whop purchase or membership reference, purchase date, and a description of the problem. Never send a full card number, CVC, password, or API key.</p>
    <p>We will review payment and access records and help troubleshoot linking or delivery issues. If a duplicate charge or failure to deliver purchased access is confirmed, we will refund the affected charge through Whop. This is not a seven-day satisfaction guarantee, and we do not promise a specific bank-processing time.</p>
    <h2>What does not qualify</h2>
    <p>Losing picks, a losing streak, dissatisfaction with sporting outcomes, or not placing wagers do not qualify by themselves. No profits or minimum number of daily picks are promised. Simply leaving Discord or not using delivered access does not qualify for a discretionary refund. We do not offer discretionary prorated refunds for unused days.</p>
    <h2>Prepaid, not auto-renewing</h2>
    <p>New HIGHROLLER paid passes cost $19.99 USD for exactly 30 days and expire without automatically charging again. The optional full-access seven-day trial expires without an automatic charge or paid conversion. Previously purchased ALL-STAR or HIGHROLLER passes keep their original access period and benefits; the retirement of longer-duration offers does not change those purchases.</p>
    <h2>Refunded access</h2>
    <p>Refunded memberships do not qualify for new Member Vault submissions, including when a payment is partially refunded. The bot normally detects changes during its five-minute verification cycle; Discord role changes are managed separately by Whop. Previously confirmed tickets remain eligible for settlement.</p>
    <h2>Your rights and provider rules</h2>
    <p>The seven-day request window and discretionary exclusions do not limit refunds or remedies required by applicable law or Whop's rules. Report duplicate charges or access failures even if you discover them later; we will review whether an exception or required remedy applies. You may also use Whop's buyer-support process. Legal questions can be sent to <LegalContact />.</p>
  </>
}

export default function PolicyPage({ kind }: { kind: PolicyKind }) {
  return <section className="membership-page policy-page">
    <Link to="/" className="capper-back-link">Home</Link>
    <header className="membership-page-heading">
      <p className="eyebrow">Playmaker Sports Analytics, LLC</p>
      <h1>{titles[kind]}</h1>
      <p>Draft for launch review - updated October 5, 2026. Checkout remains closed. These drafts require professional review and are not a statement of Whop approval.</p>
    </header>
    <nav className="policy-nav" aria-label="Customer policies">
      <Link to="/terms">Terms</Link><Link to="/privacy">Privacy</Link><Link to="/refunds">Refunds</Link>
    </nav>
    <article className="policy-content">
      {kind === 'terms' ? <Terms /> : kind === 'privacy' ? <Privacy /> : <Refunds />}
    </article>
  </section>
}
