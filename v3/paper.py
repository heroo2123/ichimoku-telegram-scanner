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
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "gross_exposure": 0.0,
        "fees_paid": 0.0,
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
    risk_units = risk_budget / distance
    notional_cap = equity * settings.paper_max_position_pct / 100.0
    notional_units = notional_cap / entry if entry > 0 else 0.0
    return min(risk_units, notional_units)


def _execution_price(price: float, direction: str, *, opening: bool) -> float:
    slip = settings.paper_slippage_bps / 10_000.0
    if direction == "bullish":
        return price * (1.0 + slip if opening else 1.0 - slip)
    return price * (1.0 - slip if opening else 1.0 + slip)


def _pnl(position: Dict[str, Any], price: float) -> float:
    entry = float(position["entry_price"])
    units = float(position["units"])
    raw = (price - entry) * units
    return raw if position["direction"] == "bullish" else -raw


def update_paper_portfolio(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    store = get_store()
    state = store.load_paper_state() or _default_state()
    state.setdefault("positions", {})
    state.setdefault("closed_trades", [])
    state.setdefault("fees_paid", 0.0)
    state.setdefault("realized_pnl", 0.0)
    equity = float(state.get("equity") or settings.paper_starting_equity)
    min_rank = GRADE_RANK.get(settings.paper_min_grade, 3)
    by_id = {str(signal["id"]): signal for signal in signals}
    for signal_id, position in list(state["positions"].items()):
        current = by_id.get(signal_id)
        if current:
            current_price = float(current.get("close") or position["entry_price"])
            position["last_price"] = current_price
            invalidation = position.get("invalidation")
            stopped = invalidation is not None and (
                current_price <= float(invalidation)
                if position["direction"] == "bullish"
                else current_price >= float(invalidation)
            )
        else:
            current_price = float(position.get("last_price") or position["entry_price"])
            stopped = False
        if current and (current.get("status") in {"invalidated", "completed"} or stopped):
            exit_price = _execution_price(current_price, position["direction"], opening=False)
            direction = position["direction"]
            pnl_value = _pnl(position, exit_price)
            entry_notional = float(position["entry_price"]) * float(position["units"])
            exit_fee = abs(exit_price * float(position["units"])) * settings.paper_fee_bps / 10_000.0
            net_pnl = pnl_value - exit_fee
            pnl_pct = net_pnl / entry_notional * 100.0 if entry_notional else 0.0
            exit_reason = "invalidation" if stopped else current.get("status")
            closed = {**position, "exit_price": round(exit_price, 8), "exit_reason": exit_reason, "closed_at": utc_now_iso(), "pnl": round(net_pnl, 4), "pnl_pct": round(pnl_pct, 4), "exit_fee": round(exit_fee, 4)}
            state["closed_trades"].append(closed)
            state["cash"] = float(state.get("cash", settings.paper_starting_equity)) + net_pnl
            state["realized_pnl"] = float(state.get("realized_pnl", 0.0)) + net_pnl
            state["fees_paid"] = float(state.get("fees_paid", 0.0)) + exit_fee
            del state["positions"][signal_id]
    cluster_counts: Dict[str, int] = {}
    for position in state["positions"].values():
        cluster = str(position.get("cluster", "unknown"))
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
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
        direction = str(signal["direction"])
        entry_price = _execution_price(float(signal["close"]), direction, opening=True)
        entry_fee = abs(entry_price * units) * settings.paper_fee_bps / 10_000.0
        state["positions"][str(signal["id"])] = {
            "signal_id": signal["id"],
            "symbol": signal["symbol"],
            "market": signal["market"],
            "direction": direction,
            "cluster": cluster,
            "entry_price": round(entry_price, 8),
            "last_price": float(signal["close"]),
            "invalidation": (signal.get("risk_plan") or {}).get("invalidation"),
            "units": round(units, 8),
            "opened_at": utc_now_iso(),
            "grade": signal.get("grade"),
            "score": signal.get("score"),
            "entry_fee": round(entry_fee, 4),
        }
        state["cash"] = float(state.get("cash", settings.paper_starting_equity)) - entry_fee
        state["fees_paid"] = float(state.get("fees_paid", 0.0)) + entry_fee
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
    state["closed_trades"] = state["closed_trades"][-1000:]
    state["updated_at"] = utc_now_iso()
    unrealized = 0.0
    gross_exposure = 0.0
    for position in state["positions"].values():
        last_price = float(position.get("last_price") or position["entry_price"])
        unrealized += _pnl(position, last_price)
        gross_exposure += abs(last_price * float(position["units"]))
    state["unrealized_pnl"] = round(unrealized, 4)
    state["gross_exposure"] = round(gross_exposure, 4)
    state["equity"] = round(float(state.get("cash", settings.paper_starting_equity)) + unrealized, 4)
    store.save_paper_state(state)
    return state
