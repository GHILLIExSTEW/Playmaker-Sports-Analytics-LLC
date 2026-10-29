begin;

create table public.capper_invalid_slips (
  message_id text primary key check (message_id ~ '^[0-9]{1,20}$'),
  forum_channel_id text,
  fingerprint text not null,
  reason text not null,
  retired boolean not null default false,
  updated_at timestamptz not null default now()
);
create table public.capper_forum_notices (
  forum_channel_id text primary key,
  thread_id text not null check (thread_id = 'sending' or thread_id ~ '^[0-9]{1,20}$')
);
create index capper_invalid_slip_forum on public.capper_invalid_slips(forum_channel_id);
alter table public.capper_invalid_slips enable row level security;
alter table public.capper_forum_notices enable row level security;
revoke all on public.capper_invalid_slips, public.capper_forum_notices from public, anon, authenticated;
grant select, insert, update on public.capper_invalid_slips, public.capper_forum_notices to service_role;

create function public.claim_invalid_capper_slip(p_message_id text, p_fingerprint text, p_reason text, p_forum_id text default null)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  insert into public.capper_invalid_slips(message_id, forum_channel_id, fingerprint, reason)
  values(p_message_id, p_forum_id, p_fingerprint, p_reason)
  on conflict (message_id) do update
    set fingerprint = excluded.fingerprint, reason = excluded.reason, retired = false, updated_at = now()
    where public.capper_invalid_slips.fingerprint <> excluded.fingerprint;
  return found;
end;
$$;

create view public.capper_invalid_slip_work as
select i.message_id, i.forum_channel_id from public.capper_invalid_slips i
where not i.retired and not exists(select 1 from public.capper_ballots b where b.message_id = i.message_id);
revoke all on public.capper_invalid_slip_work from public, anon, authenticated;
grant select on public.capper_invalid_slip_work to service_role;

alter table public.capper_ballot_scan add column forum_scanned_at timestamptz;
alter table public.capper_ballots add column official_channel_id text;
alter table public.capper_review_records
  add column revision bigint not null default 0,
  add column rendered_revision bigint not null default -1,
  add column source_deleted_at timestamptz,
  add column source_checked_at timestamptz,
  add column evidence_eligible_at timestamptz,
  add column evidence_purge_started_at timestamptz,
  add column evidence_removed_at timestamptz;

create function public.capper_record_is_resolved(p_message_id text)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists(
    select 1 from public.capper_ballots b
    where b.message_id = p_message_id and b.state not in ('pending', 'processing')
      and not exists(
        select 1 from public.plays p where p.ballot_message_id = b.message_id
        and (p.status is null or p.status not in ('win', 'loss', 'void') or p.message_id is null)
      )
  );
$$;

create function public.touch_capper_record(p_message_id text)
returns void language sql security definer set search_path = '' as $$
  update public.capper_review_records set revision = revision + 1,
    evidence_eligible_at = case when public.capper_record_is_resolved(p_message_id) then now() end
  where message_id = p_message_id;
$$;

create function public.initialize_capper_record()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  new.evidence_eligible_at := case when public.capper_record_is_resolved(new.message_id) then now() end;
  return new;
end;
$$;
create trigger initialize_capper_record before insert on public.capper_review_records
for each row execute function public.initialize_capper_record();

create function public.changed_capper_record_play()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    perform public.touch_capper_record(old.ballot_message_id);
    return old;
  end if;
  if tg_op = 'UPDATE' then
    if (new.status, new.units, new.odds, new.message_id, new.ballot_message_id)
       is not distinct from (old.status, old.units, old.odds, old.message_id, old.ballot_message_id) then
      return new;
    end if;
    if old.ballot_message_id is distinct from new.ballot_message_id then
      perform public.touch_capper_record(old.ballot_message_id);
    end if;
  end if;
  perform public.touch_capper_record(new.ballot_message_id);
  return new;
end;
$$;
create trigger changed_capper_record_play after insert or update or delete on public.plays
for each row execute function public.changed_capper_record_play();

create function public.changed_capper_record_ballot()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if (new.state, new.votes, new.error, new.review - array['slip_base64','source_text','image_filename'])
     is distinct from (old.state, old.votes, old.error, old.review - array['slip_base64','source_text','image_filename']) then
    perform public.touch_capper_record(new.message_id);
  end if;
  return new;
end;
$$;
create trigger changed_capper_record_ballot after update on public.capper_ballots
for each row execute function public.changed_capper_record_ballot();

-- Existing records get a conservative new retention clock; no historical evidence is immediately purged.
update public.capper_review_records r set evidence_eligible_at =
  case when public.capper_record_is_resolved(r.message_id) then now() end;

