-- TCC Student Parliament Election: online database (Supabase).
-- Paste all of this into Supabase -> SQL Editor -> New query, and press Run. Safe to run again.
--
-- Security model
--   * Students never read the voters table. They can only call el_lookup / el_vote with their own index number
--     (and PIN, if the uploaded list has PINs).
--   * Each student can vote once. The vote is stored WITHOUT the student's number, so votes stay secret.
--   * Only accounts listed in el_admins can see results or change anything.

create table if not exists el_settings (id int primary key default 1 check (id = 1), title text not null default 'Student Parliament Election', open boolean not null default false);
insert into el_settings (id) values (1) on conflict do nothing;

create table if not exists el_candidates (id serial primary key, number text not null, name text not null, class text default '', photo_url text);
create table if not exists el_voters (idx text primary key, name text not null, class text default '', pin text, voted boolean not null default false, voted_at timestamptz);
create table if not exists el_votes (id bigserial primary key, candidate_id int not null references el_candidates(id) on delete restrict, cast_at date not null default current_date);
create table if not exists el_admins (user_id uuid primary key);

-- index numbers are compared without spaces, case or leading zeros ("00123" = "123")
create or replace function el_norm(v text) returns text language sql immutable as $$
  select coalesce(nullif(regexp_replace(regexp_replace(upper(regexp_replace(coalesce(v, ''), '\s', '', 'g')), '\.0$', ''), '^0+(?=.)', ''), ''), '')
$$;
create or replace function el_norm_trg() returns trigger language plpgsql as $$
begin new.idx := el_norm(new.idx); new.pin := nullif(trim(coalesce(new.pin, '')), ''); return new; end $$;
drop trigger if exists el_voters_norm on el_voters;
create trigger el_voters_norm before insert or update of idx, pin on el_voters for each row execute function el_norm_trg();

create or replace function el_is_admin() returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from el_admins where user_id = auth.uid())
$$;

alter table el_settings enable row level security;
alter table el_candidates enable row level security;
alter table el_voters enable row level security;
alter table el_votes enable row level security;
alter table el_admins enable row level security;

drop policy if exists "settings read" on el_settings;      create policy "settings read" on el_settings for select using (true);
drop policy if exists "settings admin" on el_settings;     create policy "settings admin" on el_settings for update using (el_is_admin()) with check (el_is_admin());
drop policy if exists "candidates read" on el_candidates;  create policy "candidates read" on el_candidates for select using (true);
drop policy if exists "candidates admin" on el_candidates; create policy "candidates admin" on el_candidates for all using (el_is_admin()) with check (el_is_admin());
drop policy if exists "voters admin" on el_voters;         create policy "voters admin" on el_voters for all using (el_is_admin()) with check (el_is_admin());
drop policy if exists "votes admin read" on el_votes;      create policy "votes admin read" on el_votes for select using (el_is_admin());
drop policy if exists "admins self" on el_admins;          create policy "admins self" on el_admins for select using (user_id = auth.uid());

-- Student: check number (and PIN) and get name + class
create or replace function el_lookup(p_idx text, p_pin text default null)
returns table (name text, class text) language plpgsql security definer set search_path = public as $$
declare v el_voters;
begin
  if not (select open from el_settings where id = 1) then raise exception 'CLOSED'; end if;
  select * into v from el_voters where idx = el_norm(p_idx);
  if not found then raise exception 'NOT_FOUND'; end if;
  if v.pin is not null and v.pin <> trim(coalesce(p_pin, '')) then raise exception 'BAD_PIN'; end if;
  if v.voted then raise exception 'ALREADY_VOTED'; end if;
  return query select v.name, v.class;
end $$;

-- Student: cast the vote (once)
create or replace function el_vote(p_idx text, p_pin text, p_candidate int)
returns void language plpgsql security definer set search_path = public as $$
declare v el_voters;
begin
  if not (select open from el_settings where id = 1) then raise exception 'CLOSED'; end if;
  select * into v from el_voters where idx = el_norm(p_idx) for update;
  if not found then raise exception 'NOT_FOUND'; end if;
  if v.pin is not null and v.pin <> trim(coalesce(p_pin, '')) then raise exception 'BAD_PIN'; end if;
  if v.voted then raise exception 'ALREADY_VOTED'; end if;
  if not exists (select 1 from el_candidates where id = p_candidate) then raise exception 'NO_CANDIDATE'; end if;
  update el_voters set voted = true, voted_at = now() where idx = v.idx;
  insert into el_votes (candidate_id) values (p_candidate);
end $$;

-- Admin: results
create or replace function el_results()
returns table (id int, number text, name text, class text, photo_url text, votes bigint) language plpgsql stable security definer set search_path = public as $$
begin
  if not el_is_admin() then raise exception 'NOT_ADMIN'; end if;
  return query select c.id, c.number, c.name, c.class, c.photo_url, count(v.id)
    from el_candidates c left join el_votes v on v.candidate_id = c.id group by c.id order by count(v.id) desc, c.number;
end $$;

-- Admin: clear votes (and optionally everything)
create or replace function el_reset(p_everything boolean default false)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not el_is_admin() then raise exception 'NOT_ADMIN'; end if;
  delete from el_votes where true;
  update el_voters set voted = false, voted_at = null where voted;
  if p_everything then delete from el_voters where true; delete from el_candidates where true; end if;
  update el_settings set open = false where id = 1;
end $$;

revoke all on function el_lookup(text, text), el_vote(text, text, int), el_results(), el_reset(boolean) from public;
grant execute on function el_lookup(text, text), el_vote(text, text, int) to anon, authenticated;
grant execute on function el_results(), el_reset(boolean), el_is_admin() to authenticated;

-- Candidate photos (Supabase Storage): public to view, only admins can upload
do $$ begin
  if exists (select 1 from information_schema.schemata where schema_name = 'storage') then
    insert into storage.buckets (id, name, public) values ('el-photos', 'el-photos', true) on conflict (id) do nothing;
    drop policy if exists "el photos admin insert" on storage.objects;
    create policy "el photos admin insert" on storage.objects for insert to authenticated with check (bucket_id = 'el-photos' and public.el_is_admin());
    drop policy if exists "el photos admin delete" on storage.objects;
    create policy "el photos admin delete" on storage.objects for delete to authenticated using (bucket_id = 'el-photos' and public.el_is_admin());
  end if;
end $$;

-- LAST STEP (once): make your admin account an admin. Create the user first in Authentication -> Users -> Add user,
-- then replace the email below and run just this line:
-- insert into el_admins (user_id) select id from auth.users where email = 'your-admin-email@gmail.com' on conflict do nothing;
