begin;

update public.operator_user_guide_content
set html = replace(
  html,
  '</body>',
  '<section><h2>Discord compatibility and privacy update</h2>'
  '<p>Submit one static JPEG, PNG or WebP slip per private forum post, at most 10 MB and under 16 megapixels. '
  'Read the pinned READ FIRST notice; crop names, account IDs, balances and barcodes before upload. '
  'Images are normalized and sent to OpenAI for extraction/inference, not model training by this bot. '
  'Check the extracted details before confirming. Press Confirm details + set cutoff, then Open form within three minutes. '
  'Insight forms also use a second Open form button to avoid interaction timeouts.</p>'
  '<p>At 3 AM Eastern, completed reviews are privately recorded before source deletion. '
  'Original screenshots and detailed submission archives expire 365 days after review completion and bet resolution, whichever is later. '
  'Pending/unresolved items and unverified source deletion are protected. Tracked official-pick and result-announcement screenshot cards '
  'are replaced with bet/result summaries; database image copies and original notes are scrubbed. '
  'Capper/Discord ID, bet details, decisions and results remain. Older migrated records start a new conservative clock. '
  'Corrections/reopens requeue updates; expired evidence is never recreated. '
  'Downloads and independent-provider backups cannot be recalled. Contact the owner or support@playmakersportsanalytics.com '
  'for access, correction or deletion requests. This policy does not set member-vault retention.</p>'
  '<p>Apply the 20261029000000_discord_compatibility.sql migration, install updated requirements and restart one bot instance. '
  'Staff can run /bot_health for privacy, permissions, requested intents, database and worker status. '
  'Portal Message Content/Server Members toggles require manual verification. Disable required tags in both forums. '
  'Ballot access: configured capper role, owner, bot and administrators. Records: owner, bot and administrators only. '
  'Forum configuration errors back off five minutes. New submissions use bounded event intake with five-minute reconciliation. '
  'Invalid unchanged posts receive one warning across restarts; edit the opening post to retry. '
  'Uncertain notice/record sends require staff to link verified Discord IDs, never blind retries.</p></section></body>'
);

commit;
