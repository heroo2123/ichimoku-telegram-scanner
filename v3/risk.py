from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from .models import RiskPlan


def _num(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def classify_cluster(market: str, symbol: str, name: str = "") -> str:
    upper = f"{symbol} {name}".upper()
    if market == "Crypto Spot":
        if symbol.startswith("BTC") or symbol.endswith("BTC"):
            return "crypto-btc"
        if symbol.startswith("ETH") or symbol.endswith("ETH"):
            return "crypto-eth"
        if symbol.startswith("BNB") or symbol.endswith("BNB"):
            return "crypto-bnb"
        if any(token in upper for token in ("AI", "FET", "RENDER", "TAO", "AGIX")):
            return "crypto-ai"
        if any(token in upper for token in ("MEME", "DOGE", "SHIB", "PEPE", "BONK", "FLOKI")):
            return "crypto-meme"
        return "crypto-alt"
    if market == "Commodity Future":
        if symbol in {"CL=F", "BZ=F", "NG=F", "HO=F", "RB=F"}:
            return "commodity-energy"
        if symbol in {"GC=F", "SI=F", "HG=F", "PL=F", "PA=F"}:
            return "commodity-metals"
        return "commodity-agriculture"
    if market == "US Index":
        return "us-index"
    sector_hints = {
        "semiconductor": ("SEMICONDUCTOR", "CHIP", "NVIDIA", "AMD", "BROADCOM"),
        "biotech": ("BIOTECH", "THERAPEUTICS", "PHARMA", "BIOSCIENCE"),
        "financial": ("BANK", "FINANCIAL", "CAPITAL", "INSURANCE"),
        "energy": ("ENERGY", "OIL", "GAS", "PETROLEUM"),
        "software": ("SOFTWARE", "CLOUD", "SAAS", "CYBER"),
        "retail": ("RETAIL", "CONSUMER", "STORE"),
    }
    for label, hints in sector_hints.items():
        if any(hint in upper for hint in hints):
            return f"us-{label}"
    return "us-other"


def build_risk_plan(candidate: Dict[str, Any]) -> RiskPlan:
    direction = str(candidate.get("direction", "bullish"))
    close = _num(candidate.get("close"))
    metrics = dict(candidate.get("metrics") or {})
    atr = _num(metrics.get("atr"))
    kijun = _num(metrics.get("kijun"))
    cloud_top = _num(metrics.get("cloud_top"))
    cloud_bottom = _num(metrics.get("cloud_bottom"))
    notes: List[str] = []
    if close is None:
        return RiskPlan(None, None, None, None, None, None, ["Price unavailable"])
    if atr is None or atr <= 0:
        atr = abs(close) * 0.02
        notes.append("ATR unavailable; used a 2% price proxy")
    anchors = [value for value in (kijun, cloud_top, cloud_bottom) if value is not None]
    anchor = kijun if kijun is not None else (sum(anchors) / len(anchors) if anchors else close)
    zone_half = 0.35 * atr
    entry_low = min(close, anchor) - zone_half
    entry_high = max(close, anchor) + zone_half
    if direction == "bullish":
        structural = min([value for value in (kijun, cloud_bottom) if value is not None] or [close - atr])
        invalidation = structural - 0.25 * atr
        reward_reference = close + 2.0 * max(close - invalidation, atr)
    else:
        structural = max([value for value in (kijun, cloud_top) if value is not None] or [close + atr])
        invalidation = structural + 0.25 * atr
        reward_reference = close - 2.0 * max(invalidation - close, atr)
    distance = abs(close - invalidation)
    stop_pct = distance / abs(close) * 100 if close else None
    units = 1000.0 / distance if distance > 0 else None
    extension = _num(metrics.get("kijun_distance_atr"))
    if extension is not None and extension >= 2.5:
        notes.append("Extended from Kijun; prefer a pullback instead of chasing")
    if candidate.get("weekly_alignment") == "opposed":
        notes.append("Weekly trend is opposed; reduce risk or skip")
    if _num(metrics.get("cloud_thickness_atr")) is not None and float(metrics["cloud_thickness_atr"]) < 0.1:
        notes.append("Cloud is unusually thin")
    return RiskPlan(
        round(entry_low, 8),
        round(entry_high, 8),
        round(invalidation, 8),
        round(stop_pct, 3) if stop_pct is not None else None,
        round(reward_reference, 8),
        round(units, 6) if units is not None else None,
        notes,
    )


def correlation_warnings(signals: Iterable[Dict[str, Any]], max_per_cluster: int = 3) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for signal in signals:
        cluster = str(signal.get("cluster") or classify_cluster(str(signal.get("market", "")), str(signal.get("symbol", "")), str(signal.get("name", ""))))
        grouped.setdefault(cluster, []).append(str(signal.get("symbol", "")))
    return {
        cluster: symbols
        for cluster, symbols in grouped.items()
        if len(symbols) > max_per_cluster
    }