drop view public.capper_review_record_snapshots;
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

create view public.capper_review_record_work as
select * from public.capper_review_record_snapshots
where revision <> rendered_revision
  or (source_deleted_at is null and (source_checked_at is null or source_checked_at <= now() - interval '1 day'))
  or (evidence_removed_at is null and source_deleted_at is not null
      and evidence_eligible_at <= now() - interval '365 days');
revoke all on public.capper_review_record_snapshots, public.capper_review_record_work from public, anon, authenticated;
grant select on public.capper_review_record_snapshots, public.capper_review_record_work to service_role;
create index capper_record_dirty on public.capper_review_records(message_id)
  where revision <> rendered_revision;
create index capper_record_expiry on public.capper_review_records(evidence_eligible_at)
  where evidence_removed_at is null and source_deleted_at is not null;
create index capper_record_source_checks on public.capper_review_records(source_checked_at)
  where source_deleted_at is null;

create function public.begin_capper_evidence_expiry(p_message_id text, p_revision bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
declare r public.capper_review_records;
begin
  select * into r from public.capper_review_records where message_id = p_message_id for update;
  if not found or r.revision <> p_revision or r.evidence_removed_at is not null
    or r.source_deleted_at is null or r.evidence_eligible_at is null
    or r.evidence_eligible_at > now() - interval '365 days'
    or not public.capper_record_is_resolved(p_message_id) then return false; end if;
  update public.capper_review_records set evidence_purge_started_at = coalesce(evidence_purge_started_at, now())
    where message_id = p_message_id;
  update public.play_card_assets a set slip_base64 = null
    from public.plays p where p.id = a.play_id and p.ballot_message_id = p_message_id;
  return true;
end;
$$;

create function public.protect_expired_capper_artwork()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if exists(select 1 from public.plays p join public.capper_review_records r
    on r.message_id = p.ballot_message_id where p.id = new.play_id and r.evidence_purge_started_at is not null) then
    new.slip_base64 := null;
  end if;
  return new;
end;
$$;
create trigger protect_expired_capper_artwork before insert or update on public.play_card_assets
for each row execute function public.protect_expired_capper_artwork();

create function public.complete_capper_record_refresh(
  p_message_id text, p_revision bigint, p_content text, p_remove_evidence boolean default false
) returns boolean language plpgsql security definer set search_path = '' as $$
declare r public.capper_review_records;
begin
  select * into r from public.capper_review_records where message_id = p_message_id for update;
  if not found or r.revision <> p_revision then return false; end if;
  if p_remove_evidence then
    if r.evidence_purge_started_at is null or r.source_deleted_at is null or r.evidence_eligible_at is null
       or r.evidence_eligible_at > now() - interval '365 days'
       or not public.capper_record_is_resolved(p_message_id) then
      raise exception 'Pending, unresolved, or unexpired review evidence must be preserved.';
    end if;
    update public.capper_ballots
      set review = review - array['slip_base64', 'source_text', 'image_filename']
      where message_id = p_message_id;
  end if;
  update public.capper_review_records set rendered_content = p_content, rendered_revision = p_revision,
    evidence_removed_at = case when p_remove_evidence then coalesce(evidence_removed_at, now()) else evidence_removed_at end
    where message_id = p_message_id;
  return true;
end;
$$;

create function public.capper_review_health()
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'version', 1,
    'pending', (select count(*) from public.capper_ballots where policy_version = 'slip-v1' and state = 'pending'),
    'interrupted', (select count(*) from public.capper_ballots where policy_version = 'slip-v1' and state in ('processing','failed')),
    'record_jobs', (select count(*) from public.capper_review_record_work),
    'uncertain_records', (select count(*) from public.capper_review_records where record_message_id = 'sending')
      + (select count(*) from public.capper_review_record_days where thread_id = 'sending')
      + (select count(*) from public.capper_forum_notices where thread_id = 'sending')
  );
$$;

revoke all on function public.claim_invalid_capper_slip(text,text,text,text),
  public.capper_record_is_resolved(text), public.touch_capper_record(text),
  public.initialize_capper_record(), public.changed_capper_record_play(), public.changed_capper_record_ballot(),
  public.begin_capper_evidence_expiry(text,bigint), public.protect_expired_capper_artwork(),
  public.complete_capper_record_refresh(text,bigint,text,boolean), public.capper_review_health()
from public, anon, authenticated;
grant execute on function public.claim_invalid_capper_slip(text,text,text,text),
  public.begin_capper_evidence_expiry(text,bigint),
  public.complete_capper_record_refresh(text,bigint,text,boolean), public.capper_review_health()
to service_role;

commit;
