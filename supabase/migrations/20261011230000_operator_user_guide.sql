-- Generated from Playmaker_Picks_User_Guide.html by scripts/create_user_guide_pdf.py.
begin;

create table public.operator_user_guide_content (
  id boolean primary key default true check (id),
  html text not null check (length(html) > 0)
);
alter table public.operator_user_guide_content enable row level security;
revoke all on table public.operator_user_guide_content from public, anon, authenticated;

insert into public.operator_user_guide_content (html) values ('
  <header>
    <h1>Playmaker Picks</h1>
    <p>Discord, website, research tools and API usage<br>Updated October 11, 2026</p>
  </header>

  <section><h2><span>🚀</span>Start here and check your access</h2>
    <p>Run <code>/start</code> for private getting-started steps or <code>/membership_status</code> to check feature eligibility. Use the same Discord account linked to Whop. These commands do not buy, extend or grant access.</p>
    <table><thead><tr><th>Access</th><th>Available features</th></tr></thead><tbody>
      <tr><td>Public / ROOKIE</td><td>Website settled results, capper directory, public sports scores and schedules, plus the configured free Discord channels. No unsettled premium selections.</td></tr>
      <tr><td>Verified HIGHROLLER paid / eligible trial</td><td>Published current picks, member stats and Member Bet Vault when enabled. An eligible first-time trial lasts seven days; the prepaid paid pass is $19.99 for 30 days. No automatic paid conversion or renewal. Checkout and access require enabled launch settings; do not assume deployment alone opens enrollment.</td></tr>
      <tr><td>OPERATOR / Owner</td><td>Team research: website Data Models and NFL Matchup Lab, and Discord <code>/model</code> in Laboratory. Paid/trial membership alone does not unlock these tools. Owner-only <code>/api</code> is a separate cache-refresh tool.</td></tr>
    </tbody></table>
    <p>Features depend on current verification, configured channels and deployment. Adults must be 21+ or the higher local legal age. Playmaker provides analysis and tracking, not sportsbook wagering or guaranteed profits.</p>
  </section>

  <section><h2><span>📷</span>Recording a play from an image</h2>
    <p>OPERATOR/tracker authors post a betting-slip image in the configured image-input channel (IMAGE_INPUT_CHANNEL_ID). No command is needed. Open the bot''s review controls, check selections, odds and units, and use <b>Edit bet</b> or <b>Enter units</b> if needed. Press <b>Confirm and record</b> only after checking the extracted data.</p>
    <div class="callout">The normal confirmed flow replaces the upload with a tracked play message containing your slip and Tail controls; react on that message, not a deleted upload or review prompt. A play copy goes to the confirmation channel. If reposting fails, the original upload may remain the tracked message; verify the bot''s confirmation.</div>
  </section>

  <section><h2><span>✍️</span>Recording a play manually</h2>
    <p>Use <code>/play</code> when you have no image. You need the configured official role and must use the official plays channel. Follow the input form for units, selections and American odds; multi-leg plays collect the remaining legs and calculate combined odds. Check every input before submitting the final leg, which records the play.</p>
    <p>The bot posts a bet embed to the official plays channel, and that embed is what you react to.</p>
  </section>

  <section><h2><span>✅</span>Settling a play with reactions</h2>
    <p>React directly on the tracked play message in a configured settlement channel (official or confirmation). The play owner or Discord Owner can settle by reaction; an OPERATOR role alone does not authorize settling another author''s play. Officials can use the /settle picker. Reactions on unrelated messages/channels are ignored.</p>
    <table>
      <thead><tr><th>Icon</th><th>Result</th><th>What happens</th></tr></thead>
      <tbody>
        <tr><td class="icon">✅</td><td>Win</td><td>Settles immediately and updates the card</td></tr>
        <tr><td class="icon">❌</td><td>Loss</td><td>Settles immediately and updates the card</td></tr>
        <tr><td class="icon">🅿️</td><td>Void</td><td>No change to your unit total</td></tr>
        <tr><td class="icon">🌓</td><td>Partial</td><td>Not processed by reaction — use <code>/settle</code> instead</td></tr>
      </tbody>
    </table>
  </section>

  <section><h2><span>🛠️</span>Tracker and personal tools</h2>
    <table>
      <thead><tr><th>Command</th><th>What it does</th></tr></thead>
      <tbody>
        <tr><td><code>/update_tracker</code></td><td>Officials/moderators refresh the Unit Summary and reconcile missed reactions. The tracker also refreshes automatically every hour, Eastern Time.</td></tr>
        <tr><td><code>/rankings</code></td><td>Posts the current team ranking summary.</td></tr>
        <tr><td><code>/summary</code></td><td>Posts the current team summary and Top Playmakers. Use the configured team-stats channel, as with /rankings.</td></tr>
        <tr><td><code>/play_results</code></td><td>Search published settled official plays by Eastern date range, sport, capper and grade. Browse ten per page. Current OPERATOR authors only; original-post links appear when accessible.</td></tr>
        <tr><td><code>/mystats</code></td><td>Privately shows official-play, Tail and Vault records. Use its share button to publish to the eligible Free or VIP Chat destination. Tail stats use the capper''s risk units, not your personal stake.</td></tr>
        <tr><td><code>/vault_leaderboard</code></td><td>Shows this month''s member Vault records. Vault results are separate from the official capper ledger.</td></tr>
        <tr><td><code>/tracker_start date_value:show</code></td><td>Shows the date the tracker is currently counting from.</td></tr>
      </tbody>
    </table>
  </section>

  <section><h2><span>🔒</span>Commands for officials</h2>
    <table>
      <thead><tr><th>Command</th><th>What it does</th></tr></thead>
      <tbody>
        <tr><td><code>/settle</code></td><td>Opens a paginated list of open official plays. Select a play, then choose its result. Use this for partials or when the tracked message cannot be reacted to. The card''s Play # identifies the record.</td></tr>
        <tr><td><code>/regrade</code></td><td>Select a play from the last two days, then enter remaining legs, new American odds and an optional note. Regraded plays count as zero units.</td></tr>
        <tr><td><code>/unsettle</code></td><td>Select a play settled in the last seven days to reopen an accidental or incorrect settlement. Confirm before applying it.</td></tr>
        <tr><td><code>/edit_play</code></td><td>Select an open official play and edit units, team, selections and leg odds in a modal. Ownership and role checks still apply.</td></tr>
        <tr><td><code>/recap</code></td><td>Officials/moderators preview the previous weekly or monthly recap privately; post:true sends it to the results channel. Scheduled recaps run at 10 AM Eastern on Mondays and on the first day of the month.</td></tr>
        <tr><td><code>/request_insights</code></td><td>Staff can request author insight for up to 50 open picks missing an insight request. Insight is capper-written analysis, not automatically extracted slip text.</td></tr>
        <tr><td><code>/test [image:]</code></td><td>Officials run system diagnostics or parse an attached slip without recording a play.</td></tr>
      </tbody>
    </table>
  </section>

  <section><h2><span>✅</span>Automatic settlement suggestions</h2>
    <p>Every 15 minutes the bot checks supported full-game moneyline, spread and total legs against cached final scores. It posts a suggested WIN/LOSS/VOID in the confirmation channel. An authorized official/moderator must Confirm; Dismiss leaves the play unchanged. Props and unmatched or unsupported games require manual grading. A suggestion is not an automatic official settlement.</p>
  </section>

  <section><h2><span>🏦</span>Member Bet Vault and Tail tracking</h2>
    <p>Eligible members submit in the configured Member Bet Vault channel: attach exactly one static JPEG, PNG or WebP betting-slip image, up to 10 MB. Put units in the caption when needed, then follow the confirmation and settlement controls. The channel is submission-only; read the response before completing your entry.</p>
    <p>Use the Tail control under an official play to register a tail; use <code>/mystats</code> to see your record. Member Vault entries do not become official capper picks. Access failures, missing units or unclear slip data must be resolved before confirming.</p>
  </section>

  <section><h2><span>🌐</span>Website: results, picks and profiles</h2>
    <p>Visit <a href="https://playmakersportsanalytics.com">playmakersportsanalytics.com</a>. The homepage settled ledger shows all sports and grades, ten results per page with Previous/Next controls. The dedicated <code>/results</code> page retains sport filters. Open plays and private account data are excluded from the public ledger.</p>
    <p>This guide is available on the website at <code>/guide</code>, through the <b>Guide (team only)</b> navigation link. Only verified OPERATOR/Owner accounts can open it; paid/trial access alone is insufficient. The page uses the same tab-session access checks as the research tools, and its content RPC checks current access on every fetch.</p>
    <p><code>/cappers</code> lists current verified OPERATOR cappers, with public profiles and settled performance. <code>/picks</code> contains published open picks for verified member/team access. Filter by sport or capper, and open the insight control where available.</p>
    <p>On <code>/account</code>, sign in with Discord, complete age verification, check membership, edit your profile, choose sports/capper follows, and manage alert preferences. The personalized homepage uses those follows. A preference does not guarantee delivery of every alert.</p>
    <p>Verified capper page owners can edit their own page appearance and their own open-pick insights. A verified Owner can edit all capper pages/insights. Available appearance controls include colors, heading font, bio, social links, avatar, banner and section order. Saving or uploading can fail explicitly; verify the saved result.</p>
    <div class="callout">Active website access is verified once per browser tab and retained across navigation/reloads until the tab closes. No 30-second or focus role refresh runs. Sign-out, account/profile changes and membership expiry invalidate it. A removed role blocks new protected database requests even if previously viewed content remains in the tab. The browser cache cannot grant server access.</div>
  </section>

  <section><h2><span>🏟️</span>Website sports center and NFL Matchup Lab</h2>
    <p>The public sports board and sport pages show cached schedules, scores and update times, with links to each sport center. Coverage varies; empty, unsupported and failed feeds are labeled. The browser reads Supabase caches, not API-Sports directly. A sync timestamp is not proof of an instant live score.</p>
    <p>OPERATOR/Owner members can use <code>/nfl/lab</code> for matchup records, home/away splits, last-five form, scoring averages and rest context. NFL efficiency panels add nflverse offensive/defensive EPA per play and success rates over up to ten prior regular-season games; at least three are required. Same-day games are excluded from efficiency history.</p>
    <p>Enter American odds, stake and an optional <b>your estimated probability</b> for implied/break-even probability, payout and conditional expected profit. The calculator is not a sportsbook odds feed. CSV exports include matchup summaries, game history and calculator data. NFL efficiency is descriptive: it does not recalibrate the score-only model.</p>
  </section>

  <section><h2><span>📈</span>Website Data Models: six competitions</h2>
    <p>Open <code>/models</code> with verified OPERATOR/Owner access. Choose NFL, NBA, MLB, NHL, NCAA Football or NCAA Men''s Basketball, then choose an upcoming matchup. The NFL Lab link appears only under the NFL tab.</p>
    <p>Projected scores blend each team''s recent scoring with the opponent''s points allowed, using up to ten prior scored games per team. Estimated win probability uses up to 1,000 prior league score margins. At least three games per team, 30 league games and nonzero margin variation are required. Games at or after the selected start, unfinished games and missing scores are excluded. Otherwise the page explains that no projection is available.</p>
    <p>Unresolved provider teams are excluded with a count. Duplicate fixtures are merged across provider IDs/seasons by teams and exact start time so selections and history count the matchup once. Scored finals take precedence over duplicate schedule rows; fresher data is preferred.</p>
    <div class="callout">These are experimental score-only baselines. They do not adjust for injuries, lineups, home advantage, weather, prices or EPA. They are not calibrated betting recommendations or point-in-time historical backtests. Read sample sizes and source update times.</div>
  </section>

  <section><h2><span>🔬</span>Laboratory /model: fresh league and game data</h2>
    <p>Current OPERATOR/Owner members run <code>/model</code> only in Laboratory, configured by <code>CONFIRMATION_CHANNEL_ID=1328130555568455710</code>. Paid/trial access alone is not enough.</p>
    <ol>
      <li>Choose one of the six model leagues from the dropdown.</li>
      <li>In the modal, enter a game date as YYYY-MM-DD in Eastern time and an optional team filter.</li>
      <li>The bot refreshes the selected league''s current and prior seasons from API-Sports, follows all reported pages and saves the refreshed games. This spends shared API allowance.</li>
      <li>Choose a game from the paginated dropdown; use Previous/Next for more than 25 choices.</li>
      <li>Type POST in the confirmation modal. The finished model is posted publicly in Laboratory; the picker remains private.</li>
    </ol>
    <p>The report includes projected scores, estimated probabilities when supported, prior-game samples, provider refresh time and request count. It uses the same score-only baseline as the website. Snapshot/picker controls expire after three minutes and cannot publish twice. Roles, channel and picker owner are rechecked. If refresh fails, no stale cached model is posted as a success.</p>
  </section>

  <section><h2><span>📚</span>Member stats commands: cached versus refreshed</h2>
    <table><thead><tr><th>Command</th><th>Purpose</th></tr></thead><tbody>
      <tr><td>/schedule sport: [team:]</td><td>Seven-day cached schedule, optionally filtered by team.</td></tr>
      <tr><td>/results sport: [team:]</td><td>Recent provider game results, optionally filtered by team. This is not /play_results, which searches official betting-play settlements.</td></tr>
      <tr><td>/teamstats sport: team:</td><td>Recent team form and scoring from available cached games, not guaranteed season standings.</td></tr>
      <tr><td>/matchup sport: team: opponent:</td><td>Upcoming matchup context from the sports cache, not the Laboratory probability model.</td></tr>
      <tr><td>/gamestats sport: league: game: [player:]</td><td>Choose league/game suggestions and optionally a player/driver for game-specific reports.</td></tr>
      <tr><td>/playerstats sport: league: player:</td><td>Current/previous season player or driver reports; choose the league and name suggestions.</td></tr>
    </tbody></table>
    <p>These commands reply privately and require verified paid/trial access or allowed staff access. Available sports, players and endpoints vary. Without refresh:true they use cached data only and show update times. With refresh:true, live calls require enabled refresh/budget settings and consume your member allowance; player/season lookups may need multiple calls. Previous-season player data can be reused from cache.</p>
    <p>For /schedule, /results, /teamstats and /matchup, refresh:true updates today''s UTC-date feed only; other dates stay cached. For /gamestats it refreshes the selected game''s player snapshot; /playerstats uses current/previous-season scope. Neither refresh mode expands provider subscription coverage.</p>
  </section>

  <section><h2><span>🔌</span>API reference for website and bot integrations</h2>
    <p>Website requests go to the configured Supabase project at <code>/rest/v1/rpc/FUNCTION_NAME</code>. Use the public publishable/anon key for the project and the signed-in user''s access token for protected RPCs, never a service-role key. Functions with “public” in their names can still require team authorization.</p>
    <table><thead><tr><th>Supabase RPC</th><th>Access and request body</th></tr></thead><tbody>
      <tr><td>public_settled_results</td><td>Public settled official ledger. No arguments: {}.</td></tr>
      <tr><td>public_nfl_games / public_nfl_standings / public_nfl_data_status</td><td>Public NFL schedule, standings and freshness. No arguments: {}.</td></tr>
      <tr><td>website_member_access</td><td>Signed-in account''s verified membership/team access. No arguments: {}. This is an access check, not a role-grant endpoint.</td></tr>
      <tr><td>member_current_picks</td><td>Verified member/team access; published open picks only. No arguments: {}.</td></tr>
      <tr><td>public_nfl_model_games / public_nfl_team_efficiency</td><td>Verified OPERATOR/Owner research. No arguments: {}.</td></tr>
      <tr><td>public_sport_model_events</td><td>Verified OPERATOR/Owner research. Example NBA body: {"p_sport_slug":"basketball","p_league_id":"12"}.</td></tr>
      <tr><td>operator_user_guide</td><td>Verified OPERATOR/Owner guide content. No arguments: {}. Returns HTML text; the underlying content table is not accessible to browser accounts.</td></tr>
    </tbody></table>
    <p>With the existing authenticated Supabase JavaScript client, an NBA model-history page is requested as:</p>
    <p><code>supabase.rpc(''public_sport_model_events'', { p_sport_slug: ''basketball'', p_league_id: ''12'' }).order(''start_at'').order(''event_id'').range(0, 999)</code></p>
    <p>Check the returned error before using data. Fetch successive 1,000-row ranges until a raw page contains fewer than 1,000 rows; filtering unresolved teams must not determine whether another provider-history page exists.</p>
    <table><thead><tr><th>Model competition</th><th>Server-side API-Sports GET request</th></tr></thead><tbody>
      <tr><td>NFL / NCAA Football</td><td>https://v1.american-football.api-sports.io/games · league=1 for NFL, league=2 for NCAA · season=YYYY.</td></tr>
      <tr><td>NBA / NCAA Men''s Basketball</td><td>https://v1.basketball.api-sports.io/games · league=12 for NBA, league=8 for NCAA · season=YYYY-YYYY.</td></tr>
      <tr><td>MLB</td><td>https://v1.baseball.api-sports.io/games · league=1 · season=YYYY.</td></tr>
      <tr><td>NHL</td><td>https://v1.hockey.api-sports.io/games · league=57 · season=starting year.</td></tr>
    </tbody></table>
    <p>Bot requests authenticate with the server-only x-apisports-key header. Initial season calls omit page; subsequent calls add page only when paging is reported. Owner /api also uses leagues and teams directory endpoints, teams/statistics where supported, and per-game statistics endpoints. NFL/NCAA/basketball team detail uses games/statistics/teams?id=GAME_ID; NFL/NCAA player detail uses games/statistics/players?id=GAME_ID. Soccer uses fixtures and fixture-scoped statistics instead of games.</p>
    <div class="callout">Use /model, /api or the enabled member refresh commands for normal operations. Ad-hoc direct provider calls bypass this application''s reservation accounting and still consume provider quota. Do not expose the provider key in browser code. Confirm provider licensing for public display and downloads before rollout.</div>
  </section>

  <section><h2><span>🔑</span>Owner /api: refresh a team season cache</h2>
    <p>Only the Discord Owner role can run <code>/api sport: season:</code> in the configured server. Choose a sport and provider season, then choose a league and team by name from paginated dropdowns. This refreshes available team-season caches; it does not produce a /model prediction.</p>
    <p>Provider formats differ: NFL/NCAA football/MLB use calendar-year seasons; NHL uses the starting year (2025 for 2025-26); basketball uses split-year text such as 2025-2026. Coverage depends on the selected sport and subscription.</p>
    <p>The refresh loads the team directory, season schedule, available season-team summaries and supported per-game team/player statistics. NFL/NCAA have no season-summary endpoint in this tool. Other endpoints depend on coverage. Directory queries and detail calls spend shared quota too. Review the completion report: PARTIAL / FAILED means a complete refresh is not claimed. Pickers expire after three minutes; refresh work has a 12-minute interaction limit. Saved components remain cached after failure, but retrying starts again and spends quota again.</p>
  </section>

  <section><h2><span>🔌</span>API calls, limits and freshness</h2>
    <table><thead><tr><th>Action</th><th>Provider calls and limits</th></tr></thead><tbody>
      <tr><td>Website browsing / CSV exports</td><td>Reads stored Supabase data; no direct API-Sports calls. It does not force a provider refresh.</td></tr>
      <tr><td>Member stats without refresh</td><td>Cached only; no live provider request.</td></tr>
      <tr><td>Member stats with refresh:true</td><td>Five requests per user per UTC day across products, 20 member requests per product/day, and a five-minute per-product cooldown. Multi-call lookups count each request, not just one command.</td></tr>
      <tr><td>Staff /model, Owner /api and automated sync</td><td>Use the shared automated allowance, not the member five-request allowance. /model refreshes both seasons and every reported page; /api may call multiple endpoints. Only one /model refresh runs at a time.</td></tr>
      <tr><td>Shared provider ceiling</td><td>7,500 reserved API-Sports requests per UTC day across all products and system/member usage. Existing usage counts. Bot restart does not reset it. Other apps using the same provider key are not counted by this bot.</td></tr>
      <tr><td>nflverse NFL efficiency</td><td>Separate free nflreadpy/nflverse feed, not API-Sports quota. Bot synchronization updates the stored efficiency panels; feed availability still matters.</td></tr>
    </tbody></table>
    <p>API-Sports calls reserve allowance before network access, so failed calls can still consume a reservation. The provider''s own quota and subscription rules also apply. Daily budget resets at midnight UTC (8 PM Eastern during daylight time; 7 PM during standard time). Manual refresh does not guarantee that the source has newer data.</p>
    <p>Automated history backfill resumes saved cursors; routine NFL and multi-sport syncs maintain caches. NFL score checks occur every 15 minutes only during cached game windows. Do not interpret every page load or command as a live provider poll.</p>
  </section>

  <section><h2><span>🛠️</span>Deployment checklist for staff</h2>
    <p>Keep API keys and the Supabase service-role key on the bot server only, never in website VITE variables. Configure GUILD_ID, the required role/channel settings, API_SPORTS_KEY and API_SPORTS_BUDGET_ENABLED=true. MEMBER_STATS_REFRESH_ENABLED controls member on-demand refresh, not /model.</p>
    <p>On the Linux bot host:</p>
    <p><code>cd /opt/discord-bot</code><br><code>git pull</code><br><code>./.venv/bin/python3 -m pip install -r requirements.txt</code><br><code>systemctl restart discord-bot.service</code><br><code>journalctl -u discord-bot.service --since "2 minutes ago" --no-pager</code></p>
    <p>Install dependencies only when requirements change or packages are missing, and always into the service''s virtual environment. Use -r for requirements.txt; python3 is the server executable. nflreadpy&gt;=0.1.5 is the supported minimum in the requirements.</p>
    <p>In Supabase SQL Editor, apply the relevant migrations in order after their prerequisites: 20261011170000_sports_model_history.sql, 20261011180000_nflverse_team_efficiency.sql, 20261011193500_shared_api_daily_budget.sql and 20261011214000_operator_research_access.sql. Pulling Git does not execute SQL. Deploy the website separately; a bot restart does not publish frontend fixes.</p>
    <p>For the protected website guide, apply 20261011230000_operator_user_guide.sql after the research-access migration. Its table stores this guide privately and its RPC checks current OPERATOR/Owner access. Deploy the frontend to expose /guide; no bot restart is needed for this page.</p>
    <p>After restart, check the command-sync log for /model. Verify a live refresh in Laboratory and the website with a real eligible account. Automated tests do not prove production provider coverage, purchases or Discord role mappings.</p>
  </section>

  <section><h2><span>🔑</span>Commands for operators and server managers</h2>
    <table>
      <thead><tr><th>Command</th><th>What it does</th></tr></thead>
      <tbody>
        <tr><td><code>/tracker_start date_value:YYYY-MM-DD</code></td><td>Counts only plays settled on or after this date, for example 2026-09-28. Everything earlier is hidden from the tracker but is never deleted.</td></tr>
        <tr><td><code>/tracker_start date_value:clear</code></td><td>Counts every play again.</td></tr>
        <tr><td><code>/testing enabled:true</code></td><td>Enables the automatic image test-channel flow, which parses without recording. Manual /play is not a database-free dry run.</td></tr>
        <tr><td><code>/testing enabled:false</code></td><td>Returns to normal recording.</td></tr>
      </tbody>
    </table>
    <div class="callout">Both settings revert to their <code>.env</code> defaults when the bot restarts. Set <code>TRACKER_START_DATE</code> or <code>TESTING</code> in <code>.env</code> to make a change permanent.</div>
  </section>

  <section><h2><span>📊</span>How units are scored</h2>
    <p>Wins pay the true price of the bet. Risking 2 units at +250 and winning returns <b>+5 units</b>; the same 2 units at −110 returns <b>+1.82 units</b>. A loss always costs the full amount risked. Voids and regrades count as zero. Your record displays as wins-losses, and the net unit figure reflects actual profit rather than a flat win count.</p>
  </section>

  <section><h2><span>🚨</span>If something does not appear</h2>
    <p>Check the bot, role, channel, account identity and source update time. If a play will not settle by reaction, use the authorized /settle picker. Expired controls require rerunning the command. For website errors, deploy the latest frontend and reload; use Retry when offered.</p>
    <p>Sports-data failure alerts name a job; check its traceback before retrying. A shared-allowance denial is not fixed by restarting. A model can legitimately have insufficient history or no matching game. Unresolved-team and duplicate counts are data-quality notices, not fabricated estimates. Provider or database errors must not be treated as empty successful results.</p>
  </section>

  <footer>Key settings: GUILD_ID · OFFICIAL_CHANNEL_ID · IMAGE_INPUT_CHANNEL_ID · CONFIRMATION_CHANNEL_ID · MEMBER_BET_CHANNEL_ID · API_SPORTS_KEY · API_SPORTS_BUDGET_ENABLED · MEMBER_STATS_REFRESH_ENABLED · TRACKER_START_DATE<br>nflverse data attribution: <a href="https://nflverse.nflverse.com/">nflverse</a> / <a href="https://nflreadpy.nflverse.com/">nflreadpy</a>, CC-BY 4.0.</footer>
');

create function public.operator_user_guide()
returns text language plpgsql stable security definer set search_path = ''
as $$
declare content text;
begin
  perform public.require_operator_research_access();
  select html into strict content from public.operator_user_guide_content where id;
  return content;
end;
$$;
revoke all on function public.operator_user_guide() from public, anon, authenticated;
grant execute on function public.operator_user_guide() to authenticated;

commit;
