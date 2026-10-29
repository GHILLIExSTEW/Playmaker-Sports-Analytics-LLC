import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

async function setup(t, size = 5) {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.plays(id bigint primary key, tracking_scope text, units numeric, stars integer);
    create table public.capper_ballots(
      message_id text primary key, state text not null default 'pending', error text, votes jsonb,
      play_id bigint, official_message_id text
    );
    create table public.capper_ballot_scan(
      singleton boolean primary key, started_at timestamptz, last_message_id text
    );
    insert into public.capper_ballot_scan values(true, now() - interval '1 day', '100');
    insert into public.capper_ballots(message_id) values('100');
    insert into public.plays values(1, 'official', 2, 4);
  `)
  for (const name of ['20261021000000_dynamic_capper_votes.sql', '20261022000000_slip_reviews.sql']) {
    await db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  }
  const review = { author_id: '42', slip_base64: 'c2xpcA==', source_text: 'original' }
  assert.equal((await db.query('select public.register_capper_slip($1,$2) as saved', ['500', review])).rows[0].saved, true)
  await db.exec("update public.capper_ballots set review_message_id='900' where message_id='500'")
  const roster = Array.from({ length: size }, (_, i) => String(42 + i))
  await db.query(`select public.open_capper_slip('500','42',$1,$2,
    now() + interval '1 hour', now() + interval '30 minutes')`,
  [roster, { legs: [{ selection: 'Wolves ML', odds: 100 }] }])
  return db
}

async function respond(db, voter, action) {
  return (await db.query("select public.respond_capper_slip('500',$1,$2) as result", [String(voter), action])).rows[0].result
}

async function finalize(db) {
  return (await db.query("select public.finalize_capper_slip('500') as result")).rows[0].result
}

async function closeVoting(db) {
  await db.exec("update public.capper_ballots set cutoff_at=now() - interval '1 second' where message_id='500'")
}

test('forum record mappings are private, preserve legacy channel context, and expose live linked results', async (t) => {
  const db = await setup(t)
  await db.exec(`alter table public.plays add column ballot_message_id text unique,
    add column status text, add column odds integer, add column message_id text;
    alter table public.capper_ballots enable row level security;
    grant select on public.capper_ballots, public.plays to service_role;
    create table public.operator_user_guide_content (html text);
    insert into public.operator_user_guide_content values
      ('Upload one slip image per message in Cappers_Ballet (1559647368650166322). Daily 3:00 AM Eastern cleanup preserves active and interrupted review messages.');
  `)
  for (const name of ['20261028000000_capper_forum_records.sql', '20261028010000_capper_forum_guide.sql']) {
    await db.exec(await readFile(new URL(`../../supabase/migrations/${name}`, import.meta.url), 'utf8'))
  }
  const original = (await db.query("select review from capper_ballots where message_id='500'")).rows[0].review
  assert.equal(original.source_channel_id, '1559647368650166322')
  assert.equal(original.slip_base64, 'c2xpcA==')
  await db.exec(`insert into capper_review_record_days values('2026-10-28','1565170427851047053','700');
    insert into capper_review_records values('500','2026-10-28','900','Pending');
    update plays set ballot_message_id='500', status='open', odds=100, message_id='800' where id=1;`)
  assert.equal((await db.query('select play from capper_review_record_snapshots')).rows[0].play.status, 'open')
  assert.equal((await db.query('select ballot from capper_review_record_snapshots')).rows[0].ballot.review.slip_base64, undefined)
  await db.exec("update plays set status='win' where id=1")
  assert.equal((await db.query('select play from capper_review_record_snapshots')).rows[0].play.status, 'win')
  const html = (await db.query('select html from operator_user_guide_content')).rows[0].html
  assert.match(html, /1565167115726618686/)
  assert.match(html, /1565170427851047053/)
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`)
    for (const table of ['capper_review_records', 'capper_review_record_days', 'capper_review_record_snapshots']) {
      await assert.rejects(db.query(`select * from ${table}`), /permission denied/)
    }
    await db.exec('reset role')
  }
  await db.exec('set role service_role')
  assert.equal((await db.query('select * from capper_review_record_snapshots')).rows.length, 1)
  await db.exec('reset role')
})

