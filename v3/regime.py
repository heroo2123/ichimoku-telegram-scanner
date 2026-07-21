from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from .models import RegimeSnapshot, utc_now_iso
from .storage import get_store


def _trend_component(frame: pd.DataFrame, scanner_module: Any) -> Dict[str, Any]:
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
    vol = float(returns.tail(20).std() * (252 ** 0.5)) if len(returns) >= 5 else 0.0
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


def build_us_regime(scanner_module: Any) -> RegimeSnapshot:
    symbols = ["^GSPC", "^NDX", "^RUT", "^VIX"]
    data = scanner_module.fetch_yfinance_batch(symbols)
    components: Dict[str, Any] = {}
    scores: List[float] = []
    for symbol in symbols:
        frame = data.get(symbol)
        if frame is None or len(frame) < scanner_module.minimum_daily_rows():
            continue
        component = _trend_component(frame, scanner_module)
        components[symbol] = component
        value = float(component["trend"])
        if symbol == "^VIX":
            value *= -0.5
        scores.append(value)
    score = sum(scores) / len(scores) if scores else 0.0
    vol_values = [v["annualized_vol"] for k, v in components.items() if k != "^VIX"]
    avg_vol = sum(vol_values) / len(vol_values) if vol_values else 0.0
    volatility = "high" if avg_vol > 0.30 else "low" if avg_vol < 0.15 else "normal"
    positive = sum(1 for value in scores if value > 0)
    breadth = "broad" if scores and positive / len(scores) >= 0.66 else "narrow" if positive else "negative"
    return RegimeSnapshot(utc_now_iso(), "us", _label(score), round(score, 3), volatility, breadth, components)


def build_crypto_regime(scanner_module: Any) -> RegimeSnapshot:
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    components: Dict[str, Any] = {}
    scores: List[float] = []
    for symbol in symbols:
        frame = scanner_module.fetch_binance_ohlcv(symbol, int(scanner_module.config.LOOKBACK_DAYS))
        if frame is None or len(frame) < scanner_module.minimum_daily_rows():
            continue
        component = _trend_component(frame, scanner_module)
        components[symbol] = component
        scores.append(float(component["trend"]))
    score = sum(scores) / len(scores) if scores else 0.0
    vols = [v["annualized_vol"] for v in components.values()]
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    volatility = "high" if avg_vol > 0.80 else "low" if avg_vol < 0.40 else "normal"
    positive = sum(1 for value in scores if value > 0)
    breadth = "broad" if scores and positive / len(scores) >= 0.66 else "narrow" if positive else "negative"
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
