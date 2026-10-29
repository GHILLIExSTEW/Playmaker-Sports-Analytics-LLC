import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('grandfather capture is atomic, finite, service-only and never grants owner privileges', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth;
    create function auth.uid() returns uuid language sql stable as $$
      select '11111111-1111-1111-1111-111111111111'::uuid
    $$;
    create table auth.identities(user_id uuid, provider text, provider_id text);
    insert into auth.identities values
      ('11111111-1111-1111-1111-111111111111','discord','123');
    create table public.website_membership_config(singleton boolean, account_id text);
    insert into public.website_membership_config values (true,'biz_test');
    create function public.discord_has_paid_access(text) returns boolean language sql as $$ select $1 = '999' $$;
    create function public.discord_has_paid_plan_access(text,text,text[]) returns boolean language sql as $$ select $1 = '999' $$;
    create function public.paid_discord_member_ids() returns table(discord_user_id text)
      language sql as $$ select '999'::text $$;
    create function public.website_member_access() returns jsonb language sql as $$
      select coalesce(nullif(current_setting('test.access',true),''),'{"state":"membership-required"}')::jsonb
    $$;
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261029010000_highroller_grandfather.sql', import.meta.url), 'utf8'))
  const capture = async (ids) => (await db.query(
    "select public.capture_highroller_grandfather('1','77','biz_test',$1::text[]) as saved", [ids],
  )).rows[0].saved
  await db.exec('set role service_role')
  assert.equal(await capture(['123', '456', '123']), true)
  assert.equal(await capture(['789']), false)
  await db.exec('reset role')
  assert.equal((await db.query('select count(*)::int as n from public.highroller_grandfather_members')).rows[0].n, 2)
  assert.equal((await db.query("select public.discord_has_paid_access('123') as ok")).rows[0].ok, true)
  assert.equal((await db.query("select public.discord_has_paid_access('789') as ok")).rows[0].ok, false)
  assert.equal((await db.query("select public.discord_has_paid_access('999') as ok")).rows[0].ok, true)
  assert.deepEqual((await db.query('select * from public.paid_discord_member_ids() order by 1')).rows.map(r => r.discord_user_id), ['123', '456', '999'])
  for (const [account, plans, expected] of [['biz_test', ['plan'], true], ['wrong', ['plan'], false], ['biz_test', [], false]]) {
    assert.equal((await db.query('select public.discord_has_paid_plan_access($1,$2,$3) as ok', ['123', account, plans])).rows[0].ok, expected)
  }
  const access = async () => (await db.query('select public.website_member_access() as access')).rows[0].access
  const grandfathered = await access()
  assert.equal(grandfathered.state, 'active')
  assert.equal(grandfathered.kind, 'grandfather')
  assert.equal(new Date(grandfathered.expires_at).toISOString(), '2027-02-01T05:00:00.000Z')
  for (const state of ['signed-out', 'age-required', 'discord-required', 'launch-pending']) {
    await db.query("select set_config('test.access',$1,false)", [JSON.stringify({ state })])
    assert.deepEqual(await access(), { state })
  }
  for (const kind of ['paid', 'trial', 'owner', 'operator', 'research']) {
    const original = { state: 'active', kind, expires_at: null }
    await db.query("select set_config('test.access',$1,false)", [JSON.stringify(original)])
    assert.deepEqual(await access(), original)
  }
  await db.exec(`select set_config('test.access','{"state":"membership-required"}',false);
    update public.highroller_grandfather_members set expires_at=now() where discord_user_id='123'`)
  assert.equal((await db.query("select public.discord_has_paid_access('123') as ok")).rows[0].ok, false)
  assert.equal((await access()).state, 'membership-required')
  await db.exec("delete from public.highroller_grandfather_members where discord_user_id='456'")
  assert.equal(await capture(['456']), false)
  assert.equal((await db.query("select public.discord_has_paid_access('456') as ok")).rows[0].ok, false)
  await assert.rejects(capture(['invalid']), /Invalid HIGHROLLER/)
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`)
    await assert.rejects(capture(['789']), /permission denied/)
    await assert.rejects(db.query('select * from public.highroller_grandfather_members'), /permission denied/)
    await assert.rejects(db.query('select public.website_member_access_before_grandfather()'), /permission denied/)
    await db.exec('reset role')
  }
})
