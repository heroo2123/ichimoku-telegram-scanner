from __future__ import annotations

from typing import Any, Dict, List

from .lifecycle import evaluate_status
from .storage import get_store


def _latest_context(scanner_module: Any, signal: Dict[str, Any]) -> Dict[str, Any] | None:
    market = signal.get("market")
    symbol = str(signal.get("symbol"))
    if market == "Crypto Spot":
        frame = scanner_module.fetch_binance_ohlcv(symbol, int(scanner_module.config.LOOKBACK_DAYS))
    else:
        frame = scanner_module.fetch_yfinance_batch([symbol]).get(symbol)
    if frame is None or len(frame) < scanner_module.minimum_daily_rows():
        return None
    enriched = scanner_module.add_ichimoku(frame)
    row = enriched.iloc[-1]
    return {
        "date": str(enriched.index[-1].date()),
        "close": float(row["Close"]),
        "kijun": float(row["Kijun"]) if row["Kijun"] == row["Kijun"] else None,
        "cloud_top": float(row["CloudTop"]) if row["CloudTop"] == row["CloudTop"] else None,
        "cloud_bottom": float(row["CloudBottom"]) if row["CloudBottom"] == row["CloudBottom"] else None,
        "atr": float(row["ATR"]) if row["ATR"] == row["ATR"] else None,
    }


def refresh_lifecycle(scanner_module: Any, limit: int = 50) -> List[Dict[str, Any]]:
    store = get_store()
    signals = store.list_signals(limit=limit)
    updated: List[Dict[str, Any]] = []
    for signal in signals:
        if signal.get("status") in {"invalidated", "completed"}:
            continue
        try:
            context = _latest_context(scanner_module, signal)
            if not context:
                continue
            old = signal.get("status")
            new = evaluate_status(signal, context)
            signal["status"] = new
            signal["close"] = context["close"]
            signal.setdefault("metrics", {}).update({k: v for k, v in context.items() if k != "date"})
            updated.append(signal)
            if new != old:
                store.record_event(signal["id"], "status_changed", {"from": old, "to": new, "context": context})
        except Exception as exc:
            print(f"Lifecycle refresh failed for {signal.get('symbol')}: {exc}")
    if updated:
        store.upsert_signals(updated)
    return updated
