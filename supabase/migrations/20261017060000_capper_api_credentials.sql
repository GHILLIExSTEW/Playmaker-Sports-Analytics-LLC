begin;

create table public.capper_api_keys (
  id uuid primary key default gen_random_uuid(),
  discord_user_id text not null check (discord_user_id ~ '^[0-9]{1,20}$'),
  label text not null check (char_length(btrim(label)) between 1 and 100),
  key_hash text not null unique check (key_hash ~ '^[0-9a-f]{64}$'),
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  last_used_at timestamptz
);

create table public.capper_api_requests (
  id uuid primary key default gen_random_uuid(),
  key_id uuid not null references public.capper_api_keys(id),
  product text not null,
  path text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status integer check (status between 100 and 599),
  check ((completed_at is null) = (status is null))
);
create index capper_api_requests_key_started_idx
  on public.capper_api_requests(key_id, started_at);

alter table public.capper_api_keys enable row level security;
alter table public.capper_api_requests enable row level security;
revoke all on public.capper_api_keys, public.capper_api_requests from public, anon, authenticated;
grant select, insert, update on public.capper_api_keys, public.capper_api_requests to service_role;

create function public.list_capper_api_keys()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not exists (
    select 1 from public.member_profiles where user_id = auth.uid() and age_verified_at is not null
  ) or public.website_can_edit_all_cappers() is distinct from true then
    raise exception 'Age-verified current Owner role required.' using errcode = '42501';
  end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', k.id, 'discord_user_id', k.discord_user_id, 'label', k.label,
      'created_at', k.created_at, 'revoked_at', k.revoked_at, 'last_used_at', k.last_used_at,
      'requests_today', (
        select count(*) from public.capper_api_requests r
        where r.key_id = k.id
          and r.started_at >= ((now() at time zone 'UTC')::date::timestamp at time zone 'UTC')
          and r.started_at < (((now() at time zone 'UTC')::date + 1)::timestamp at time zone 'UTC')
      )
    ) order by k.created_at desc, k.id)
    from public.capper_api_keys k
  ), '[]'::jsonb);
end;
$$;

create function public.issue_capper_api_key(p_discord_user_id text, p_label text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  token text;
  key_id uuid;
begin
  if auth.uid() is null or not exists (
    select 1 from public.member_profiles where user_id = auth.uid() and age_verified_at is not null
  ) or public.website_can_edit_all_cappers() is distinct from true then
    raise exception 'Age-verified current Owner role required.' using errcode = '42501';
  end if;
  if p_discord_user_id is null or p_discord_user_id !~ '^[0-9]{1,20}$'
    or (p_discord_user_id = any(public.website_capper_ids())) is distinct from true
    or not exists (
      select 1 from auth.identities i
      join public.member_profiles p on p.user_id = i.user_id and p.age_verified_at is not null
      where i.provider = 'discord' and i.provider_id = p_discord_user_id
    ) then
    raise exception 'Recipient must be a current capper with a linked Discord Auth identity and an age-verified member profile.'
      using errcode = '42501';
  end if;
  if p_label is null or char_length(btrim(p_label)) not between 1 and 100 then
    raise exception 'Label must contain 1 to 100 characters.';
  end if;
  token := 'pmcap_' || replace(pg_catalog.gen_random_uuid()::text, '-', '')
    || replace(pg_catalog.gen_random_uuid()::text, '-', '');
  insert into public.capper_api_keys(discord_user_id, label, key_hash, created_by)
  values (p_discord_user_id, btrim(p_label),
    pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(token, 'UTF8')), 'hex'), auth.uid())
  returning id into key_id;
  -- The raw credential exists only in this issuance response, never in stored audit data.
  return jsonb_build_object('id', key_id, 'token', token);
end;
$$;

create function public.revoke_capper_api_key(p_id uuid)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not exists (
    select 1 from public.member_profiles where user_id = auth.uid() and age_verified_at is not null
  ) or public.website_can_edit_all_cappers() is distinct from true then
    raise exception 'Age-verified current Owner role required.' using errcode = '42501';
  end if;
  update public.capper_api_keys set revoked_at = coalesce(revoked_at, now()) where id = p_id;
  return found;
end;
$$;

create function public.reserve_capper_api_call(p_key_hash text, p_product text, p_path text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  credential public.capper_api_keys;
  reservation text;
  request_id uuid;
begin
  if p_key_hash is null or p_key_hash !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('state', 'unauthorized', 'reason', 'Invalid API credential');
  end if;
  -- Serialize revocation and use before entering the existing shared budget lock.
  select * into credential from public.capper_api_keys where key_hash = p_key_hash for update;
  if not found or credential.revoked_at is not null then
    return jsonb_build_object('state', 'unauthorized', 'reason', 'Invalid API credential');
  end if;
  if (credential.discord_user_id = any(public.website_capper_ids())) is distinct from true
    or not exists (
      select 1 from auth.identities i
      join public.member_profiles p on p.user_id = i.user_id and p.age_verified_at is not null
      where i.provider = 'discord' and i.provider_id = credential.discord_user_id
    ) then
    return jsonb_build_object('state', 'unauthorized', 'reason', 'Recipient is no longer a verified current capper');
  end if;
  if p_product is null or p_product not in (
    'american-football', 'basketball', 'football', 'hockey', 'baseball',
    'rugby', 'handball', 'volleyball', 'mma', 'formula-1'
  ) then
    return jsonb_build_object('state', 'denied', 'reason', 'Invalid API product');
  end if;
  if p_path is null or char_length(p_path) > 512
    or p_path !~ '^/[a-z0-9-]+(/[a-z0-9-]+)*$' or p_path ~ '(^|/)status(/|$)' then
    return jsonb_build_object('state', 'denied', 'reason', 'Invalid API data path');
  end if;
  -- System reservations bypass member limits, not the shared UTC/provider allowance.
  reservation := public.reserve_api_requests(p_product, null, 1);
  if reservation is distinct from 'reserved' then
    return jsonb_build_object('state', 'denied', 'reason', reservation);
  end if;
  insert into public.capper_api_requests(key_id, product, path)
    values (credential.id, p_product, p_path) returning id into request_id;
  update public.capper_api_keys set last_used_at = now() where id = credential.id;
  return jsonb_build_object('state', 'reserved', 'request_id', request_id);
end;
$$;

create function public.finish_capper_api_call(p_request_id uuid, p_status integer)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if p_status is null or p_status not between 100 and 599 then
    raise exception 'Invalid HTTP completion status.';
  end if;
  -- Completion is write-once and never refunds a reservation, including provider failures.
  update public.capper_api_requests set completed_at = now(), status = p_status
    where id = p_request_id and completed_at is null;
  return found;
end;
$$;

revoke all on function public.list_capper_api_keys() from public, anon, authenticated;
revoke all on function public.issue_capper_api_key(text, text) from public, anon, authenticated;
revoke all on function public.revoke_capper_api_key(uuid) from public, anon, authenticated;
revoke all on function public.reserve_capper_api_call(text, text, text) from public, anon, authenticated;
revoke all on function public.finish_capper_api_call(uuid, integer) from public, anon, authenticated;
grant execute on function public.list_capper_api_keys() to authenticated;
grant execute on function public.issue_capper_api_key(text, text) to authenticated;
grant execute on function public.revoke_capper_api_key(uuid) to authenticated;
grant execute on function public.reserve_capper_api_call(text, text, text) to service_role;
grant execute on function public.finish_capper_api_call(uuid, integer) to service_role;

commit;
