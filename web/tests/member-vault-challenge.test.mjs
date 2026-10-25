import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('member vault monthly challenge eligibility and whole-ticket standings', async t => {
  const db = new PGlite()
  let sequence = 0
  const start = '2099-11-01T20:00:00+00:00'
  const known = { start_at: start, sport: 'nfl', id: '123' }
  const query = async (sql, params = []) => (await db.query(sql, params)).rows
  const load = async name => db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  const create = async ({
    owner = '10', odds = 150, units = 1, events = [known], submitted = '2026-10-15T12:00:00Z',
    guild = '400', channel = '300',
  } = {}) => {
    const id = ++sequence
    const rows = await query(`
      insert into public.member_bets (
        source_message_id, guild_id, channel_id, owner_id, owner_name, image_path,
        status, units, odds, details, submitted_at, challenge_events
      ) values ($1,$2,$3,$4,'Member',$1 || '.png','draft',$5,$6,$7::jsonb,$8,$9::jsonb)
      returning *
    `, [String(id), guild, channel, owner, units, odds,
      JSON.stringify({ game: 'Home vs Away', legs: events.map(() => ({ selection: 'Private pick', odds: -110 })) }),
      submitted, JSON.stringify(events)])
    return rows[0]
  }
  const get = async id => (await query('select * from public.member_bets where id = $1', [id]))[0]
  const confirm = async id => (await query(`
    update public.member_bets set confirmed_at = now(), status = 'review' where id = $1 returning *
  `, [id]))[0]
  const settle = async (id, result = 'win') => (await query(`
    select public.settle_member_bet($1, 'moderator', $2, 'Moderator-settled', 'Reviewed whole ticket') as changed
  `, [id, result]))[0].changed
  const verify = async (id, starts) => (await query(`
    select public.verify_member_challenge($1,'moderator',$2::timestamptz[],'Official schedule evidence') as changed
  `, [id, starts]))[0].changed
  const standings = async (month = '2026-10-01', guild = '400', channel = '300') =>
    query('select * from public.member_challenge_standings($1,$2,$3::date)', [guild, channel, month])
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create schema storage;
      create table storage.buckets (
        id text primary key, name text, public boolean, file_size_limit bigint, allowed_mime_types text[]
      );
    `)
    await load('20261003000000_member_bet_vault.sql')
    await load('20261025000000_member_vault_monthly_challenge.sql')

    await t.test('positive ticket odds, not leg odds, control eligibility', async () => {
      const negative = await create({ odds: -110 })
      assert.equal(negative.status, 'rejected')
      assert.equal(negative.challenge_eligibility, 'rejected')
      assert.equal(await settle(negative.id), false)
      const even = await create({ odds: 100, events: [known, known] })
      assert.equal((await confirm(even.id)).challenge_eligibility, 'eligible')
      assert.equal(await settle(even.id, 'void'), true)
    })

    await t.test('every leg is checked, including the earliest later-listed leg', async () => {
      const late = await create({ events: [known, { start_at: '2020-01-01T12:00:00Z' }, null] })
      const row = await confirm(late.id)
      assert.equal(row.status, 'rejected')
      assert.equal(row.challenge_eligibility, 'rejected')
      assert.match(row.review_reason, /strictly before/)
      assert.equal(await settle(row.id), false)
      const uploadAtStart = await create({ submitted: start })
      assert.equal((await confirm(uploadAtStart.id)).status, 'rejected')
      const confirmAtStart = await create()
      const confirmed = await confirm(confirmAtStart.id)
      await query('update public.member_bets set challenge_events = $2::jsonb where id = $1', [
        confirmed.id, JSON.stringify([{ start_at: confirmed.confirmed_at }]),
      ])
      assert.equal((await get(confirmed.id)).status, 'rejected')
    })

    await t.test('confirmation uses database receipt time, not a backdated client time', async () => {
      const row = await create({ events: [{ start_at: '2026-01-01T00:00:00Z' }], submitted: '2025-12-01T00:00:00Z' })
      const [confirmed] = await query(`
        update public.member_bets set confirmed_at = '2025-12-02T00:00:00Z' where id = $1 returning *
      `, [row.id])
      assert.notEqual(confirmed.confirmed_at, '2025-12-02T00:00:00.000Z')
      assert.equal(confirmed.status, 'rejected')
      await assert.rejects(query("update public.member_bets set submitted_at = '2025-01-01' where id = $1", [row.id]), /immutable/)
      await assert.rejects(query("update public.member_bets set confirmed_at = '2025-01-01' where id = $1", [row.id]), /immutable/)
    })

    await t.test('unknown starts require complete, audited moderator evidence', async () => {
      const row = await create({ owner: '30', events: [known, null] })
      assert.equal((await confirm(row.id)).challenge_eligibility, 'pending')
      assert.equal(await settle(row.id), false)
      await assert.rejects(verify(row.id, [start]), /every leg/)
      await assert.rejects(verify(row.id, ['2099-11-02T20:00:00Z', start]), /cannot be overridden/)
      await assert.rejects(verify(row.id, [start, null]), /finite start time/)
      assert.equal(await verify(row.id, [start, start]), true)
      assert.equal((await get(row.id)).challenge_eligibility, 'eligible')
      assert.equal(await settle(row.id, 'void'), true)
      assert.equal(await verify(row.id, [start, start]), false)
      const audit = await query('select * from public.member_challenge_audit where bet_id = $1', [row.id])
      assert.equal(audit.length, 1)
      assert.equal(audit[0].evidence, 'Official schedule evidence')
      assert.equal(audit[0].eligibility, 'eligible')

      const late = await create({ events: [null] })
      await confirm(late.id)
      assert.equal(await verify(late.id, ['2020-01-01T00:00:00Z']), true)
      assert.equal((await get(late.id)).status, 'rejected')
      const rejectedAudit = await query('select eligibility from public.member_challenge_audit where bet_id = $1', [late.id])
      assert.equal(rejectedAudit[0].eligibility, 'rejected')
    })

    await t.test('standings count each whole ticket once and preserve all win-count ties', async () => {
      for (const [owner, units, odds, legs, result] of [
        ['10', 1, 100, 1, 'win'], ['10', 10, 500, 4, 'win'], ['10', 9, 150, 1, 'loss'],
        ['20', 100, 200, 2, 'win'], ['20', 1, 1000, 10, 'win'], ['20', 5, 120, 1, 'void'],
      ]) {
        const row = await create({ owner, units, odds, events: Array(legs).fill(known) })
        await confirm(row.id)
        assert.equal(await settle(row.id, result), true)
        assert.equal(await settle(row.id, result), false)
      }
      const draft = await create({ owner: '40', events: [null] })
      const [first, second, third, fourth] = await standings()
      assert.deepEqual([first.owner_id, first.wins, first.losses, first.position], ['10', 2, 1, 1])
      assert.deepEqual([second.owner_id, second.wins, second.losses, second.position], ['20', 2, 0, 1])
      assert.equal(third.wins, 0)
      assert.equal(fourth.owner_id, '40')
      assert.equal(fourth.unresolved, 1)
      await query("update public.member_bets set status = 'rejected' where id = $1", [draft.id])
    })

    await t.test('Eastern upload month, not settlement month, and scope determine standings', async () => {
      for (const [submitted, guild, channel] of [
        ['2026-11-01T03:59:59Z', '400', '300'],
        ['2026-11-01T04:00:00Z', '400', '300'],
        ['2026-10-15T12:00:00Z', '401', '300'],
        ['2026-10-15T12:00:00Z', '400', '301'],
      ]) {
        const row = await create({ owner: '50', submitted, guild, channel })
        await confirm(row.id)
        await settle(row.id)
      }
      const october = (await standings()).find(row => row.owner_id === '50')
      const november = (await standings('2026-11-01')).find(row => row.owner_id === '50')
      assert.equal(october.wins, 1)
      assert.equal(november.wins, 1)
      assert.equal((await standings('2026-10-01', '401'))[0].wins, 1)
      assert.equal((await standings('2026-10-01', '400', '301'))[0].wins, 1)
      await assert.rejects(standings('2026-10-02'), /first day/)
    })

    await t.test('historical entries are excluded and member roles cannot read or verify challenge records', async () => {
      const row = await create({ submitted: null, owner: 'historical' })
      await confirm(row.id)
      assert.equal(await settle(row.id), true)
      assert.equal((await get(row.id)).challenge_eligibility, 'excluded')
      assert.ok(!(await standings()).some(entry => entry.owner_id === 'historical'))
      for (const role of ['anon', 'authenticated']) {
        await db.exec(`set role ${role}`)
        try {
          await assert.rejects(standings(), /permission denied/)
          await assert.rejects(verify(row.id, [start]), /permission denied/)
          await assert.rejects(query('select * from public.member_challenge_audit'), /permission denied/)
          await assert.rejects(query('select * from public.member_bets'), /permission denied/)
        } finally {
          await db.exec('reset role')
        }
      }
    })

    await t.test('the bot service role can confirm, verify and settle with trigger enforcement', async () => {
      const row = await create({ guild: 'service-test', events: [null] })
      await db.exec('set role service_role')
      try {
        assert.equal((await confirm(row.id)).challenge_eligibility, 'pending')
        assert.equal(await verify(row.id, [start]), true)
        assert.equal(await settle(row.id), true)
        assert.equal((await standings('2026-10-01', 'service-test'))[0].wins, 1)
      } finally {
        await db.exec('reset role')
      }
    })
  } finally {
    await db.close()
  }
})
