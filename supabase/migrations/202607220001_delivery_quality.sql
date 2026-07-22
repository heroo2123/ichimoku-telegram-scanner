-- Reconcile terminal lifecycle states before Telegram delivery without
-- deleting history or falsely marking skipped signals as delivered.

create or replace function public.cancel_delivery_batch(
  p_queue_ids bigint[],
  p_worker_id uuid,
  p_reason text default 'cancelled before delivery'
)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  changed integer;
begin
  update public.delivery_queue q
  set status = 'cancelled',
      receipt = coalesce(q.receipt, '{}'::jsonb) || jsonb_build_object(
        'cancelled', true,
        'reason', left(coalesce(p_reason, 'cancelled before delivery'), 500),
        'cancelled_at', now()
      ),
      worker_id = null,
      claimed_at = null,
      next_attempt_at = null,
      last_error = null,
      updated_at = now()
  where q.id = any(p_queue_ids)
    and q.status = 'sending'
    and q.worker_id = p_worker_id;

  get diagnostics changed = row_count;
  return changed;
end;
$$;

revoke execute on function public.cancel_delivery_batch(bigint[], uuid, text) from public;
revoke execute on function public.cancel_delivery_batch(bigint[], uuid, text) from anon, authenticated;
grant execute on function public.cancel_delivery_batch(bigint[], uuid, text) to service_role;

-- V3.1 originally refreshed lifecycle state on the signal candle and could
-- invalidate a setup before a later completed candle existed. Restore only
-- those same-candle rows to their initial lifecycle state.
update public.signals
set status = case
      when coalesce(metrics->>'kijun_distance_atr', '') ~ '^[0-9]+([.][0-9]+)?$'
           and (metrics->>'kijun_distance_atr')::numeric >= 2.5 then 'extended'
      when coalesce(metrics->>'kijun_distance_atr', '') ~ '^[0-9]+([.][0-9]+)?$'
           and (metrics->>'kijun_distance_atr')::numeric <= 0.75 then 'entry_zone'
      else 'confirmed'
    end,
    updated_at = now()
where status = 'invalidated'
  and coalesce(metrics->>'current_date', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  and signal_date = (metrics->>'current_date')::date;
