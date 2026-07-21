from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .ingest import normalize_candidate
from .settings import settings
from .storage import SupabaseStore, get_store


def next_delivery_time(now: Optional[datetime] = None) -> str:
    """Return the next 12:00 UTC delivery boundary (15:00 Kuwait)."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    boundary = current.replace(hour=12, minute=0, second=0, microsecond=0)
    if current >= boundary:
        boundary += timedelta(days=1)
    return boundary.isoformat()


def market_group(market: str) -> str:
    return "crypto" if market == "Crypto Spot" else "us"


def _queue_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_candidate(raw).to_dict()
    return {
        "signal": normalized,
        "payload": raw,
        "market_group": market_group(str(raw.get("market") or "")),
    }


@dataclass
class QueueClaim:
    store: SupabaseStore
    worker_id: str
    rows: List[Dict[str, Any]]

    @property
    def candidates(self) -> List[Dict[str, Any]]:
        return [dict(row.get("payload") or {}) for row in self.rows]

    def _queue_ids(self, signal_ids: Sequence[str]) -> List[int]:
        selected = set(signal_ids)
        return [int(row["queue_id"]) for row in self.rows if str(row.get("signal_id")) in selected]

    def complete(self, signal_ids: Sequence[str], receipt: Optional[Dict[str, Any]] = None) -> None:
        queue_ids = self._queue_ids(signal_ids)
        if not queue_ids:
            return
        self.store._request(
            "POST",
            "rpc/complete_delivery_batch",
            json={
                "p_queue_ids": queue_ids,
                "p_worker_id": self.worker_id,
                "p_receipt": receipt or {},
            },
        )

    def fail(self, signal_ids: Sequence[str], error: str) -> None:
        queue_ids = self._queue_ids(signal_ids)
        if not queue_ids:
            return
        self.store._request(
            "POST",
            "rpc/fail_delivery_batch",
            json={
                "p_queue_ids": queue_ids,
                "p_worker_id": self.worker_id,
                "p_error": str(error)[:1000],
            },
        )


class DatabaseDeliveryQueue:
    def __init__(self, store: Optional[SupabaseStore] = None):
        selected = store or get_store()
        if not isinstance(selected, SupabaseStore):
            raise RuntimeError("Supabase delivery queue is not configured")
        self.store = selected

    def enqueue(self, candidates: Iterable[Dict[str, Any]], scheduled_for: Optional[str] = None) -> int:
        items = [_queue_item(dict(candidate)) for candidate in candidates]
        if not items:
            return 0
        result = self.store._request(
            "POST",
            "rpc/enqueue_delivery_signals",
            json={
                "p_items": items,
                "p_scheduled_for": scheduled_for or next_delivery_time(),
            },
        )
        if isinstance(result, list) and result:
            return int(result[0].get("enqueue_delivery_signals", result[0].get("count", len(items))))
        return int(result or len(items))

    def claim(self, group: str = "all", limit: Optional[int] = None) -> QueueClaim:
        worker_id = str(uuid.uuid4())
        result = self.store._request(
            "POST",
            "rpc/claim_delivery_batch",
            json={
                "p_market_group": group,
                "p_limit": int(limit or settings.delivery_claim_limit),
                "p_worker_id": worker_id,
                "p_claim_timeout_minutes": int(settings.delivery_claim_timeout_minutes),
            },
        )
        return QueueClaim(self.store, worker_id, list(result or []))

    def delivered_signal_ids(self, signal_ids: Sequence[str]) -> set[str]:
        delivered: set[str] = set()
        values = [str(value) for value in signal_ids if value]
        for start in range(0, len(values), 40):
            batch = values[start:start + 40]
            quoted = ",".join(f'"{value.replace(chr(34), "")}"' for value in batch)
            rows = self.store._request(
                "GET",
                "signals",
                params={
                    "select": "id",
                    "id": f"in.({quoted})",
                    "delivered_at": "not.is.null",
                },
            )
            delivered.update(str(row["id"]) for row in rows or [])
        return delivered


def get_delivery_queue() -> Optional[DatabaseDeliveryQueue]:
    if not settings.database_delivery_enabled:
        return None
    try:
        return DatabaseDeliveryQueue()
    except Exception as exc:
        print(f"Warning: Supabase delivery queue unavailable: {exc}")
        return None
