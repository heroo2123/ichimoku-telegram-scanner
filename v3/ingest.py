from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .lifecycle import initial_status
from .models import SignalRecord, utc_now_iso
from .risk import build_risk_plan, classify_cluster, correlation_warnings
from .storage import get_store

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "data" / "last_run_summary.json"


def _load_summary() -> Dict[str, Any]:
    try:
        return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_candidate(raw: Dict[str, Any]) -> SignalRecord:
    market = str(raw.get("market") or "Unknown")
    symbol = str(raw.get("symbol") or "")
    direction = str(raw.get("direction") or raw.get("type") or "unknown")
    signal_type = str(raw.get("signal_type") or f"legacy_{direction}")
    signal_date = str(raw.get("date") or raw.get("signal_date") or "")
    signal_id = str(raw.get("id") or f"{market}|{symbol}|1D|{direction}|{signal_type}|{signal_date}")
    candidate = {
        **raw,
        "id": signal_id,
        "market": market,
        "symbol": symbol,
        "direction": direction,
        "signal_type": signal_type,
        "signal_date": signal_date,
        "close": float(raw.get("close") or 0.0),
        "score": int(raw.get("score") or 0),
        "grade": str(raw.get("grade") or "D"),
        "weekly_alignment": str(raw.get("weekly_alignment") or "unknown"),
        "metrics": dict(raw.get("metrics") or {}),
    }
    risk = build_risk_plan(candidate).to_dict()
    return SignalRecord(
        id=signal_id,
        market=market,
        symbol=symbol,
        name=str(raw.get("name") or symbol),
        direction=direction,
        signal_type=signal_type,
        signal_date=signal_date,
        close=float(candidate["close"]),
        score=int(candidate["score"]),
        grade=str(candidate["grade"]),
        weekly_alignment=str(candidate["weekly_alignment"]),
        status=initial_status(candidate),
        reasons=list(raw.get("reasons") or []),
        warnings=list(raw.get("warnings") or []),
        metrics=dict(candidate["metrics"]),
        risk_plan=risk,
        cluster=classify_cluster(market, symbol, str(raw.get("name") or symbol)),
        detected_at=str(raw.get("detected_at") or utc_now_iso()),
        updated_at=utc_now_iso(),
        delivered_at=raw.get("delivered_at"),
    )


def summary_candidates(market: str = "all") -> List[SignalRecord]:
    summary = _load_summary()
    keys = ["crypto", "us"] if market == "all" else [market]
    records: List[SignalRecord] = []
    for key in keys:
        payload = summary.get(key) or {}
        for raw in payload.get("alerts") or []:
            try:
                records.append(normalize_candidate(raw))
            except Exception as exc:
                print(f"Skipping malformed candidate: {exc}")
    return records


def ingest_summary(market: str = "all") -> List[Dict[str, Any]]:
    store = get_store()
    records = summary_candidates(market)
    rows = [record.to_dict() for record in records]
    existing_ids = {str(row.get("id")) for row in store.list_signals(limit=5000)}
    correlated = correlation_warnings(rows)
    for row in rows:
        cluster_symbols = correlated.get(row.get('cluster'))
        if cluster_symbols:
            row.setdefault('warnings', []).append(f"Correlation concentration: {', '.join(cluster_symbols[:8])}")
    store.upsert_signals(rows, preserve_lifecycle=True)
    for row in rows:
        if str(row["id"]) not in existing_ids:
            store.record_event(row["id"], "detected", {"status": row["status"], "score": row["score"], "grade": row["grade"]})
    return rows
