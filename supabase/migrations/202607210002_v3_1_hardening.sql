-- V3.1 additive hardening. Runtime activation remains controlled by DELIVERY_BACKEND.

alter table public.delivery_queue
  add column if not exists market_group text,
  add column if not exists payload jsonb not null default '{}'::jsonb,
  add column if not exists worker_id uuid,
  add column if not exists claimed_at timestamptz,
  add column if not exists next_attempt_at timestamptz,
  add column if not exists receipt jsonb not null default '{}'::jsonb;

create index if not exists delivery_queue_claim_idx
  on public.delivery_queue (status, market_group, scheduled_for, next_attempt_at);

create table if not exists public.scanner_runs (
  run_id uuid primary key,
  market text not null,
  mode text not null,
  status text not null check (status in ('running','completed','failed','partial')),
  started_at timestamptz not null,
  completed_at timestamptz,
  stats jsonb not null default '{}'::jsonb,
  error text,
  source text not null default 'github-actions'
);

create index if not exists scanner_runs_market_time_idx
  on public.scanner_runs (market, started_at desc);

create table if not exists public.dashboard_sessions (
  token_hash text primary key,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz,
  user_agent_hash text
);

create index if not exists dashboard_sessions_expiry_idx
  on public.dashboard_sessions (expires_at)
  where revoked_at is null;

alter table public.scanner_runs enable row level security;
alter table public.dashboard_sessions enable row level security;
revoke all on table public.scanner_runs from anon, authenticated;
revoke all on table public.dashboard_sessions from anon, authenticated;
grant all on table public.scanner_runs to service_role;
grant all on table public.dashboard_sessions to service_role;

create or replace function public.upsert_signal_records(
  p_rows jsonb,
  p_preserve_lifecycle boolean default false
) returns integer
language plpgsql
set search_path = ''
as $$
declare
  signal_row jsonb;
  changed integer := 0;
begin
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'p_rows must be a JSON array';
  end if;
  for signal_row in select value from jsonb_array_elements(p_rows)
  loop
    insert into public.signals (
      id, market, symbol, name, direction, signal_type, signal_date, close,
      score, grade, weekly_alignment, status, reasons, warnings, metrics,
      risk_plan, cluster, detected_at, updated_at, delivered_at
    ) values (
      signal_row->>'id', signal_row->>'market', signal_row->>'symbol',
      coalesce(signal_row->>'name', signal_row->>'symbol'),
      signal_row->>'direction', signal_row->>'signal_type',
      (signal_row->>'signal_date')::date, (signal_row->>'close')::numeric,
      (signal_row->>'score')::integer, signal_row->>'grade',
      coalesce(signal_row->>'weekly_alignment', 'unknown'),
      coalesce(signal_row->>'status', 'confirmed'),
      coalesce(signal_row->'reasons', '[]'::jsonb),
      coalesce(signal_row->'warnings', '[]'::jsonb),
      coalesce(signal_row->'metrics', '{}'::jsonb),
      coalesce(signal_row->'risk_plan', '{}'::jsonb),
      coalesce(signal_row->>'cluster', 'unknown'),
      coalesce((signal_row->>'detected_at')::timestamptz, now()), now(),
      nullif(signal_row->>'delivered_at', '')::timestamptz
    )
    on conflict (id) do update set
      name = excluded.name,
      close = excluded.close,
      score = excluded.score,
      grade = excluded.grade,
      weekly_alignment = excluded.weekly_alignment,
      status = case when p_preserve_lifecycle then public.signals.status else excluded.status end,
      reasons = excluded.reasons,
      warnings = excluded.warnings,
      metrics = excluded.metrics,
      risk_plan = excluded.risk_plan,
      cluster = excluded.cluster,
      detected_at = case when p_preserve_lifecycle then public.signals.detected_at else excluded.detected_at end,
      delivered_at = coalesce(public.signals.delivered_at, excluded.delivered_at),
      updated_at = now();
    changed := changed + 1;
  end loop;
  return changed;
end;
$$;

create or replace function public.enqueue_delivery_signals(
  p_items jsonb,
  p_scheduled_for timestamptz
) returns integer
language plpgsql
set search_path = ''
as $$
declare
  item jsonb;
  signal_row jsonb;
  queued integer := 0;
