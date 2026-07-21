from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .models import RegimeSnapshot, utc_now_iso
from .storage import get_store


SUMMARY_PATH = Path(__file__).resolve().parents[1] / "data" / "last_run_summary.json"


def _trend_component(frame: pd.DataFrame, scanner_module: Any, sessions_per_year: int = 252) -> Dict[str, Any]:
    enriched = scanner_module.add_ichimoku(frame)
    row = enriched.iloc[-1]
    close = float(row["Close"])
    top = float(row["CloudTop"]) if pd.notna(row["CloudTop"]) else close
    bottom = float(row["CloudBottom"]) if pd.notna(row["CloudBottom"]) else close
    kijun = float(row["Kijun"]) if pd.notna(row["Kijun"]) else close
    if close > top and close > kijun:
        trend = 1
    elif close < bottom and close < kijun:
        trend = -1
    else:
        trend = 0
    returns = frame["Close"].pct_change().dropna()
    vol = float(returns.tail(20).std() * (sessions_per_year ** 0.5)) if len(returns) >= 5 else 0.0
    return {"trend": trend, "close": close, "cloud_top": top, "cloud_bottom": bottom, "kijun": kijun, "annualized_vol": vol}


def _label(score: float) -> str:
    if score >= 0.6:
        return "strong_bull"
    if score >= 0.2:
        return "bull"
    if score <= -0.6:
        return "strong_bear"
    if score <= -0.2:
        return "bear"
    return "sideways"


def _universe_breadth(market: str) -> Dict[str, Any]:
    try:
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    payload = dict(summary.get(market) or {})
    scan = dict(payload.get("last_scan") or payload)
    stats = dict(scan.get("stats") or {})
    total = int(stats.get("breadth_total") or 0)
    above = int(stats.get("breadth_above_cloud") or 0)
    below = int(stats.get("breadth_below_cloud") or 0)
    return {
        "total": total,
        "above": above,
        "below": below,
        "above_pct": round(above / total * 100.0, 2) if total else None,
        "below_pct": round(below / total * 100.0, 2) if total else None,
        "score": (above - below) / total if total else None,
    }


def _breadth_label(breadth: Dict[str, Any], fallback_scores: List[float]) -> str:
    score = breadth.get("score")
    if score is None:
        positive = sum(1 for value in fallback_scores if value > 0)
        return "broad" if fallback_scores and positive / len(fallback_scores) >= 0.66 else "narrow" if positive else "negative"
    if score >= 0.25:
        return "broad"
    if score <= -0.25:
        return "negative"
    return "mixed"


def build_us_regime(scanner_module: Any) -> RegimeSnapshot:
    symbols = ["^GSPC", "^NDX", "^RUT", "^VIX"]
    data = scanner_module.fetch_yfinance_batch(symbols)
    components: Dict[str, Any] = {}
    scores: List[float] = []
    for symbol in symbols:
        frame = data.get(symbol)
        if frame is None or len(frame) < scanner_module.minimum_daily_rows():
            continue
        component = _trend_component(frame, scanner_module, 252)
        components[symbol] = component
        value = float(component["trend"])
        if symbol == "^VIX":
            value *= -0.5
        scores.append(value)
    trend_score = sum(scores) / len(scores) if scores else 0.0
    universe = _universe_breadth("us")
    breadth_score = universe.get("score")
    score = 0.7 * trend_score + 0.3 * float(breadth_score) if breadth_score is not None else trend_score
    components["universe_breadth"] = universe
    vol_values = [float(v["annualized_vol"]) for k, v in components.items() if k != "^VIX" and v.get("annualized_vol") is not None]
    avg_vol = sum(vol_values) / len(vol_values) if vol_values else 0.0
    volatility = "high" if avg_vol > 0.30 else "low" if avg_vol < 0.15 else "normal"
    breadth = _breadth_label(universe, scores)
    return RegimeSnapshot(utc_now_iso(), "us", _label(score), round(score, 3), volatility, breadth, components)


def build_crypto_regime(scanner_module: Any) -> RegimeSnapshot:
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    components: Dict[str, Any] = {}
    scores: List[float] = []
    for symbol in symbols:
        frame = scanner_module.fetch_binance_ohlcv(symbol, int(scanner_module.config.LOOKBACK_DAYS))
        if frame is None or len(frame) < scanner_module.minimum_daily_rows():
            continue
        component = _trend_component(frame, scanner_module, 365)
        components[symbol] = component
        scores.append(float(component["trend"]))
    trend_score = sum(scores) / len(scores) if scores else 0.0
    universe = _universe_breadth("crypto")
    breadth_score = universe.get("score")
    score = 0.7 * trend_score + 0.3 * float(breadth_score) if breadth_score is not None else trend_score
    components["universe_breadth"] = universe
    vols = [float(v["annualized_vol"]) for v in components.values() if v.get("annualized_vol") is not None]
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    volatility = "high" if avg_vol > 0.80 else "low" if avg_vol < 0.40 else "normal"
    breadth = _breadth_label(universe, scores)
    return RegimeSnapshot(utc_now_iso(), "crypto", _label(score), round(score, 3), volatility, breadth, components)


def refresh_regimes(scanner_module: Any, market: str = "all") -> List[Dict[str, Any]]:
    snapshots: List[RegimeSnapshot] = []
    if market in {"all", "crypto"}:
        snapshots.append(build_crypto_regime(scanner_module))
    if market in {"all", "us"}:
        snapshots.append(build_us_regime(scanner_module))
    store = get_store()
    rows = [snapshot.to_dict() for snapshot in snapshots]
    for row in rows:
        store.save_regime(row)
    return rows