test('author plus one supporter qualifies despite three missing responses, at 1u only at cutoff', async (t) => {
  const db = await setup(t)
  assert.equal(await respond(db, 43, 'yes_add'), 'saved')
  assert.equal(await finalize(db), null)
  await closeVoting(db)
  const result = await finalize(db)
  assert.equal(result.outcome, 'processing')
  assert.equal(result.units, 1)
  assert.equal(result.stars, 3)
  assert.deepEqual(result.counts, { yes: 2, no: 0, pass: 0 })
  assert.deepEqual(result.voter_choices, { '42': 'yes', '43': 'yes' })
  assert.equal(await finalize(db), null)
  assert.equal(await respond(db, 44, 'yes_add'), 'closed')
  await db.query('update public.plays set units=1,stars=3,team_approval=$1 where id=1', [result])
  await assert.rejects(db.query('update public.plays set units=2,stars=4,team_approval=$1 where id=1',
    [{ ...result, units: 2, stars: 4 }]), /slip_approval_stake/)
})

test('all six capper counts and every yes/no combination use the selected support scale', async (t) => {
  const db = await setup(t)
  for (let size = 2; size <= 7; size++) {
    const roster = Array.from({ length: size }, (_, i) => String(42 + i))
    for (let yes = 1; yes <= size; yes++) {
      for (let no = 0; no <= size - yes; no++) {
        await db.query(`update public.capper_ballots set state='pending', decision=null,
          eligible_voter_ids=$1, cutoff_at=now() - interval '1 second', response_states=$2 where message_id='500'`,
        [roster, Object.fromEntries(roster.slice(0, yes + no).map((id, index) => [id, { [index < yes ? 'yes' : 'no']: true }]))])
        const result = await finalize(db)
        const qualified = yes >= 2 && yes > no
        assert.equal(result.outcome, qualified ? 'processing' : 'rejected', `${size}/${yes}/${no}`)
        assert.equal(result.units, qualified ? Math.min(yes - 1, 3) : null)
      }
    }
  }
})

test('all responses finalize early, Pass is distinct from opposition, and author counts once', async (t) => {
  const db = await setup(t, 4)
  assert.equal(await respond(db, 42, 'no_add'), 'author-confirmed')
  assert.equal(await respond(db, 999, 'yes_add'), 'ineligible')
  await respond(db, 43, 'yes_add')
  await respond(db, 44, 'yes_add')
  assert.equal(await finalize(db), null)
  await respond(db, 45, 'pass')
  const result = await finalize(db)
  assert.deepEqual(result.counts, { yes: 3, no: 0, pass: 1 })
  assert.equal(result.units, 2)
})

test('conflicting reactions are unresolved, removals change responses, and Pass survives remove events', async (t) => {
  const db = await setup(t, 3)
  await respond(db, 43, 'yes_add')
  assert.equal(await respond(db, 43, 'no_add'), 'conflict')
  await respond(db, 44, 'pass')
  assert.equal(await finalize(db), null)
  await respond(db, 43, 'no_remove')
  await respond(db, 43, 'pass')
  await respond(db, 43, 'yes_remove')
  const result = await finalize(db)
  assert.equal(result.outcome, 'rejected')
  assert.deepEqual(result.counts, { yes: 1, no: 0, pass: 2 })
})

test('author confirmation and cutoff validation are enforced in the database', async (t) => {
  const db = await setup(t)
  await db.query("select public.register_capper_slip('600',$1)", [{ author_id: '42', slip_base64: 'c2xpcA==' }])
  await db.exec("update public.capper_ballots set review_message_id='901' where message_id='600'")
  for (const [author, roster, start, cutoff, error] of [
    ['43', ['42', '43'], "now() + interval '1 hour'", "now() + interval '5 minutes'", /author confirmation/],
    ['42', ['42', '42'], "now() + interval '1 hour'", "now() + interval '5 minutes'", /verified roster/],
    ['42', ['42'], "now() + interval '1 hour'", "now() + interval '5 minutes'", /verified roster/],
    ['42', ['42', '43'], "now() + interval '1 hour'", "now() - interval '1 second'", /future voting cutoff/],
    ['42', ['42', '43'], "now() + interval '1 hour'", "now() + interval '1 hour'", /future voting cutoff/],
  ]) {
    await assert.rejects(db.query(`select public.open_capper_slip('600',$1,$2,$3,${start},${cutoff})`,
      [author, roster, { legs: [{ selection: 'Wolves ML', odds: 100 }] }]), error)
  }
  await assert.rejects(db.query(`select public.open_capper_slip('500','42',$1,$2,
    now() + interval '2 hours',now() + interval '1 hour')`,
  [['42', '43'], { legs: [{ selection: 'Changed', odds: 120 }] }]), /author confirmation/)
})

