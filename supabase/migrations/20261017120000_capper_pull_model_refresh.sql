begin;

create table public.capper_model_refreshes (
  request_id uuid primary key references public.capper_team_pulls(id) on delete cascade,
  competition text not null,
  requested_at timestamptz not null default clock_timestamp(),
  state text not null default 'pending' check (state in ('pending','running','completed','failed')),
  started_at timestamptz,
  completed_at timestamptz,
  claim_token uuid,
  projections integer
);
create index capper_model_refreshes_pending on public.capper_model_refreshes(requested_at) where state='pending';
create unique index capper_model_refreshes_running on public.capper_model_refreshes(competition) where state='running';
alter table public.capper_model_refreshes enable row level security;
revoke all on public.capper_model_refreshes from public,anon,authenticated;
grant select on public.capper_model_refreshes to service_role;

alter function public.finish_capper_team_pull(uuid,jsonb,jsonb,boolean) rename to save_capper_team_pull;
revoke all on function public.save_capper_team_pull(uuid,jsonb,jsonb,boolean) from public,anon,authenticated,service_role;

create function public.finish_capper_team_pull(p_request_id uuid,p_games jsonb,p_injuries jsonb,p_failed boolean)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if public.save_capper_team_pull(p_request_id,p_games,p_injuries,p_failed) is distinct from true then return false; end if;
  if not p_failed then
    insert into public.capper_model_refreshes(request_id,competition)
      select id,competition from public.capper_team_pulls where id=p_request_id;
  end if;
  return true;
end;
$$;
revoke all on function public.finish_capper_team_pull(uuid,jsonb,jsonb,boolean) from public,anon,authenticated;
grant execute on function public.finish_capper_team_pull(uuid,jsonb,jsonb,boolean) to service_role;

create function public.claim_capper_model_refresh()
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare job public.capper_model_refreshes;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('capper-model-refreshes',0));
  update public.capper_model_refreshes set state='failed',completed_at=clock_timestamp()
    where (state='pending' and requested_at<clock_timestamp()-interval '3 minutes')
      or (state='running' and started_at<clock_timestamp()-interval '3 minutes');
  select * into job from public.capper_model_refreshes q
    where q.state='pending' and not exists (
      select 1 from public.capper_model_refreshes running where running.competition=q.competition and running.state='running')
    order by q.requested_at limit 1 for update skip locked;
  if not found then return null; end if;
  update public.capper_model_refreshes set state='running',started_at=clock_timestamp(),claim_token=gen_random_uuid()
    where request_id=job.request_id returning * into job;
  return jsonb_build_object('request_id',job.request_id,'competition',job.competition,'claim_token',job.claim_token);
end;
$$;
revoke all on function public.claim_capper_model_refresh() from public,anon,authenticated;
grant execute on function public.claim_capper_model_refresh() to service_role;

create function public.finish_capper_model_refresh(p_request_id uuid,p_claim_token uuid,p_projections integer)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if p_projections<0 then raise exception 'Invalid projection count'; end if;
  update public.capper_model_refreshes
    set state=case when p_projections is null then 'failed' else 'completed' end,
      completed_at=clock_timestamp(),projections=p_projections
    where request_id=p_request_id and state='running' and claim_token=p_claim_token
      and started_at>=clock_timestamp()-interval '3 minutes';
  return found;
end;
$$;
revoke all on function public.finish_capper_model_refresh(uuid,uuid,integer) from public,anon,authenticated;
grant execute on function public.finish_capper_model_refresh(uuid,uuid,integer) to service_role;

create function public.capper_model_refresh_status(p_request_id uuid)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare job public.capper_model_refreshes;
begin
  if public.capper_tools_access() is distinct from true then
    raise exception 'Approved capper access required' using errcode='42501'; end if;
  select q.* into job from public.capper_model_refreshes q join public.capper_team_pulls p on p.id=q.request_id
    where q.request_id=p_request_id and p.user_id=auth.uid();
  if not found then raise exception 'Model refresh not found' using errcode='22023'; end if;
  if (job.state='pending' and job.requested_at<clock_timestamp()-interval '3 minutes')
    or (job.state='running' and job.started_at<clock_timestamp()-interval '3 minutes') then
    job.state := 'failed';
  end if;
  return jsonb_build_object('state',job.state,'completed_at',job.completed_at,'projections',job.projections);
end;
$$;
revoke all on function public.capper_model_refresh_status(uuid) from public,anon;
grant execute on function public.capper_model_refresh_status(uuid) to authenticated;

commit;
