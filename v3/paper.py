from __future__ import annotations

from typing import Any, Dict, List

from .models import utc_now_iso
from .settings import settings
from .storage import get_store

GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


def _default_state() -> Dict[str, Any]:
    return {
        "starting_equity": settings.paper_starting_equity,
        "cash": settings.paper_starting_equity,
        "equity": settings.paper_starting_equity,
        "positions": {},
        "closed_trades": [],
        "updated_at": utc_now_iso(),
    }


def _position_size(signal: Dict[str, Any], equity: float) -> float:
    risk_plan = dict(signal.get("risk_plan") or {})
    entry = float(signal.get("close") or 0.0)
    invalidation = risk_plan.get("invalidation")
    if not entry or invalidation is None:
        return 0.0
    distance = abs(entry - float(invalidation))
    if distance <= 0:
        return 0.0
    risk_budget = equity * settings.paper_risk_per_trade_pct / 100.0
    return risk_budget / distance


def update_paper_portfolio(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    store = get_store()
    state = store.load_paper_state() or _default_state()
    state.setdefault("positions", {})
    state.setdefault("closed_trades", [])
    equity = float(state.get("equity") or settings.paper_starting_equity)
    min_rank = GRADE_RANK.get(settings.paper_min_grade, 3)
    cluster_counts: Dict[str, int] = {}
    for position in state["positions"].values():
        cluster_counts[position.get("cluster", "unknown")] = cluster_counts.get(position.get("cluster", "unknown"), 0) + 1
    by_id = {str(signal["id"]): signal for signal in signals}
    for signal_id, position in list(state["positions"].items()):
        current = by_id.get(signal_id)
        if current and current.get("status") in {"invalidated", "completed"}:
            exit_price = float(current.get("close") or position["entry_price"])
            direction = position["direction"]
            raw = (exit_price / position["entry_price"] - 1.0) * 100.0 if position["entry_price"] else 0.0
            pnl_pct = raw if direction == "bullish" else -raw
            closed = {**position, "exit_price": exit_price, "exit_reason": current.get("status"), "closed_at": utc_now_iso(), "pnl_pct": round(pnl_pct, 4)}
            state["closed_trades"].append(closed)
            state["cash"] = float(state.get("cash", 0.0)) + position["units"] * position["entry_price"] * pnl_pct / 100.0
            del state["positions"][signal_id]
    ranked = sorted(signals, key=lambda row: (row.get("score", 0), row.get("weekly_alignment") == "aligned"), reverse=True)
    for signal in ranked:
        if len(state["positions"]) >= settings.paper_max_positions:
            break
        if str(signal["id"]) in state["positions"]:
            continue
        if GRADE_RANK.get(str(signal.get("grade", "D")), 1) < min_rank:
            continue
        if signal.get("status") not in {"confirmed", "entry_zone", "active"}:
            continue
        cluster = str(signal.get("cluster") or "unknown")
        if cluster_counts.get(cluster, 0) >= settings.paper_max_cluster_positions:
            continue
        units = _position_size(signal, equity)
        if units <= 0:
            continue
        state["positions"][str(signal["id"])] = {
            "signal_id": signal["id"],
            "symbol": signal["symbol"],
            "market": signal["market"],
            "direction": signal["direction"],
            "cluster": cluster,
            "entry_price": float(signal["close"]),
            "invalidation": (signal.get("risk_plan") or {}).get("invalidation"),
            "units": round(units, 8),
            "opened_at": utc_now_iso(),
            "grade": signal.get("grade"),
            "score": signal.get("score"),
        }
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
    state["closed_trades"] = state["closed_trades"][-1000:]
    state["updated_at"] = utc_now_iso()
    state["equity"] = float(state.get("cash") or settings.paper_starting_equity)
    store.save_paper_state(state)
    return state
