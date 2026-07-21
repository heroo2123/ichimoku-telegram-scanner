from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from .settings import settings


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def initial_status(candidate: Dict[str, Any]) -> str:
    metrics = dict(candidate.get("metrics") or {})
    extension = _float(metrics.get("kijun_distance_atr"))
    if extension is not None and extension >= settings.lifecycle_extended_atr:
        return "extended"
    if extension is not None and extension <= settings.lifecycle_entry_zone_atr:
        return "entry_zone"
    return "confirmed"


def sessions_since(signal_date: str, as_of: Optional[str] = None) -> int:
    try:
        start = date.fromisoformat(signal_date[:10])
        end = date.fromisoformat((as_of or datetime.utcnow().date().isoformat())[:10])
        return max(0, (end - start).days)
    except (TypeError, ValueError):
        return 0


def evaluate_status(signal: Dict[str, Any], current: Dict[str, Any]) -> str:
    direction = str(signal.get("direction", "bullish"))
    close = _float(current.get("close"))
    kijun = _float(current.get("kijun"))
    cloud_top = _float(current.get("cloud_top"))
    cloud_bottom = _float(current.get("cloud_bottom"))
    atr = _float(current.get("atr"))
    if close is None:
        return str(signal.get("status") or "confirmed")
    if direction == "bullish":
        if cloud_bottom is not None and close < cloud_bottom:
            return "invalidated"
        if kijun is not None and close < kijun:
            return "invalidated"
    else:
        if cloud_top is not None and close > cloud_top:
            return "invalidated"
        if kijun is not None and close > kijun:
            return "invalidated"
    if sessions_since(str(signal.get("signal_date", "")), str(current.get("date", ""))) >= settings.lifecycle_complete_sessions:
        return "completed"
    if atr and kijun is not None:
        distance = abs(close - kijun) / atr
        if distance >= settings.lifecycle_extended_atr:
            return "extended"
        if distance <= settings.lifecycle_entry_zone_atr:
            return "entry_zone"
    return "active"
