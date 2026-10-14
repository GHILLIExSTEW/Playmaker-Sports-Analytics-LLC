begin;
alter table public.capper_ballots
  add column source_message_id text check (source_message_id ~ '^[0-9]{1,20}$');
commit;