test('cancellation cannot race the cutoff, duplicates cannot replace originals, and expired events never publish', async (t) => {
  const db = await setup(t)
  assert.equal((await db.query("select public.register_capper_slip('500',$1) as saved",
    [{ author_id: '99', slip_base64: 'changed' }])).rows[0].saved, false)
  assert.equal((await db.query("select public.cancel_capper_slip('500','43') as result")).rows[0].result, false)
  await closeVoting(db)
  assert.equal((await db.query("select public.cancel_capper_slip('500','42') as result")).rows[0].result, false)
  await db.exec(`update public.capper_ballots set event_starts_at=now() - interval '1 second',
    cutoff_at=now() - interval '2 seconds' where message_id='500'`)
  assert.deepEqual(await finalize(db), { outcome: 'expired' })
  assert.equal((await db.query("select state from public.capper_ballots where message_id='500'")).rows[0].state, 'failed')
})

test('private review RPCs and images are inaccessible to website roles; cutover preserves history', async (t) => {
  const db = await setup(t)
  assert.deepEqual((await db.query('select units::float,stars from public.plays')).rows, [{ units: 2, stars: 4 }])
  assert.equal((await db.query("select state from public.capper_ballots where message_id='100'")).rows[0].state, 'failed')
  assert.equal((await db.query('select last_message_id from public.capper_ballot_scan')).rows[0].last_message_id, null)
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query('select has_table_privilege($1,$2,$3) as allowed',
      [role, 'public.capper_ballots', 'select'])).rows[0].allowed, false)
    for (const fn of [
      'register_capper_slip(text,jsonb)', 'open_capper_slip(text,text,text[],jsonb,timestamptz,timestamptz)',
      'respond_capper_slip(text,text,text)', 'finalize_capper_slip(text)',
      'cancel_capper_slip(text,text)', 'fail_capper_slip(text,text)',
    ]) {
      assert.equal((await db.query('select has_function_privilege($1,$2,$3) as allowed',
        [role, `public.${fn}`, 'execute'])).rows[0].allowed, false)
    }
  }
})

test('member feed exposes versioned totals only and preserves the existing membership gate', async (t) => {
  const db = await setup(t)
  await respond(db, 43, 'yes_add')
  await closeVoting(db)
  const decision = await finalize(db)
  await db.query('update public.plays set units=1,stars=3,team_approval=$1 where id=1', [decision])
  await db.exec(`
    create table public.tracking_display_settings(singleton boolean, starts_at timestamptz);
    insert into public.tracking_display_settings values(true, '2026-10-14T00:00:00Z');
    create function public.archived_current_picks(text,text)
    returns table(id bigint,created_at timestamptz,sport text,capper text,avatar_url text,
      selection text,analysis text,odds integer,units numeric)
    language plpgsql stable security definer set search_path='' as $$
    begin
      if current_setting('test.member_access',true) is distinct from 'active' then
        raise exception 'Verified active membership required.';
      end if;
      return query select 1::bigint,now(),'NBA'::text,'Official Playmaker Picks'::text,
        null::text,'Wolves ML'::text,''::text,100,1::numeric;
    end;
    $$;
    revoke all on function public.archived_current_picks(text,text) from public;
    create function public.member_current_picks(text,text) returns void language sql as $$ select $$;
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261022010000_slip_review_current_picks.sql', import.meta.url), 'utf8'))
  await db.exec('set role authenticated')
  await assert.rejects(db.query('select * from public.member_current_picks()'), /Verified active membership/)
  await db.exec("select set_config('test.member_access','active',false)")
  const row = (await db.query('select * from public.member_current_picks()')).rows[0]
  assert.equal(row.approval_policy, 'slip-v1')
  assert.equal(row.approval_yes, 2)
  assert.equal(row.approval_no, 0)
  assert.equal(row.approval_pass, 0)
  assert.equal(row.eligible_cappers, 5)
  for (const field of ['voter_choices', 'response_states', 'review', 'slip_base64', 'author_id']) {
    assert.equal(field in row, false)
  }
})
