-- Ichimoku Scanner V3 server-only schema.
-- The dashboard and GitHub Actions use the service-role key on trusted servers.
-- anon/authenticated receive no table privileges in this migration.

create table if not exists public.signals (
  id text primary key,
  market text not null,
  symbol text not null,
  name text not null default '',
  direction text not null check (direction in ('bullish','bearish','unknown')),
  signal_type text not null,
  signal_date date not null,
  close numeric not null,
  score integer not null check (score between 0 and 10),
  grade text not null check (grade in ('A','B','C','D')),
  weekly_alignment text not null default 'unknown',
  status text not null check (status in ('detected','confirmed','entry_zone','extended','active','invalidated','completed')),
  reasons jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  metrics jsonb not null default '{}'::jsonb,
  risk_plan jsonb not null default '{}'::jsonb,
  cluster text not null default 'unknown',
  detected_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  delivered_at timestamptz
);

create index if not exists signals_date_score_idx on public.signals (signal_date desc, score desc);
create index if not exists signals_status_idx on public.signals (status, signal_date desc);
create index if not exists signals_market_symbol_idx on public.signals (market, symbol);
create index if not exists signals_cluster_idx on public.signals (cluster, status);

create table if not exists public.signal_events (
  id bigint generated always as identity primary key,
  signal_id text not null references public.signals(id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists signal_events_signal_idx on public.signal_events (signal_id, created_at desc);

create table if not exists public.market_regimes (
  id bigint generated always as identity primary key,
  as_of timestamptz not null,
  market text not null,
  regime text not null,
  score double precision not null,
  volatility text not null,
  breadth text not null,
  components jsonb not null default '{}'::jsonb
);
create index if not exists market_regimes_market_time_idx on public.market_regimes (market, as_of desc);

create table if not exists public.backtest_runs (
  run_id uuid primary key,
  market text not null,
  symbol text not null,
  started_at timestamptz not null,
  completed_at timestamptz not null,
  parameters jsonb not null default '{}'::jsonb,
  trades jsonb not null default '[]'::jsonb,
  summary jsonb not null default '{}'::jsonb
);
create index if not exists backtest_runs_symbol_time_idx on public.backtest_runs (market, symbol, completed_at desc);

create table if not exists public.paper_accounts (
  account_key text primary key,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.delivery_queue (
  id bigint generated always as identity primary key,
  signal_id text not null references public.signals(id) on delete cascade,
  channel text not null default 'telegram',
  status text not null default 'pending' check (status in ('pending','sending','delivered','failed','cancelled')),
  attempts integer not null default 0,
  scheduled_for timestamptz,
  delivered_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(signal_id, channel)
);
create index if not exists delivery_queue_ready_idx on public.delivery_queue (status, scheduled_for);

create table if not exists public.model_calibrations (
  id bigint generated always as identity primary key,
  market text not null,
  direction text not null,
  horizon integer not null,
  trained_at timestamptz not null default now(),
  model jsonb not null,
  metrics jsonb not null default '{}'::jsonb
);

create table if not exists public.user_settings (
  setting_key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.signals enable row level security;
alter table public.signal_events enable row level security;
alter table public.market_regimes enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.paper_accounts enable row level security;
alter table public.delivery_queue enable row level security;
alter table public.model_calibrations enable row level security;
alter table public.user_settings enable row level security;

revoke all on table public.signals from anon, authenticated;
revoke all on table public.signal_events from anon, authenticated;
revoke all on table public.market_regimes from anon, authenticated;
revoke all on table public.backtest_runs from anon, authenticated;
revoke all on table public.paper_accounts from anon, authenticated;
revoke all on table public.delivery_queue from anon, authenticated;
revoke all on table public.model_calibrations from anon, authenticated;
revoke all on table public.user_settings from anon, authenticated;

grant all on table public.signals to service_role;
grant all on table public.signal_events to service_role;
grant all on table public.market_regimes to service_role;
grant all on table public.backtest_runs to service_role;
grant all on table public.paper_accounts to service_role;
grant all on table public.delivery_queue to service_role;
grant all on table public.model_calibrations to service_role;
grant all on table public.user_settings to service_role;
grant usage, select on all sequences in schema public to service_role;
