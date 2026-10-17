import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

const owner = '00000000-0000-0000-0000-000000000001'
const capper = '00000000-0000-0000-0000-000000000002'
const other = '00000000-0000-0000-0000-000000000003'
const missing = '00000000-0000-0000-0000-000000000099'
const hash = (token) => createHash('sha256').update(token).digest('hex')

async function database(t) {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon;
    create role authenticated;
    create role service_role bypassrls;
    create schema auth;
    create table auth.users(id uuid primary key);
    create table auth.identities(user_id uuid references auth.users, provider text, provider_id text);
    create table public.member_profiles(user_id uuid references auth.users, age_verified_at timestamptz);
    create table public.test_roster(discord_id text primary key, is_owner boolean, verified_at timestamptz);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
    $$;
    create function public.website_capper_ids() returns text[] language sql stable as $$
      select coalesce(array_agg(discord_id), '{}') from public.test_roster
      where verified_at > now() - interval '15 minutes' and verified_at <= now();
    $$;
    create function public.website_can_edit_all_cappers() returns boolean language sql stable as $$
      select exists (select 1 from public.test_roster r join auth.identities i
        on i.provider_id = r.discord_id and i.provider = 'discord'
        where i.user_id = auth.uid() and r.is_owner
          and r.verified_at > now() - interval '15 minutes' and r.verified_at <= now());
    $$;
    insert into auth.users values ('${owner}'), ('${capper}'), ('${other}');
    insert into auth.identities values
      ('${owner}', 'discord', '111'), ('${capper}', 'discord', '222'), ('${other}', 'discord', '333');
    insert into public.member_profiles values ('${owner}', now()), ('${capper}', now()), ('${other}', now());
    insert into public.test_roster values ('111', true, now()), ('222', false, now()), ('444', false, now());
  `)
  for (const file of [
    '20261003050000_api_request_budget.sql',
    '20261011193500_shared_api_daily_budget.sql',
    '20261017060000_capper_api_credentials.sql',
  ]) {
    await db.exec(await readFile(new URL(`../../supabase/migrations/${file}`, import.meta.url), 'utf8'))
  }
  return db
}

async function asRole(db, role, user, callback) {
  await db.exec(`set role ${role}`)
  await db.query("select set_config('request.jwt.claim.sub', $1, false)", [user ?? ''])
  try {
    return await callback()
  } finally {
    await db.exec('reset role')
  }
}

async function rpc(db, name, args = []) {
  const params = args.map((_, index) => `$${index + 1}`).join(', ')
  return (await db.query(`select public.${name}(${params}) as result`, args)).rows[0].result
}

const issue = (db, id = '222', label = 'External research') =>
  asRole(db, 'authenticated', owner, () => rpc(db, 'issue_capper_api_key', [id, label]))
const reserve = (db, credential, product = 'basketball', path = '/games') =>
  asRole(db, 'service_role', null, () => rpc(db, 'reserve_capper_api_call', [credential, product, path]))
const finish = (db, id, status = 200) =>
  asRole(db, 'service_role', null, () => rpc(db, 'finish_capper_api_call', [id, status]))
const list = (db) => asRole(db, 'authenticated', owner, () => rpc(db, 'list_capper_api_keys'))

test('only age-verified current owners manage keys; recipient needs linked verified capper identity', async (t) => {
  const db = await database(t)
  assert.deepEqual(await list(db), [])
  for (const user of [null, capper, other]) {
    await asRole(db, 'authenticated', user, async () => {
      await assert.rejects(rpc(db, 'list_capper_api_keys'), /Owner role required/)
      await assert.rejects(rpc(db, 'issue_capper_api_key', ['222', 'key']), /Owner role required/)
      await assert.rejects(rpc(db, 'revoke_capper_api_key', [missing]), /Owner role required/)
    })
  }
  await db.exec(`update public.test_roster set verified_at = now() - interval '16 minutes' where discord_id = '111'`)
  await assert.rejects(issue(db), /Owner role required/)
  await assert.rejects(list(db), /Owner role required/)
  await asRole(db, 'authenticated', owner, () =>
    assert.rejects(rpc(db, 'revoke_capper_api_key', [missing]), /Owner role required/))
  await db.exec(`update public.test_roster set verified_at = now() where discord_id = '111';
    update public.member_profiles set age_verified_at = null where user_id = '${owner}'`)
  await assert.rejects(issue(db), /Owner role required/)
  await assert.rejects(list(db), /Owner role required/)
  await db.exec(`update public.member_profiles set age_verified_at = now() where user_id = '${owner}'`)
  for (const id of ['333', '444', 'invalid', null]) {
    await assert.rejects(issue(db, id), /current capper.*linked Discord Auth identity.*age-verified/)
  }
  await db.exec(`update public.member_profiles set age_verified_at = null where user_id = '${capper}'`)
  await assert.rejects(issue(db), /age-verified member profile/)
  await db.exec(`update public.member_profiles set age_verified_at = now() where user_id = '${capper}';
    update auth.identities set provider = 'email' where user_id = '${capper}'`)
  await assert.rejects(issue(db), /linked Discord Auth identity/)
  await db.exec(`update auth.identities set provider = 'discord' where user_id = '${capper}'`)
  for (const label of ['', '   ', null, 'x'.repeat(101)]) {
    await assert.rejects(issue(db, '222', label), /Label must contain/)
  }
  assert.match((await issue(db)).token, /^pmcap_[0-9a-f]{64}$/)
})

test('raw tokens are returned once, hashes and audits are service-only, revocation is persistent and idempotent', async (t) => {
  const db = await database(t)
  const key = await issue(db, '222', '  Research  ')
  const second = await issue(db)
  assert.deepEqual(Object.keys(key).sort(), ['id', 'token'])
  assert.notEqual(key.token, second.token)
  const stored = (await db.query('select * from public.capper_api_keys where id = $1', [key.id])).rows[0]
  assert.equal(stored.key_hash, hash(key.token))
  assert.equal(stored.created_by, owner)
  assert.equal(stored.label, 'Research')
  assert.ok(!JSON.stringify(stored).includes(key.token))
  const rows = await list(db)
  const entry = rows.find((row) => row.id === key.id)
  assert.deepEqual(Object.keys(entry).sort(), [
    'id', 'discord_user_id', 'label', 'created_at', 'revoked_at', 'last_used_at', 'requests_today',
  ].sort())
  assert.equal(entry.requests_today, 0)
  assert.ok(!JSON.stringify(rows).includes(stored.key_hash))
  assert.ok(!JSON.stringify(rows).includes(key.token))
  for (const role of ['anon', 'authenticated']) {
    await asRole(db, role, owner, async () => {
      for (const table of ['capper_api_keys', 'capper_api_requests']) {
        await assert.rejects(db.query(`select * from public.${table}`), /permission denied/)
        await assert.rejects(db.query(`delete from public.${table}`), /permission denied/)
        await assert.rejects(db.query(`update public.${table} set id = id`), /permission denied/)
      }
      await assert.rejects(rpc(db, 'reserve_capper_api_call', [stored.key_hash, 'basketball', '/games']), /permission denied/)
      await assert.rejects(rpc(db, 'finish_capper_api_call', [missing, 200]), /permission denied/)
      if (role === 'anon') {
        await assert.rejects(rpc(db, 'list_capper_api_keys'), /permission denied/)
        await assert.rejects(rpc(db, 'issue_capper_api_key', ['222', 'key']), /permission denied/)
        await assert.rejects(rpc(db, 'revoke_capper_api_key', [key.id]), /permission denied/)
      }
    })
  }
  const call = await reserve(db, stored.key_hash)
  assert.equal(call.state, 'reserved')
  await asRole(db, 'authenticated', owner, async () => {
    assert.equal(await rpc(db, 'revoke_capper_api_key', [key.id]), true)
    const before = (await rpc(db, 'list_capper_api_keys')).find((row) => row.id === key.id).revoked_at
    assert.equal(await rpc(db, 'revoke_capper_api_key', [key.id]), true)
    assert.equal((await rpc(db, 'list_capper_api_keys')).find((row) => row.id === key.id).revoked_at, before)
    assert.equal(await rpc(db, 'revoke_capper_api_key', [missing]), false)
  })
  assert.equal((await reserve(db, stored.key_hash)).state, 'unauthorized')
  assert.equal(await finish(db, call.request_id, 503), true)
  assert.equal(await finish(db, call.request_id, 200), false)
  assert.equal(await finish(db, missing), false)
  await assert.rejects(finish(db, call.request_id, 600), /Invalid HTTP/)
  await assert.rejects(finish(db, call.request_id, null), /Invalid HTTP/)
  const audit = (await db.query('select * from public.capper_api_requests')).rows[0]
  assert.equal(audit.status, 503)
  assert.ok(audit.completed_at)
  assert.equal(audit.key_id, key.id)
  assert.ok(!JSON.stringify(audit).includes(key.token))
  assert.ok(!JSON.stringify(audit).includes(stored.key_hash))
  await db.exec(`delete from auth.identities where user_id = '${owner}';
    delete from public.member_profiles where user_id = '${owner}';
    delete from auth.users where id = '${owner}'`)
  assert.equal((await db.query('select created_by from public.capper_api_keys where id = $1', [key.id])).rows[0].created_by, null)
  assert.equal((await db.query('select count(*)::int as n from public.capper_api_requests')).rows[0].n, 1)
})

test('credential validation fails closed for revoked, removed, stale and unverified recipients', async (t) => {
  const db = await database(t)
  const key = await issue(db)
  const digest = hash(key.token)
  for (const invalid of [null, '', key.token, 'a'.repeat(64), digest.toUpperCase()]) {
    assert.equal((await reserve(db, invalid)).state, 'unauthorized')
  }
  for (const change of [
    `delete from public.test_roster where discord_id = '222'`,
    `update public.test_roster set verified_at = now() - interval '16 minutes' where discord_id = '222'`,
    `update public.member_profiles set age_verified_at = null where user_id = '${capper}'`,
    `delete from auth.identities where user_id = '${capper}'`,
  ]) {
    await db.exec('begin')
    await db.exec(change)
    assert.equal((await reserve(db, digest)).state, 'unauthorized')
    await db.exec('rollback')
  }
  assert.equal((await db.query('select count(*)::int as n from public.api_request_budget')).rows[0].n, 0)
  assert.equal((await list(db))[0].last_used_at, null)
  assert.equal((await reserve(db, digest)).state, 'reserved')
})

test('invalid products and unsafe paths never consume budget or log request parameters', async (t) => {
  const db = await database(t)
  const digest = hash((await issue(db)).token)
  for (const product of [null, '', 'nfl', 'Basketball', 'unknown']) {
    assert.deepEqual(await reserve(db, digest, product), { state: 'denied', reason: 'Invalid API product' })
  }
  for (const path of [
    null, '', 'games', '//games', '/games/', '/Games', '/games//live', '/games/../status',
    '/games?token=secret', '/games#secret', '/games%2fstatus', '/games\\status',
    'https://example.com/games', '/status', '/games/status', '/status/games',
    '/games/a_b', '/games/á', '/games\n', `/${'a'.repeat(513)}`,
  ]) {
    assert.deepEqual(await reserve(db, digest, 'basketball', path), { state: 'denied', reason: 'Invalid API data path' })
  }
  assert.equal((await db.query('select count(*)::int as n from public.api_request_budget')).rows[0].n, 0)
  assert.equal((await db.query('select count(*)::int as n from public.capper_api_requests')).rows[0].n, 0)
  for (const product of [
    'american-football', 'basketball', 'football', 'hockey', 'baseball',
    'rugby', 'handball', 'volleyball', 'mma', 'formula-1',
  ]) {
    assert.equal((await reserve(db, digest, product, '/games/head-to-head')).state, 'reserved')
  }
})

test('rapid capper calls bypass member caps, isolate key audits and share the existing 7500 UTC budget', async (t) => {
  const db = await database(t)
  const first = await issue(db)
  const second = await issue(db)
  await db.exec(`insert into public.api_request_budget values
    ('basketball', (now() at time zone 'UTC')::date, 0, 20, now()),
    ('hockey', (now() at time zone 'UTC')::date - 1, 7500, 0, null);
    insert into public.api_member_request_usage values ('222', (now() at time zone 'UTC')::date, 5)`)
  for (let i = 0; i < 25; i++) {
    const result = await reserve(db, hash(first.token))
    assert.equal(result.state, 'reserved')
    assert.match(result.request_id, /^[0-9a-f-]{36}$/)
  }
  const secondCall = await reserve(db, hash(second.token), 'baseball')
  assert.equal(secondCall.state, 'reserved')
  assert.equal(await finish(db, secondCall.request_id, 429), true)
  const rows = await list(db)
  assert.equal(rows.find((row) => row.id === first.id).requests_today, 25)
  assert.equal(rows.find((row) => row.id === second.id).requests_today, 1)
  assert.ok(rows.every((row) => row.last_used_at !== null))
  assert.equal((await db.query('select requests from public.api_member_request_usage')).rows[0].requests, 5)
  const budget = (await db.query("select * from public.api_request_budget where product = 'basketball'")).rows[0]
  assert.equal(budget.system_requests, 25)
  assert.equal(budget.member_requests, 20)
  await db.exec(`insert into public.api_request_budget(product, day, system_requests)
    values ('football', (now() at time zone 'UTC')::date, 7452)`)
  // 25 capper + 20 member + 1 failed provider + 7452 system = 7498.
  assert.equal(await asRole(db, 'service_role', null, () => rpc(db, 'reserve_api_request', ['mma', null])), 'reserved')
  assert.equal((await reserve(db, hash(second.token), 'hockey')).state, 'reserved')
  assert.deepEqual(await reserve(db, hash(first.token), 'volleyball'), {
    state: 'denied', reason: 'Shared 7500-request daily allowance exhausted',
  })
  assert.equal(await asRole(db, 'service_role', null, () =>
    rpc(db, 'reserve_api_requests', ['rugby', '333', 1])), 'Shared 7500-request daily allowance exhausted')
  assert.equal((await db.query(`select sum(system_requests + member_requests)::int as n
    from public.api_request_budget where day = (now() at time zone 'UTC')::date`)).rows[0].n, 7500)
  assert.equal((await list(db)).find((row) => row.id === first.id).requests_today, 25)
  // PGlite serializes execution; verify delegation and lock order rather than claim parallel-session coverage.
  const source = (await db.query(`select pg_get_functiondef(
    'public.reserve_capper_api_call(text,text,text)'::regprocedure) as body`)).rows[0].body
  assert.ok(source.indexOf('for update') < source.indexOf('public.reserve_api_requests(p_product, null, 1)'))
  const shared = (await db.query(`select pg_get_functiondef(
    'public.reserve_api_requests(text,text,integer)'::regprocedure) as body`)).rows[0].body
  assert.ok(shared.includes("hashtextextended('api-global-daily-budget', 0)"))
})
