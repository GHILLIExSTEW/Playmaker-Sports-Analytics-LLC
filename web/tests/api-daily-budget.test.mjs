import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

async function database() {
  const db = new PGlite()
  await db.exec('create role anon; create role authenticated; create role service_role;')
  await db.exec(await readFile(new URL('../../supabase/migrations/20261003050000_api_request_budget.sql', import.meta.url), 'utf8'))
  await db.exec(await readFile(new URL('../../supabase/migrations/20261011193500_shared_api_daily_budget.sql', import.meta.url), 'utf8'))
  return db
}

async function reserve(db, product, user = null, count = 1) {
  const result = await db.query('select public.reserve_api_requests($1, $2, $3) as result', [product, user, count])
  return result.rows[0].result
}

test('global ceiling includes prior usage, all products, singles and batches', async () => {
  const db = await database()
  try {
    await db.exec(`insert into public.api_request_budget(product, day, system_requests, member_requests)
      values ('basketball', (now() at time zone 'UTC')::date, 7497, 0);`)
    assert.equal(await reserve(db, 'basketball'), 'reserved')
    assert.equal(await reserve(db, 'baseball', '123', 3), 'Shared 7500-request daily allowance exhausted')
    assert.equal(await reserve(db, 'hockey', '123', 2), 'reserved')
    const single = await db.query("select public.reserve_api_request('american-football') as result")
    assert.equal(single.rows[0].result, 'Shared 7500-request daily allowance exhausted')
    const usage = await db.query("select sum(system_requests + member_requests)::integer as total from public.api_request_budget")
    assert.equal(usage.rows[0].total, 7500)
  } finally {
    await db.close()
  }
})

test('member caps and cooldown remain enforced; previous days do not count', async () => {
  const db = await database()
  try {
    await db.exec(`insert into public.api_request_budget(product, day, system_requests)
      values ('basketball', (now() at time zone 'UTC')::date - 1, 7500);`)
    assert.equal(await reserve(db, 'basketball', '123', 5), 'reserved')
    assert.match(await reserve(db, 'basketball', '456'), /five-minute cooldown/)
    assert.match(await reserve(db, 'hockey', '123'), /five-request daily allowance/)
    await db.exec(`update public.api_request_budget set member_requests = 20,
      last_member_request = now() - interval '10 minutes'
      where product = 'basketball' and day = (now() at time zone 'UTC')::date;`)
    assert.equal(await reserve(db, 'basketball', '456'), 'Shared member daily allowance exhausted')
    assert.equal(await reserve(db, 'basketball'), 'reserved')
    await assert.rejects(reserve(db, 'unknown'), /Invalid request batch/)
    await assert.rejects(reserve(db, 'basketball', 'invalid'), /Invalid Discord ID/)
    await assert.rejects(reserve(db, 'basketball', null, 6), /Invalid request batch/)
    const grants = await db.query(`select
      has_function_privilege('anon', 'public.reserve_api_requests(text,text,integer)', 'execute') as anon,
      has_function_privilege('service_role', 'public.reserve_api_request(text,text)', 'execute') as service`)
    assert.equal(grants.rows[0].anon, false)
    assert.equal(grants.rows[0].service, true)
  } finally {
    await db.close()
  }
})
