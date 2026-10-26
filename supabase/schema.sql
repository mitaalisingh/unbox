-- Unbox: database layout for Supabase (Postgres).  Safe to run more than once (idempotent).
-- Run it in the Supabase SQL editor of YOUR project once you have reviewed it.
--
-- Design notes (see DESIGN.md):
--   * Users live in Supabase Auth (auth.users). We never store account passwords ourselves.
--   * A user owns many trips; a trip holds a tree of boxes and items ("nodes").
--   * Owners read/write through Row Level Security (RLS): you can only touch your own trips.
--   * Viewers (no account) get in through the Flask server with the trip's viewer password.
--     The server checks it, then reads with the service key. The password is stored hashed.
--   * Photos live in a PRIVATE storage bucket and are shown through short-lived signed links.

create extension if not exists vector with schema extensions;   -- semantic search (pgvector)
set search_path = public, extensions;                            -- so "vector" and its operators resolve below

-- One row per person, to record consent and the 18+ confirmation (DPDP-friendly).
create table if not exists public.profiles (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  display_name   text,
  accepted_terms_at   timestamptz not null,
  confirmed_18_plus   boolean     not null check (confirmed_18_plus),
  created_at     timestamptz not null default now()
);

create table if not exists public.trips (
  id              uuid primary key default gen_random_uuid(),
  owner_id        uuid not null references auth.users(id) on delete cascade,
  name            text not null,
  archived_at     timestamptz,                      -- archive = hidden but kept; null = active
  viewer_pw_hash  text,                             -- hashed (scrypt or argon2) by the server; never plain text
  viewer_pw_set_at timestamptz,
  locked_at       timestamptz,
  share_code_hash text,                             -- scrambled trip code; the code itself is never stored
  share_code_expires_at timestamptz,                      -- set = the trip is locked (view only, even for the owner)
  created_at      timestamptz not null default now()
);
alter table public.trips add column if not exists locked_at timestamptz;
alter table public.trips add column if not exists share_code_hash text;
alter table public.trips add column if not exists share_code_expires_at timestamptz;
create index if not exists trips_owner_idx on public.trips(owner_id);

-- Boxes and items in one table: is_box tells them apart; parent_id nests them.
create table if not exists public.nodes (
  id          uuid primary key default gen_random_uuid(),
  trip_id     uuid not null references public.trips(id) on delete cascade,
  parent_id   uuid references public.nodes(id) on delete cascade,
  is_box      boolean not null default false,
  name        text not null,
  description text not null default '',
  tags        text not null default '',
  weight_kg   numeric(8,2),
  photo_path  text,                                 -- path inside the private 'photos' bucket
  ai_suggested_name text,                           -- what the model proposed, kept for measuring accuracy
  ai_confidence     real,                           -- 0..1, used to flag low-confidence names for review
  position    integer not null default 0,
  embedding   vector(384),                          -- bge-small-en-v1.5 produces 384 numbers
  created_at  timestamptz not null default now()
);
create index if not exists nodes_trip_idx   on public.nodes(trip_id);
create index if not exists nodes_parent_idx on public.nodes(parent_id);
create index if not exists nodes_embedding_idx on public.nodes using hnsw (embedding vector_cosine_ops);

-- Wrong-password tries on viewer links, so guessing can be slowed down.
create table if not exists public.viewer_attempts (
  id         bigint generated always as identity primary key,
  trip_id    uuid not null references public.trips(id) on delete cascade,
  client_key text not null,                         -- hashed IP, not the raw address
  attempted_at timestamptz not null default now()
);
create index if not exists viewer_attempts_idx on public.viewer_attempts(trip_id, client_key, attempted_at);

-- ---------- Row Level Security: owners only ----------
alter table public.profiles        enable row level security;
alter table public.trips           enable row level security;
alter table public.nodes           enable row level security;
alter table public.viewer_attempts enable row level security;   -- no policy = nobody but the server (service key)

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "own trips" on public.trips;
create policy "own trips" on public.trips
  for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());

drop policy if exists "nodes of own trips" on public.nodes;
create policy "nodes of own trips" on public.nodes
  for all
  using      (exists (select 1 from public.trips t where t.id = nodes.trip_id and t.owner_id = auth.uid()))
  with check (exists (select 1 from public.trips t where t.id = nodes.trip_id and t.owner_id = auth.uid()));

-- ---------- Private photo bucket ----------
insert into storage.buckets (id, name, public)
values ('photos', 'photos', false)                  -- false = private: no guessable public URLs
on conflict (id) do nothing;

-- Files are stored as  <trip_id>/<node_id>.jpg ; owners can only reach their own trip folders.
drop policy if exists "owners read own photos" on storage.objects;
create policy "owners read own photos" on storage.objects
  for select using (
    bucket_id = 'photos'
    and (storage.foldername(name))[1] in (select id::text from public.trips where owner_id = auth.uid()));
drop policy if exists "owners write own photos" on storage.objects;
create policy "owners write own photos" on storage.objects
  for insert with check (
    bucket_id = 'photos'
    and (storage.foldername(name))[1] in (select id::text from public.trips where owner_id = auth.uid()));
drop policy if exists "owners delete own photos" on storage.objects;
create policy "owners delete own photos" on storage.objects
  for delete using (
    bucket_id = 'photos'
    and (storage.foldername(name))[1] in (select id::text from public.trips where owner_id = auth.uid()));

-- ---------- Semantic search inside one trip ----------
create or replace function public.match_nodes(p_trip uuid, p_query vector(384), p_limit int default 20)
returns table (id uuid, name text, similarity real)
language sql stable security invoker set search_path = public, extensions as $$
  select n.id, n.name, (1 - (n.embedding <=> p_query))::real as similarity
  from public.nodes n
  where n.trip_id = p_trip and n.embedding is not null
  order by n.embedding <=> p_query
  limit p_limit;
$$;

-- ---------- Explicit permissions (the project is created with "Automatically expose new tables" OFF) ----------
-- Only logged-in users ("authenticated") may touch these tables, and RLS above limits them to their own rows.
-- The anonymous (not-logged-in) role gets nothing. The server uses the secret key for viewer access.
grant usage on schema public to authenticated;
grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.trips    to authenticated;
grant select, insert, update, delete on public.nodes    to authenticated;
-- The server (secret key = service_role) bypasses RLS but still needs table privileges when auto-expose is off.
grant usage on schema public to service_role;
grant all on public.profiles, public.trips, public.nodes, public.viewer_attempts to service_role;
grant usage, select on all sequences in schema public to service_role;
-- viewer_attempts: no grants to authenticated (server only).

-- Functions are callable by everyone by default in Postgres, so lock this one down explicitly.
revoke execute on function public.match_nodes(uuid, vector, int) from public, anon;
grant  execute on function public.match_nodes(uuid, vector, int) to authenticated, service_role;

-- NOTE for the app code:
--   * Deleting a trip or node removes database rows by cascade, but NOT the photo files.
--     The server must delete the files in storage too ("delete means delete").
--   * Strip EXIF/GPS and resize photos BEFORE uploading them to the bucket.
