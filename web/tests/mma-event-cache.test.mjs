import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('MMA cache repair restores fighters without changing sync timestamps or other sports', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`create table public.api_sports_events (
    sport_slug text, event_name text, home_id text, home_name text, home_logo text,
    away_id text, away_name text, away_logo text, league_name text, round_name text,
    raw_event jsonb, synced_at timestamptz
  )`)
  const raw = { slug: 'UFC 332', category: 'Middleweight', fighters: {
    first: { id: 241, name: 'Ismail Naurdiev', logo: 'first.png' },
    second: { id: 503, name: 'Marvin Vettori', logo: 'second.png' },
  } }
  for (const sport of ['mma', 'basketball']) {
    await db.query(`insert into public.api_sports_events(sport_slug,event_name,raw_event,synced_at)
      values ($1,'Home vs Away',$2,'2026-10-10T14:00:00Z')`, [sport, JSON.stringify(raw)])
  }
  const migration = await readFile(new URL('../../supabase/migrations/20261010153000_repair_mma_fighter_names.sql', import.meta.url), 'utf8')
  await db.exec(migration)
  await db.exec(migration)
  const rows = (await db.query(`select sport_slug,event_name,home_name,away_name,home_id,away_id,
    home_logo,away_logo,league_name,round_name,synced_at from public.api_sports_events order by sport_slug`)).rows
  assert.equal(rows[0].event_name, 'Home vs Away')
  assert.deepEqual(rows[1], {
    sport_slug: 'mma', event_name: 'Ismail Naurdiev vs Marvin Vettori', home_name: 'Ismail Naurdiev',
    away_name: 'Marvin Vettori', home_id: '241', away_id: '503', home_logo: 'first.png',
    away_logo: 'second.png', league_name: 'UFC 332', round_name: 'Middleweight',
    synced_at: new Date('2026-10-10T14:00:00Z'),
  })
})