begin
  if jsonb_typeof(p_items) <> 'array' then
    raise exception 'p_items must be a JSON array';
  end if;

  for item in select value from jsonb_array_elements(p_items)
  loop
    signal_row := item->'signal';
    insert into public.signals (
      id, market, symbol, name, direction, signal_type, signal_date, close,
      score, grade, weekly_alignment, status, reasons, warnings, metrics,
      risk_plan, cluster, detected_at, updated_at, delivered_at
    ) values (
      signal_row->>'id', signal_row->>'market', signal_row->>'symbol',
      coalesce(signal_row->>'name', signal_row->>'symbol'),
      signal_row->>'direction', signal_row->>'signal_type',
      (signal_row->>'signal_date')::date, (signal_row->>'close')::numeric,
      (signal_row->>'score')::integer, signal_row->>'grade',
      coalesce(signal_row->>'weekly_alignment', 'unknown'),
      coalesce(signal_row->>'status', 'confirmed'),
      coalesce(signal_row->'reasons', '[]'::jsonb),
      coalesce(signal_row->'warnings', '[]'::jsonb),
      coalesce(signal_row->'metrics', '{}'::jsonb),
      coalesce(signal_row->'risk_plan', '{}'::jsonb),
      coalesce(signal_row->>'cluster', 'unknown'),
      coalesce((signal_row->>'detected_at')::timestamptz, now()), now(),
      nullif(signal_row->>'delivered_at', '')::timestamptz
    )
    on conflict (id) do update set
      name = excluded.name,
      close = excluded.close,
      score = excluded.score,
      grade = excluded.grade,
      weekly_alignment = excluded.weekly_alignment,
      reasons = excluded.reasons,
      warnings = excluded.warnings,
      metrics = excluded.metrics,
      risk_plan = excluded.risk_plan,
      cluster = excluded.cluster,
      updated_at = now();

    insert into public.delivery_queue (
      signal_id, channel, status, scheduled_for, next_attempt_at,
      market_group, payload, updated_at
    ) values (
      signal_row->>'id', 'telegram', 'pending', p_scheduled_for, p_scheduled_for,
      item->>'market_group', coalesce(item->'payload', '{}'::jsonb), now()
    )
    on conflict (signal_id, channel) do update set
      scheduled_for = least(public.delivery_queue.scheduled_for, excluded.scheduled_for),
      next_attempt_at = case
        when public.delivery_queue.status in ('delivered','cancelled') then public.delivery_queue.next_attempt_at
        else least(coalesce(public.delivery_queue.next_attempt_at, excluded.next_attempt_at), excluded.next_attempt_at)
      end,
      market_group = excluded.market_group,
      payload = excluded.payload,
      status = case
        when public.delivery_queue.status in ('delivered','cancelled','sending') then public.delivery_queue.status
        else 'pending'
      end,
      updated_at = now();
    queued := queued + 1;
  end loop;
  return queued;
end;
$$;

create or replace function public.claim_delivery_batch(
  p_market_group text,
  p_limit integer,
  p_worker_id uuid,
  p_claim_timeout_minutes integer default 30
) returns table (queue_id bigint, signal_id text, payload jsonb, attempts integer)
language plpgsql
set search_path = ''
as $$
begin
  update public.delivery_queue
  set status = 'failed',
      worker_id = null,
      claimed_at = null,
      last_error = coalesce(last_error, 'stale delivery claim released'),
      next_attempt_at = now(),
      updated_at = now()
  where status = 'sending'
    and claimed_at < now() - make_interval(mins => greatest(5, p_claim_timeout_minutes));

  return query
  with ready as (
    select q.id
    from public.delivery_queue q
    where q.status in ('pending','failed')
      and coalesce(q.scheduled_for, now()) <= now()
      and coalesce(q.next_attempt_at, now()) <= now()
      and (p_market_group = 'all' or q.market_group = p_market_group)
    order by q.scheduled_for nulls first, q.id
    for update skip locked
    limit greatest(1, least(coalesce(p_limit, 1000), 1000))
  ), claimed as (
    update public.delivery_queue q
    set status = 'sending',
        attempts = q.attempts + 1,
        worker_id = p_worker_id,
        claimed_at = now(),
        updated_at = now()
    from ready
    where q.id = ready.id
    returning q.id, q.signal_id, q.payload, q.attempts
  )
  select claimed.id, claimed.signal_id, claimed.payload, claimed.attempts
  from claimed;
end;
$$;

create or replace function public.complete_delivery_batch(
  p_queue_ids bigint[],
  p_worker_id uuid,
  p_receipt jsonb default '{}'::jsonb
) returns integer
language plpgsql
set search_path = ''
as $$
declare
  changed integer;
begin
  with completed as (
    update public.delivery_queue q
    set status = 'delivered', delivered_at = now(), receipt = p_receipt,
        worker_id = null, claimed_at = null, last_error = null, updated_at = now()
    where q.id = any(p_queue_ids)
      and q.status = 'sending'
      and q.worker_id = p_worker_id
    returning q.signal_id
  )
  update public.signals s
  set delivered_at = coalesce(s.delivered_at, now()), updated_at = now()
  where s.id in (select completed.signal_id from completed);
  get diagnostics changed = row_count;
  return changed;
end;
$$;

create or replace function public.fail_delivery_batch(
  p_queue_ids bigint[],
  p_worker_id uuid,
  p_error text
) returns integer
language plpgsql
set search_path = ''
as $$
declare
  changed integer;
begin
  update public.delivery_queue q
  set status = 'failed',
      last_error = left(coalesce(p_error, 'delivery failed'), 1000),
      next_attempt_at = now() + make_interval(secs => least(3600, 30 * (2 ^ least(q.attempts, 7))::integer)),
      worker_id = null,
      claimed_at = null,
      updated_at = now()
  where q.id = any(p_queue_ids)
    and q.status = 'sending'
    and q.worker_id = p_worker_id;
  get diagnostics changed = row_count;
  return changed;
end;
$$;

revoke all on function public.enqueue_delivery_signals(jsonb, timestamptz) from public, anon, authenticated;
revoke all on function public.upsert_signal_records(jsonb, boolean) from public, anon, authenticated;
revoke all on function public.claim_delivery_batch(text, integer, uuid, integer) from public, anon, authenticated;
revoke all on function public.complete_delivery_batch(bigint[], uuid, jsonb) from public, anon, authenticated;
revoke all on function public.fail_delivery_batch(bigint[], uuid, text) from public, anon, authenticated;
grant execute on function public.enqueue_delivery_signals(jsonb, timestamptz) to service_role;
grant execute on function public.upsert_signal_records(jsonb, boolean) to service_role;
grant execute on function public.claim_delivery_batch(text, integer, uuid, integer) to service_role;
grant execute on function public.complete_delivery_batch(bigint[], uuid, jsonb) to service_role;
grant execute on function public.fail_delivery_batch(bigint[], uuid, text) to service_role;
