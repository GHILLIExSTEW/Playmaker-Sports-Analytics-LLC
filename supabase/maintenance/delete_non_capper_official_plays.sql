-- Run as database owner. Preview first, confirm a backup, then set apply_delete
-- to true and rerun this entire statement. Accounts/memberships/vault stay intact.
do $$
declare
  apply_delete boolean := false;
  target_ids bigint[];
  target_play_ids bigint[];
  play_count bigint;
  deleted_count bigint;
begin
  select array_agg(id order by id) into target_ids
  from public.users
  where (id = 11 and discord_user_id = '1211806245229695138')
     or (id = 12 and discord_user_id = '759419251773014056');
  if coalesce(cardinality(target_ids), 0) <> 2 then
    raise exception 'Both approved user ID/Discord ID pairs must match. Nothing deleted.';
  end if;

  -- Block new writes until the preview/deletion completes.
  lock table public.plays in share row exclusive mode;
  select array_agg(id order by id), count(*) into target_play_ids, play_count
  from public.plays where user_id = any(target_ids);
  target_play_ids := coalesce(target_play_ids, array[]::bigint[]);

  raise notice 'Target user IDs: %, official play IDs: %, plays: %',
    target_ids, target_play_ids, play_count;
  raise notice 'Related rows: legs %, tails %, settlements %, play history %',
    (select count(*) from public.play_legs where play_id = any(target_play_ids)),
    (select count(*) from public.play_tails where play_id = any(target_play_ids)),
    (select count(*) from public.settlements where play_id = any(target_play_ids)),
    (select count(*) from public.play_versions where play_id = any(target_play_ids));
  if not apply_delete then
    raise notice 'PREVIEW ONLY: no rows deleted. Confirm backup and review IDs before setting apply_delete=true.';
    return;
  end if;

  delete from public.play_tails where play_id = any(target_play_ids);
  delete from public.play_legs where play_id = any(target_play_ids);
  delete from public.settlements where play_id = any(target_play_ids);
  delete from public.play_versions where play_id = any(target_play_ids);
  delete from public.plays where id = any(target_play_ids) and user_id = any(target_ids);
  get diagnostics deleted_count = row_count;
  if deleted_count <> play_count then
    raise exception 'Deleted play count mismatch; transaction rolled back.';
  end if;
  raise notice 'Deleted % official plays and their related records. User accounts, memberships, vault records, and unpublished draft legs were not deleted.',
    deleted_count;
end;
$$;
