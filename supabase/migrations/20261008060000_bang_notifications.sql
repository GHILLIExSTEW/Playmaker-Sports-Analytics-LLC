begin;

create table public.play_bang_notifications (
  play_id bigint not null references public.plays(id) on delete cascade,
  channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
  claimed_at timestamptz not null default now(),
  message_id text check (message_id ~ '^[0-9]{1,20}$'),
  primary key (play_id, channel_id)
);
alter table public.play_bang_notifications enable row level security;
revoke all on public.play_bang_notifications from public, anon, authenticated;

create function public.claim_play_bang(p_play_id bigint, p_channel_id text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
  claimed boolean;
begin
  if not exists (select 1 from public.plays where id = p_play_id and status = 'win' and message_id is not null) then
    raise exception 'BANG requires a published winning play.';
  end if;
  insert into public.play_bang_notifications (play_id, channel_id)
  values (p_play_id, p_channel_id)
  on conflict do nothing;
  claimed := found;
  return claimed;
end;
$$;

create function public.complete_play_bang(p_play_id bigint, p_channel_id text, p_message_id text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if p_message_id is null or p_message_id !~ '^[0-9]{1,20}$' then
    raise exception 'Invalid BANG message identity.';
  end if;
  update public.play_bang_notifications set message_id = p_message_id
  where play_id = p_play_id and channel_id = p_channel_id;
  if not found then
    raise exception 'BANG notification was not claimed.';
  end if;
  return true;
end;
$$;

revoke all on function public.claim_play_bang(bigint, text) from public, anon, authenticated;
revoke all on function public.complete_play_bang(bigint, text, text) from public, anon, authenticated;
grant execute on function public.claim_play_bang(bigint, text) to service_role;
grant execute on function public.complete_play_bang(bigint, text, text) to service_role;

commit;
