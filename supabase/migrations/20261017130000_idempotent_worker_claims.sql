-- Worker claims/completions accept a worker-generated token so a request whose
-- connection dropped can be replayed safely: a replay returns the same job or
-- result instead of claiming a second job or reporting a false failure.
-- The previous no-argument claim functions remain for bots not yet restarted.
begin;

create function public.claim_capper_model_refresh(p_claim_token uuid)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare job public.capper_model_refreshes;
begin
  if p_claim_token is null then raise exception 'Claim token required'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('capper-model-refreshes',0));
  update public.capper_model_refreshes set state='failed',completed_at=clock_timestamp()
    where (state='pending' and requested_at<clock_timestamp()-interval '3 minutes')
      or (state='running' and started_at<clock_timestamp()-interval '3 minutes');
  select * into job from public.capper_model_refreshes where claim_token=p_claim_token and state='running';
  if not found then
    select * into job from public.capper_model_refreshes q
      where q.state='pending' and not exists (
        select 1 from public.capper_model_refreshes running where running.competition=q.competition and running.state='running')
      order by q.requested_at limit 1 for update skip locked;
    if not found then return null; end if;
    update public.capper_model_refreshes set state='running',started_at=clock_timestamp(),claim_token=p_claim_token
      where request_id=job.request_id returning * into job;
  end if;
  return jsonb_build_object('request_id',job.request_id,'competition',job.competition,'claim_token',job.claim_token);
end;
$$;
revoke all on function public.claim_capper_model_refresh(uuid) from public,anon,authenticated;
grant execute on function public.claim_capper_model_refresh(uuid) to service_role;

create or replace function public.finish_capper_model_refresh(p_request_id uuid,p_claim_token uuid,p_projections integer)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if p_projections<0 then raise exception 'Invalid projection count'; end if;
  update public.capper_model_refreshes
    set state=case when p_projections is null then 'failed' else 'completed' end,
      completed_at=clock_timestamp(),projections=p_projections
    where request_id=p_request_id and state='running' and claim_token=p_claim_token
      and started_at>=clock_timestamp()-interval '3 minutes';
  if found then return true; end if;
  -- A replay of a committed completion reports the result already saved.
  return exists (select 1 from public.capper_model_refreshes
    where request_id=p_request_id and claim_token=p_claim_token
      and state=case when p_projections is null then 'failed' else 'completed' end
      and projections is not distinct from p_projections);
end;
$$;

alter table public.research_refresh_requests add column claim_token uuid;
create unique index research_refresh_claim_token on public.research_refresh_requests(claim_token) where claim_token is not null;

create function public.claim_research_refresh(p_claim_token uuid)
returns setof public.research_refresh_requests language plpgsql security definer set search_path = ''
as $$
begin
  if p_claim_token is null then raise exception 'Claim token required'; end if;
  -- Never retry an interrupted provider request whose outcome is unknown.
  update public.research_refresh_requests set state='failed', updated_at=now(),
    result='Worker interrupted or timed out. Inspect cached data before requesting again.'
  where state='running' and updated_at < now() - interval '10 minutes';
  update public.research_refresh_requests set state='failed', updated_at=now(),
    result='Request expired before a worker claimed it. No provider call was made.'
  where state='queued' and requested_at < now() - interval '15 minutes';
  if exists (select 1 from public.research_refresh_requests where claim_token=p_claim_token and state='running') then
    return query select * from public.research_refresh_requests where claim_token=p_claim_token;
    return;
  end if;
  return query
  update public.research_refresh_requests r set state='running', updated_at=now(), claim_token=p_claim_token
  where id = (select id from public.research_refresh_requests where state='queued'
    order by requested_at for update skip locked limit 1)
  returning r.*;
end;
$$;
revoke all on function public.claim_research_refresh(uuid) from public, anon, authenticated;
grant execute on function public.claim_research_refresh(uuid) to service_role;

create function public.finish_research_refresh(p_id uuid, p_claim_token uuid, p_state text, p_result text)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if p_state not in ('completed','failed') then raise exception 'Invalid research refresh state'; end if;
  update public.research_refresh_requests set state=p_state, result=p_result, updated_at=now()
    where id=p_id and claim_token=p_claim_token and state='running';
  if found then return true; end if;
  return exists (select 1 from public.research_refresh_requests
    where id=p_id and claim_token=p_claim_token and state=p_state and result is not distinct from p_result);
end;
$$;
revoke all on function public.finish_research_refresh(uuid,uuid,text,text) from public, anon, authenticated;
grant execute on function public.finish_research_refresh(uuid,uuid,text,text) to service_role;

commit;
