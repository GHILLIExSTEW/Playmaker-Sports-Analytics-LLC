begin;

-- Existing reviews must still recover from the former text channel after forum cutover.
update public.capper_ballots
set review = review || jsonb_build_object('source_channel_id', '1559647368650166322')
where policy_version = 'slip-v1' and not (review ? 'source_channel_id');

create table public.capper_review_record_days (
  record_date date primary key,
  forum_channel_id text not null check (forum_channel_id ~ '^[0-9]{1,20}$'),
  thread_id text not null check (thread_id = 'sending' or thread_id ~ '^[0-9]{1,20}$')
);
create table public.capper_review_records (
  message_id text primary key references public.capper_ballots(message_id),
  record_date date not null references public.capper_review_record_days(record_date),
  record_message_id text not null check (record_message_id = 'sending' or record_message_id ~ '^[0-9]{1,20}$'),
  rendered_content text not null default ''
);
alter table public.capper_review_record_days enable row level security;
alter table public.capper_review_records enable row level security;
revoke all on public.capper_review_record_days, public.capper_review_records from public, anon, authenticated;
grant select, insert, update on public.capper_review_record_days, public.capper_review_records to service_role;

create view public.capper_review_record_snapshots as
select r.*, d.forum_channel_id, d.thread_id,
  to_jsonb(b) || jsonb_build_object('review', b.review - 'slip_base64') as ballot,
  case when p.id is not null then jsonb_build_object(
    'id', p.id, 'status', p.status, 'units', p.units, 'odds', p.odds, 'message_id', p.message_id
  ) end as play
from public.capper_review_records r
join public.capper_review_record_days d using (record_date)
join public.capper_ballots b using (message_id)
left join public.plays p on p.ballot_message_id = b.message_id;
revoke all on public.capper_review_record_snapshots from public, anon, authenticated;
grant select on public.capper_review_record_snapshots to service_role;

commit;
