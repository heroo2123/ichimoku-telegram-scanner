from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd


def validate_ohlcv(frame: pd.DataFrame, *, minimum_rows: int = 50) -> Tuple[bool, List[str], Dict[str, Any]]:
    issues: List[str] = []
    meta: Dict[str, Any] = {"rows": len(frame)}
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        issues.append(f"Missing columns: {', '.join(missing)}")
        return False, issues, meta
    if len(frame) < minimum_rows:
        issues.append(f"Insufficient history: {len(frame)} rows")
    if not frame.index.is_monotonic_increasing:
        issues.append("Timestamps are not increasing")
    duplicate_count = int(frame.index.duplicated().sum())
    if duplicate_count:
        issues.append(f"Duplicate timestamps: {duplicate_count}")
    invalid_ohlc = int(((frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)) | (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1))).sum())
    if invalid_ohlc:
        issues.append(f"Invalid OHLC rows: {invalid_ohlc}")
    nulls = int(frame[list(required)].isna().sum().sum())
    if nulls:
        issues.append(f"Missing OHLCV values: {nulls}")
    zero_prices = int((frame["Close"] <= 0).sum())
    if zero_prices:
        issues.append(f"Non-positive closes: {zero_prices}")
    returns = frame["Close"].pct_change().abs()
    extreme_moves = int((returns > 0.80).sum())
    if extreme_moves:
        issues.append(f"Extreme one-session moves requiring review: {extreme_moves}")
    meta.update({"duplicates": duplicate_count, "invalid_ohlc": invalid_ohlc, "nulls": nulls, "extreme_moves": extreme_moves, "last_timestamp": str(frame.index[-1]) if len(frame) else None})
    hard_fail = bool(missing or len(frame) < minimum_rows or duplicate_count or invalid_ohlc or nulls or zero_prices)
    return not hard_fail, issues, meta
