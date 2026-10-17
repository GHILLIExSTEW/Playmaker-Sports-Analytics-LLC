begin;

create table public.play_card_assets (
  play_id bigint primary key references public.plays(id) on delete cascade,
  slip_base64 text check (length(slip_base64) <= 16777216),
  badge_base64 text not null check (length(badge_base64) <= 16777216),
  badge_label text not null,
  created_at timestamptz not null default now()
);
alter table public.play_card_assets enable row level security;
revoke all on public.play_card_assets from public, anon, authenticated;
grant select, insert, update on public.play_card_assets to service_role;

alter table public.play_bang_notifications
  add column state text not null default 'claimed'
    check (state in ('claimed', 'delivered', 'failed', 'uncertain')),
  add column attempt_id uuid not null default gen_random_uuid(),
  add column updated_at timestamptz not null default now(),
  add column displayed_status text,
  add column last_error text,
  add column is_test boolean not null default false,
  add column reconciled_by text,
  add column reconciled_at timestamptz;

update public.play_bang_notifications
set state = case when message_id is null then 'uncertain' else 'delivered' end,
    displayed_status = case when message_id is not null then 'win' end;

update public.play_bang_notifications n set is_test = true
from public.plays p where p.id = n.play_id and p.tracking_scope = 'test';

grant select on public.play_bang_notifications to service_role;

create table public.play_bang_events (
  play_id bigint primary key references public.plays(id) on delete cascade,
  revision uuid not null default gen_random_uuid(),
  updated_at timestamptz not null default now()
);
alter table public.play_bang_events enable row level security;
revoke all on public.play_bang_events from public, anon, authenticated;
grant select on public.play_bang_events to service_role;

create function public.queue_play_bang_event()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if new.message_id is not null and (
    new.tracking_scope = 'official' and new.status = 'win'
    or exists (select 1 from public.play_bang_notifications where play_id = new.id)
  ) then
    insert into public.play_bang_events (play_id) values (new.id)
    on conflict (play_id) do update set revision = gen_random_uuid(), updated_at = now();
  end if;
  return new;
end;
$$;

create trigger queue_play_bang_event
after update of status, tracking_scope, message_id, units, odds on public.plays
for each row when (
  old.status is distinct from new.status or old.tracking_scope is distinct from new.tracking_scope
  or old.message_id is distinct from new.message_id or old.units is distinct from new.units
  or old.odds is distinct from new.odds
)
execute function public.queue_play_bang_event();

create function public.finish_play_bang_event(p_play_id bigint, p_revision uuid)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  delete from public.play_bang_events where play_id = p_play_id and revision = p_revision;
  return found;
end;
$$;

drop function public.claim_play_bang(bigint, text);
drop function public.complete_play_bang(bigint, text, text);

create function public.begin_play_bang(p_play_id bigint, p_channel_id text, p_is_test boolean)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  token uuid;
begin
  if not exists (
    select 1 from public.plays
    where id = p_play_id and status = 'win' and tracking_scope = 'official' and message_id is not null
  ) then
    raise exception 'BANG requires a published official winning play.';
  end if;
  insert into public.play_bang_notifications (play_id, channel_id, is_test)
  values (p_play_id, p_channel_id, p_is_test)
  on conflict do nothing
  returning attempt_id into token;
  return token;
end;
$$;

