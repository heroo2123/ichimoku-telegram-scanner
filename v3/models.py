from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RiskPlan:
    entry_low: Optional[float]
    entry_high: Optional[float]
    invalidation: Optional[float]
    stop_distance_pct: Optional[float]
    reward_reference: Optional[float]
    suggested_units_per_1000_risk: Optional[float]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SignalRecord:
    id: str
    market: str
    symbol: str
    name: str
    direction: str
    signal_type: str
    signal_date: str
    close: float
    score: int
    grade: str
    weekly_alignment: str
    status: str
    reasons: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]
    risk_plan: Dict[str, Any]
    cluster: str
    detected_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    delivered_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeSnapshot:
    as_of: str
    market: str
    regime: str
    score: float
    volatility: str
    breadth: str
    components: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    run_id: str
    market: str
    symbol: str
    started_at: str
    completed_at: str
    parameters: Dict[str, Any]
    trades: List[Dict[str, Any]]
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
