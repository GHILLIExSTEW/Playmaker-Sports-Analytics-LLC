import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('approved official-play cleanup previews safely and preserves all other records', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(await readFile(new URL('../../src/database/schema.sql', import.meta.url), 'utf8'))
  const script = await readFile(new URL('../../supabase/maintenance/delete_non_capper_official_plays.sql', import.meta.url), 'utf8')
  await db.exec(`
    insert into public.sports(id,api_slug,name) values(1,'nfl','NFL');
    insert into public.users(id,discord_user_id) values
      (11,'1211806245229695138'),(12,'759419251773014056'),(13,'333');
    insert into public.plays(id,user_id,sport_id,units,legs,odds,message_id)
      values(1,11,1,1,1,100,'101'),(2,12,1,1,1,100,'102'),(3,13,1,1,1,100,'103');
    insert into public.play_legs(play_id,leg_number,selection,odds)
      select id,1,'Selection',100 from public.plays;
    insert into public.play_tails(play_id,discord_user_id) select id,'999' from public.plays;
    insert into public.play_versions(play_id,version_number) select id,1 from public.plays;
    insert into public.settlements(play_id,result) select id,'win' from public.plays;
  `)
  await db.exec(script)
  assert.equal((await db.query('select count(*)::integer as count from public.plays')).rows[0].count, 3)
  await db.exec("update public.users set discord_user_id='wrong' where id=11")
  await assert.rejects(db.exec(script.replace('apply_delete boolean := false', 'apply_delete boolean := true')), /Both approved user ID/)
  assert.equal((await db.query('select count(*)::integer as count from public.plays')).rows[0].count, 3)
  await db.exec("update public.users set discord_user_id='1211806245229695138' where id=11")
  await db.exec(script.replace('apply_delete boolean := false', 'apply_delete boolean := true'))
  assert.deepEqual((await db.query('select id from public.plays')).rows, [{ id: 3 }])
  for (const table of ['play_legs', 'play_tails', 'play_versions', 'settlements']) {
    assert.deepEqual((await db.query(`select play_id from public.${table}`)).rows, [{ play_id: 3 }])
  }
  assert.equal((await db.query('select count(*)::integer as count from public.users')).rows[0].count, 3)
  await db.exec(script.replace('apply_delete boolean := false', 'apply_delete boolean := true'))
  assert.deepEqual((await db.query('select id from public.plays')).rows, [{ id: 3 }])
})