create function public.finish_play_bang(
  p_play_id bigint, p_channel_id text, p_attempt_id uuid, p_message_id text, p_status text
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if p_message_id is null or p_message_id !~ '^[0-9]{1,20}$'
     or p_status is null or p_status not in ('win', 'loss', 'void', 'partial', 'open', 'regraded', 'not_official') then
    raise exception 'Invalid BANG message identity or displayed status.';
  end if;
  update public.play_bang_notifications
  set message_id = p_message_id, state = 'delivered', displayed_status = p_status,
      last_error = null, updated_at = now()
  where play_id = p_play_id and channel_id = p_channel_id and attempt_id = p_attempt_id
    and (message_id is null or message_id = p_message_id);
  if not found then
    raise exception 'BANG delivery attempt changed; staff reconciliation is required.';
  end if;
  return true;
end;
$$;

create function public.fail_play_bang(
  p_play_id bigint, p_channel_id text, p_attempt_id uuid, p_error text, p_uncertain boolean
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  update public.play_bang_notifications
  set state = case when p_uncertain then 'uncertain' else 'failed' end,
      last_error = left(p_error, 1500), updated_at = now()
  where play_id = p_play_id and channel_id = p_channel_id and attempt_id = p_attempt_id
    and state = 'claimed' and message_id is null;
  return found;
end;
$$;

create function public.recover_play_bang(
  p_play_id bigint, p_channel_id text, p_attempt_id uuid, p_actor_id text, p_message_id text
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  delivery public.play_bang_notifications;
  token uuid;
begin
  if p_actor_id is null or p_actor_id !~ '^[0-9]{1,20}$'
     or (p_message_id is not null and p_message_id !~ '^[0-9]{1,20}$') then
    raise exception 'Invalid staff or message identity.';
  end if;
  select * into delivery from public.play_bang_notifications
  where play_id = p_play_id and channel_id = p_channel_id for update;
  if not found or delivery.attempt_id <> p_attempt_id then
    raise exception 'Delivery changed. Read /bang_status again before recovery.';
  end if;
  if delivery.state = 'claimed' and delivery.updated_at > now() - interval '10 minutes' then
    raise exception 'Delivery is still in flight. Wait at least ten minutes before recovery.';
  end if;
  if delivery.message_id is not null then
    raise exception 'A delivered message is already tracked. Do not resend it.';
  end if;
  if p_message_id is null and not exists (
    select 1 from public.plays where id = p_play_id and status = 'win'
      and tracking_scope = 'official' and message_id is not null
  ) then
    raise exception 'Only published official winning plays can be retried.';
  end if;
  token := gen_random_uuid();
  update public.play_bang_notifications
  set attempt_id = token, message_id = p_message_id,
      state = case when p_message_id is null then 'claimed' else 'delivered' end,
      displayed_status = null, last_error = null, updated_at = now(),
      reconciled_by = p_actor_id, reconciled_at = now()
  where play_id = p_play_id and channel_id = p_channel_id;
  return token;
end;
$$;

create function public.stale_play_bangs(p_is_test boolean, p_test_channel_id text default null)
returns table (play_id bigint)
language sql security definer set search_path = ''
as $$
  select distinct n.play_id
  from public.play_bang_notifications n join public.plays p on p.id = n.play_id
  where (n.is_test or n.channel_id = coalesce(p_test_channel_id, '')) = p_is_test
    and n.state = 'delivered'
    and n.displayed_status is distinct from
      case when p.tracking_scope = 'official' then p.status else 'not_official' end
  order by n.play_id
  limit 100;
$$;

revoke all on function public.begin_play_bang(bigint, text, boolean) from public, anon, authenticated;
revoke all on function public.finish_play_bang(bigint, text, uuid, text, text) from public, anon, authenticated;
revoke all on function public.fail_play_bang(bigint, text, uuid, text, boolean) from public, anon, authenticated;
revoke all on function public.recover_play_bang(bigint, text, uuid, text, text) from public, anon, authenticated;
revoke all on function public.stale_play_bangs(boolean, text) from public, anon, authenticated;
revoke all on function public.queue_play_bang_event() from public, anon, authenticated;
revoke all on function public.finish_play_bang_event(bigint, uuid) from public, anon, authenticated;
grant execute on function public.begin_play_bang(bigint, text, boolean) to service_role;
grant execute on function public.finish_play_bang(bigint, text, uuid, text, text) to service_role;
grant execute on function public.fail_play_bang(bigint, text, uuid, text, boolean) to service_role;
grant execute on function public.recover_play_bang(bigint, text, uuid, text, text) to service_role;
grant execute on function public.stale_play_bangs(boolean, text) to service_role;
grant execute on function public.finish_play_bang_event(bigint, uuid) to service_role;

commit;
