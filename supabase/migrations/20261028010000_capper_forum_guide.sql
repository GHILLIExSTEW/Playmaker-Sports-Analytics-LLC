begin;

update public.operator_user_guide_content
set html = replace(
  replace(
    html,
    'Upload one slip image per message in Cappers_Ballet (1559647368650166322).',
    'Create one post per slip in the private capper forum (1565167115726618686). Put the matchup in its title and one slip image in the opening post; keep confirmation, voting and discussion inside that post''s thread.'
  ),
  'Daily 3:00 AM Eastern cleanup preserves active and interrupted review messages.',
  'Daily 3:00 AM Eastern cleanup deletes completed forum posts only after saving and reading back their private records in the owner/bot-only records forum (1565170427851047053). One dated post per Eastern submission date retains authors, bet details, original slips, decisions and linked official betting results. Pending results update every five minutes after settlement or corrections; untracked personal bets are not graded. The attached text is an archive-time snapshot; the message carries the current result. Awaiting-confirmation, open-voting, interrupted and uncertain-record posts are preserved.'
);

commit;
