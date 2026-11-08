import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

async function database() {
  const db = new PGlite()
  await db.exec('create role anon; create role authenticated; create role service_role;')
  await db.exec(await readFile(new URL('../../supabase/migrations/20261108181000_openai_vision_budget.sql', import.meta.url), 'utf8'))
  return db
}

const reserve = async (db, limit) =>
  (await db.query('select public.reserve_openai_vision_request($1) as ok', [limit])).rows[0].ok

test('vision reservations stop exactly at the daily limit and never overcount', async () => {
  const db = await database()
  try {
    assert.equal(await reserve(db, 3), true)
    assert.equal(await reserve(db, 3), true)
    assert.equal(await reserve(db, 3), true)
    assert.equal(await reserve(db, 3), false)
    assert.equal(await reserve(db, 3), false)
    const used = await db.query('select requests from public.openai_vision_budget')
    assert.deepEqual(used.rows, [{ requests: 3 }])
    // A raised limit allows more calls the same day; yesterday's usage does not count.
    assert.equal(await reserve(db, 4), true)
    await db.exec("update public.openai_vision_budget set day = day - 1")
    assert.equal(await reserve(db, 1), true)
    await assert.rejects(reserve(db, 0), /Invalid vision daily limit/)
    await assert.rejects(reserve(db, null), /Invalid vision daily limit/)
  } finally { await db.close() }
})

test('only the service role can reserve or read the vision budget', async () => {
  const db = await database()
  try {
    for (const role of ['anon', 'authenticated']) {
      const access = await db.query(`select
        has_function_privilege('${role}', 'public.reserve_openai_vision_request(integer)', 'execute') as reserve,
        has_table_privilege('${role}', 'public.openai_vision_budget', 'select') as read`)
      assert.deepEqual(access.rows[0], { reserve: false, read: false })
    }
    const service = await db.query(`select
      has_function_privilege('service_role', 'public.reserve_openai_vision_request(integer)', 'execute') as reserve`)
    assert.equal(service.rows[0].reserve, true)
  } finally { await db.close() }
})
