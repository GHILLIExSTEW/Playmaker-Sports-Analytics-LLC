begin;

create table public.tracker_message_settings (
  singleton boolean primary key default true check (singleton),
  message_id text check (message_id is null or message_id ~ '^[0-9]{1,20}$')
);
insert into public.tracker_message_settings (singleton, message_id) values (true, null);
alter table public.tracker_message_settings enable row level security;
revoke all on public.tracker_message_settings from public, anon, authenticated;
grant select, update on public.tracker_message_settings to service_role;

commit;
