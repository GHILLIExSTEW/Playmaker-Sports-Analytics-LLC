import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

async function setup(t) {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema auth;
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('test.uid',true),'')::uuid
    $$;
    create table auth.identities(user_id uuid, provider text, provider_id text);
    insert into auth.identities values
      ('00000000-0000-0000-0000-000000000042','discord','42'),
      ('00000000-0000-0000-0000-000000000043','discord','43'),
      ('00000000-0000-0000-0000-000000000099','discord','99');
    create table public.users(id bigint primary key, discord_user_id text);
    insert into public.users values(1,'42'),(2,'900');
    create table public.plays(
      id bigint primary key, user_id bigint, tracking_scope text, team_approval jsonb,
      team_name text, status text default 'open', settled_at timestamptz, message_id text,
      created_at timestamptz default now(), units numeric default 1, odds integer default 100
    );
    insert into public.plays(id,user_id,tracking_scope,team_approval,team_name,message_id) values
      (1,2,'official','{"policy_version":"slip-v1","author_id":"42","yes":2,"roster_size":5,"counts":{"yes":2,"no":0,"pass":0}}','Wolves ML','101'),
      (2,1,'capper',null,'Personal pick','102'),
      (3,2,'official',null,'Historical team pick','103'),
      (4,2,'official','{"policy_version":"slip-v1","author_id":"42"}','Unpublished',null),
      (5,2,'official','{"policy_version":"slip-v1","author_id":"42"}','Closed','105'),
      (6,2,'official','{"policy_version":"slip-v1","author_id":"42"}','Duplicate','101');
    update public.plays set status='win', settled_at=now() where id=5;
    create table public.play_legs(play_id bigint,leg_number integer,selection text);
    insert into public.play_legs values(1,1,'Wolves ML (+100)');
    create table public.play_insights(
      play_id bigint primary key, justification text not null default '', updated_at timestamptz, prompt_message_id text
    );
    create function public.website_capper_ids() returns text[] language sql stable as $$
      select case when current_setting('test.roster',true) = 'removed'
        then array['43']::text[] else array['42','43']::text[] end
    $$;
    create function public.website_can_edit_all_cappers() returns boolean language sql stable as $$
      select coalesce(current_setting('test.owner',true),'false') = 'true'
    $$;
    create function public.website_member_access() returns jsonb language sql stable as $$
      select jsonb_build_object('state',coalesce(nullif(current_setting('test.access',true),''),'active'))
    $$;
    create function public.owned_capper_page() returns text language sql as $$ select 'Capper'::text $$;
    create table public.tracking_display_settings(singleton boolean,starts_at timestamptz);
    insert into public.tracking_display_settings values(true,'2026-10-14T00:00:00Z');
    create function public.archived_current_picks(text,text)
    returns table(id bigint,created_at timestamptz,sport text,capper text,avatar_url text,
      selection text,analysis text,odds integer,units numeric)
    language plpgsql stable security definer set search_path='' as $$
    begin
      if public.website_member_access() ->> 'state' is distinct from 'active' then
        raise exception 'Verified active membership required.';
      end if;
      return query select p.id,p.created_at,'NBA'::text,
        case when p.tracking_scope='official' then 'Official Playmaker Picks' else 'Capper' end,
        null::text,p.team_name,coalesce(i.justification,''),p.odds,p.units
      from public.plays p left join public.play_insights i on i.play_id=p.id
      where p.status='open' and p.message_id is not null;
    end;
    $$;
    create function public.member_current_picks(text,text) returns void language sql as $$ select $$;
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261022030000_official_capper_insights.sql', import.meta.url), 'utf8'))
  return db
}

async function identity(db, id, owner = false) {
  await db.query("select set_config('test.uid',$1,false)", [`00000000-0000-0000-0000-0000000000${id}`])
  await db.query("select set_config('test.owner',$1,false)", [String(owner)])
}

test('team insight context resolves the original capper, not the bot or supporting voters', async (t) => {
  const db = await setup(t)
  const rows = (await db.query('select * from public.capper_insight_context()')).rows
  assert.deepEqual(rows.map((r) => [r.play_id, r.discord_user_id]), [[1, '42'], [2, '42']])
  assert.equal(rows[0].selection, 'Wolves ML (+100)')
  assert.equal((await db.query('select public.play_insight_author(1) as author')).rows[0].author, '42')
  assert.equal((await db.query('select public.play_insight_author(3) as author')).rows[0].author, null)
})

test('only the original current capper can save a nonempty team justification', async (t) => {
  const db = await setup(t)
  for (const actor of ['43', '900', '99']) {
    await assert.rejects(db.query("select public.save_capper_insight(1,$1,'Reason')", [actor]), /original current OPERATOR/)
  }
  for (const text of ['', '   ', 'x'.repeat(2001)]) {
    await assert.rejects(db.query("select public.save_capper_insight(1,'42',$1)", [text]), /1 to 2000/)
  }
  assert.equal((await db.query("select public.save_capper_insight(1,'42','  Matchup reasoning  ') as saved")).rows[0].saved, true)
  assert.equal((await db.query('select justification from public.play_insights where play_id=1')).rows[0].justification, 'Matchup reasoning')
  await db.exec("select set_config('test.roster','removed',false)")
  await assert.rejects(db.query("select public.save_capper_insight(1,'42','Changed')"), /original current OPERATOR/)
})

test('closed, unpublished, duplicate and unattributed historical team picks cannot be assigned guessed insight authors', async (t) => {
  const db = await setup(t)
  for (const id of [3, 4, 5, 6]) {
    await assert.rejects(db.query("select public.save_capper_insight($1,'42','Reason')", [id]), /original current OPERATOR/)
  }
})

test('website team editor is available only to the original capper, even when another account is Owner', async (t) => {
  const db = await setup(t)
  await identity(db, '42')
  await db.exec('set role authenticated')
  const authorRow = (await db.query('select * from public.member_current_picks() where id=1')).rows[0]
  assert.equal(authorRow.can_edit_insight, true)
  assert.equal(authorRow.capper, 'Official Playmaker Picks')
  assert.equal('author_id' in authorRow, false)
  assert.equal((await db.query("select public.save_my_pick_insight(1,'Author reasoning') as saved")).rows[0].saved, true)
  assert.equal((await db.query('select analysis from public.member_current_picks() where id=1')).rows[0].analysis, 'Author reasoning')
  await identity(db, '43')
  assert.equal((await db.query('select can_edit_insight from public.member_current_picks() where id=1')).rows[0].can_edit_insight, false)
  await assert.rejects(db.query("select public.save_my_pick_insight(1,'Not my pick')"), /original current OPERATOR/)
  await identity(db, '99', true)
  assert.equal((await db.query('select can_edit_insight from public.member_current_picks() where id=1')).rows[0].can_edit_insight, false)
  await assert.rejects(db.query("select public.save_my_pick_insight(1,'Owner impersonation')"), /original current OPERATOR/)
})

test('personal Owner editing and member access gates remain intact', async (t) => {
  const db = await setup(t)
  await identity(db, '99', true)
  await db.exec('set role authenticated')
  assert.equal((await db.query("select public.save_my_pick_insight(2,'Personal edit') as saved")).rows[0].saved, true)
  await db.exec("select set_config('test.access','membership-required',false)")
  await assert.rejects(db.query('select * from public.member_current_picks()'), /Verified active membership/)
  await identity(db, '42')
  await assert.rejects(db.query("select public.save_my_pick_insight(1,'No access')"), /Verified active/)
})
