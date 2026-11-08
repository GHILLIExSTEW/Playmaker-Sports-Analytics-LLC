begin;

-- Each new play carries a unique submission key so the bot can resolve a dropped
-- connection by reading the row back instead of replaying the insert (which recorded
-- the same play twice). Existing rows keep NULL; NULLs never conflict.
alter table public.plays add column if not exists submission_key uuid;
create unique index if not exists plays_submission_key_key on public.plays (submission_key);

commit;
