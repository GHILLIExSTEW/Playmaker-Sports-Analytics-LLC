import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

const owner = '00000000-0000-0000-0000-000000000001'
const manager = '00000000-0000-0000-0000-000000000002'
const requester = '00000000-0000-0000-0000-000000000003'
const stranger = '00000000-0000-0000-0000-000000000004'

async function setup(t) {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon;
    create role authenticated;
    create role service_role;
    create schema auth;
    create table auth.users(id uuid primary key);
    create table auth.identities(user_id uuid, provider text, provider_id text, identity_data jsonb);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('test.uid', true), '')::uuid
    $$;
    create function public.website_can_edit_all_cappers()
    returns boolean language sql stable as $$
      select coalesce(current_setting('test.owner', true), 'false') = 'true'
    $$;
    insert into auth.users values
      ('${owner}'), ('${manager}'), ('${requester}'), ('${stranger}');
    insert into auth.identities values
      ('${owner}', 'discord', '1001', '{"username":"owner"}'),
      ('${manager}', 'discord', '1002', '{"username":"manager"}'),
      ('${requester}', 'discord', '1003', '{"username":"N45TY"}'),
      ('${stranger}', 'discord', '1004', '{"username":"stranger"}');
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261124052300_merchandise_catalog_sales.sql', import.meta.url), 'utf8'))
  return db
}

async function identity(db, userId, isOwner = false) {
  await db.query("select set_config('test.uid', $1, false)", [userId])
  await db.query("select set_config('test.owner', $1, false)", [String(isOwner)])
}

async function rpc(db, name, args = []) {
  const placeholders = args.map((_, index) => `$${index + 1}`).join(',')
  return (await db.query(`select public.${name}(${placeholders}) as data`, args)).rows[0].data
}

async function rows(db, name) {
  return (await db.query(`select * from public.${name}()`)).rows
}

test('Discord requests require Owner approval, and only approved managers can manage products and sales', async (t) => {
  const db = await setup(t)
  await identity(db, requester)
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'none')
  assert.equal(await rpc(db, 'request_merchandise_manager_access'), 'pending')
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'pending')
  await assert.rejects(rpc(db, 'merchandise_access_requests'), /Verified Owner/)
  await assert.rejects(
    rpc(db, 'save_merchandise_product', [null, 'Cap', '', 20, 2, null, null, true]),
    /Approved merchandise manager/,
  )

  await identity(db, owner, true)
  const requests = await rows(db, 'merchandise_access_requests')
  assert.equal(requests.length, 1)
  assert.equal(requests[0].discord_username, 'N45TY')
  assert.equal(requests[0].discord_user_id, '1003')
  assert.equal(await rpc(db, 'review_merchandise_access_request', [requester, true]), true)
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'owner')
  assert.equal((await rows(db, 'merchandise_manager_roster')).length, 1)

  await identity(db, requester)
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'manager')
  const productId = await rpc(db, 'save_merchandise_product', [
    null, 'Embroidered cap', 'One size', 24.5, 3, 'https://shop.example/cap.webp', 'https://shop.example/cap', true,
  ])
  assert.ok(productId)
  const catalog = await rows(db, 'public_merchandise_catalog')
  assert.equal(catalog.length, 1)
  assert.deepEqual(catalog[0], {
    id: productId, name: 'Embroidered cap', description: 'One size', price: '24.50',
    in_stock: true, image_url: 'https://shop.example/cap.webp', order_url: 'https://shop.example/cap',
  })

  await db.exec('set role anon')
  const anonymousCatalog = await rows(db, 'public_merchandise_catalog')
  assert.equal(anonymousCatalog.length, 1)
  await assert.rejects(db.query('select * from public.merchandise_sales'), /permission denied/)
  await db.exec('reset role')

  const saleId = await rpc(db, 'record_merchandise_sale', [productId, 2, '2026-11-23', 22])
  assert.ok(saleId)
  const [sale] = await rows(db, 'merchandise_sales_report')
  assert.equal(sale.quantity, 2)
  assert.equal(sale.unit_price, '22.00')
  assert.equal(sale.total, '44.00')
  assert.equal(sale.entered_by, requester)
  assert.equal((await rows(db, 'merchandise_products_for_manager'))[0].stock_quantity, 1)
  await assert.rejects(
    rpc(db, 'record_merchandise_sale', [productId, 2, '2026-11-23', null]),
    /exceeds available stock/,
  )

  await identity(db, owner, true)
  assert.equal(await rpc(db, 'revoke_merchandise_manager', [requester]), true)
  await identity(db, requester)
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'none')
  await assert.rejects(rpc(db, 'merchandise_sales_report'), /Approved merchandise manager/)
})

test('declined access can be requested again, while invalid products and future sales are rejected', async (t) => {
  const db = await setup(t)
  await identity(db, stranger)
  assert.equal(await rpc(db, 'request_merchandise_manager_access'), 'pending')
  await identity(db, owner, true)
  assert.equal(await rpc(db, 'review_merchandise_access_request', [stranger, false]), true)
  await identity(db, stranger)
  assert.equal(await rpc(db, 'merchandise_manager_status'), 'none')
  assert.equal(await rpc(db, 'request_merchandise_manager_access'), 'pending')

  await identity(db, owner, true)
  await assert.rejects(
    rpc(db, 'save_merchandise_product', [null, '   ', '', 20, 2, null, null, true]),
    /Enter a product name/,
  )
  await assert.rejects(
    rpc(db, 'save_merchandise_product', [null, 'Cap', '', 20, 2, 'http://shop.example/cap.webp', null, true]),
    /HTTPS/,
  )
  const productId = await rpc(db, 'save_merchandise_product', [null, 'Cap', '', 20, 2, null, null, true])
  await assert.rejects(
    rpc(db, 'record_merchandise_sale', [productId, 1, '2999-01-01', null]),
    /valid sale date/,
  )
})
