from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def lower_timeframe_confirmation(frame: pd.DataFrame, direction: str, scanner_module: Any) -> Dict[str, Any]:
    """Evaluate optional lower-timeframe timing using the same Ichimoku engine.

    The caller supplies completed 4h candles. This function never promotes an
    unconfirmed daily setup; it only labels entry timing for an existing setup.
    """
    enriched = scanner_module.add_ichimoku(frame)
    if len(enriched) < scanner_module.minimum_daily_rows():
        return {"status": "unknown", "reason": "insufficient_history"}
    current = scanner_module.ichimoku_context_at(enriched, -1, int(scanner_module.config.DISPLACEMENT))
    previous = scanner_module.ichimoku_context_at(enriched, -2, int(scanner_module.config.DISPLACEMENT))
    if not current or not previous:
        return {"status": "unknown", "reason": "context_unavailable"}
    if direction == "bullish":
        cross = current.get("tenkan") is not None and current.get("kijun") is not None and previous.get("tenkan") is not None and previous.get("kijun") is not None and current["tenkan"] > current["kijun"] and previous["tenkan"] <= previous["kijun"]
        pullback = current.get("low") is not None and current.get("kijun") is not None and current["low"] <= current["kijun"] < current["close"]
        invalid = current.get("cloudbottom") is not None and current["close"] < current["cloudbottom"]
    else:
        cross = current.get("tenkan") is not None and current.get("kijun") is not None and previous.get("tenkan") is not None and previous.get("kijun") is not None and current["tenkan"] < current["kijun"] and previous["tenkan"] >= previous["kijun"]
        pullback = current.get("high") is not None and current.get("kijun") is not None and current["high"] >= current["kijun"] > current["close"]
        invalid = current.get("cloudtop") is not None and current["close"] > current["cloudtop"]
    if invalid:
        return {"status": "invalidated", "reason": "lower_timeframe_cloud_failure"}
    if cross or pullback:
        return {"status": "confirmed", "reason": "tk_cross" if cross else "kijun_retest"}
    return {"status": "waiting", "reason": "no_entry_trigger"}
