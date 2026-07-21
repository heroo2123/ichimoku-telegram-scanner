from __future__ import annotations

from typing import Any, Dict, List

from .lifecycle import evaluate_status
from .storage import get_store


def _context_from_frame(scanner_module: Any, frame: Any) -> Dict[str, Any] | None:
    if frame is None or len(frame) < scanner_module.minimum_daily_rows():
        return None
    enriched = scanner_module.add_ichimoku(frame)
    row = enriched.iloc[-1]
    return {
        "date": str(enriched.index[-1].date()),
        "open": float(row["Open"]),
        "close": float(row["Close"]),
        "kijun": float(row["Kijun"]) if row["Kijun"] == row["Kijun"] else None,
        "cloud_top": float(row["CloudTop"]) if row["CloudTop"] == row["CloudTop"] else None,
        "cloud_bottom": float(row["CloudBottom"]) if row["CloudBottom"] == row["CloudBottom"] else None,
        "atr": float(row["ATR"]) if row["ATR"] == row["ATR"] else None,
    }


def refresh_lifecycle(scanner_module: Any, limit: int = 50) -> List[Dict[str, Any]]:
    store = get_store()
    signals = [
        signal for signal in store.list_signals(limit=limit)
        if signal.get("status") not in {"invalidated", "completed"}
    ]
    contexts: Dict[tuple[str, str], Dict[str, Any]] = {}
    crypto_symbols = sorted({str(signal.get("symbol")) for signal in signals if signal.get("market") == "Crypto Spot"})
    for symbol in crypto_symbols:
        try:
            frame = scanner_module.fetch_binance_ohlcv(symbol, int(scanner_module.config.LOOKBACK_DAYS))
            context = _context_from_frame(scanner_module, frame)
            if context:
                contexts[("Crypto Spot", symbol)] = context
        except Exception as exc:
            print(f"Lifecycle refresh failed to fetch {symbol}: {exc}")
    for market in ("US Stock", "US Index", "Commodity Future"):
        symbols = sorted({str(signal.get("symbol")) for signal in signals if signal.get("market") == market})
        for batch in scanner_module.chunks(symbols, int(scanner_module.config.YFINANCE_BATCH_SIZE)):
            try:
                frames = scanner_module.fetch_yfinance_batch(batch)
                for symbol, frame in frames.items():
                    context = _context_from_frame(scanner_module, frame)
                    if context:
                        contexts[(market, symbol)] = context
            except Exception as exc:
                print(f"Lifecycle refresh failed to fetch {market} batch: {exc}")
    updated: List[Dict[str, Any]] = []
    for signal in signals:
        try:
            context = contexts.get((str(signal.get("market")), str(signal.get("symbol"))))
            if not context:
                continue
            old = signal.get("status")
            new = evaluate_status(signal, context)
            signal["status"] = new
            signal["close"] = context["close"]
            signal.setdefault("metrics", {}).update({
                **{k: v for k, v in context.items() if k != "date"},
                "current_date": context["date"],
                "current_open": context["open"],
            })
            updated.append(signal)
            if new != old:
                store.record_event(signal["id"], "status_changed", {"from": old, "to": new, "context": context})
        except Exception as exc:
            print(f"Lifecycle refresh failed for {signal.get('symbol')}: {exc}")
    if updated:
        store.upsert_signals(updated)
    return updated
