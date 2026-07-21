from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

from .models import BacktestResult
from .storage import get_store


def _return(direction: str, entry: float, exit_price: float) -> float:
    raw = (exit_price / entry - 1.0) * 100.0 if entry else 0.0
    return raw if direction == "bullish" else -raw


def run_frame_backtest(scanner_module: Any, frame: pd.DataFrame, market: str, symbol: str, horizons: Sequence[int] = (1, 3, 5, 10, 20)) -> BacktestResult:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    clean = frame.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    trades: List[Dict[str, Any]] = []
    minimum = scanner_module.minimum_daily_rows()
    max_horizon = max(horizons)
    for pos in range(minimum, len(clean) - max_horizon):
        history = clean.iloc[: pos + 1]
        enriched = scanner_module.add_ichimoku(history)
        classification = scanner_module.classify_signal(enriched)
        if not classification:
            continue
        signal_type, direction = classification
        weekly_status, _ = scanner_module.weekly_alignment(history, direction)
        score, reasons, warnings, metrics = scanner_module.score_signal(enriched, direction, signal_type, weekly_status)
        if score < int(scanner_module.config.MIN_SCORE_TO_REPORT):
            continue
        entry = float(clean["Close"].iloc[pos])
        future = clean.iloc[pos + 1 : pos + max_horizon + 1]
        row: Dict[str, Any] = {
            "date": pd.Timestamp(clean.index[pos]).strftime("%Y-%m-%d"),
            "direction": direction,
            "signal_type": signal_type,
            "score": score,
            "grade": scanner_module.grade_for_score(score),
            "weekly_alignment": weekly_status,
            "entry": entry,
            "reasons": reasons,
            "warnings": warnings,
            "metrics": metrics,
        }
        for horizon in horizons:
            exit_price = float(clean["Close"].iloc[pos + horizon])
            row[f"return_{horizon}"] = round(_return(direction, entry, exit_price), 4)
        if direction == "bullish":
            row["mfe_pct"] = round((float(future["High"].max()) / entry - 1.0) * 100.0, 4)
            row["mae_pct"] = round((float(future["Low"].min()) / entry - 1.0) * 100.0, 4)
        else:
            row["mfe_pct"] = round((1.0 - float(future["Low"].min()) / entry) * 100.0, 4)
            row["mae_pct"] = round((1.0 - float(future["High"].max()) / entry) * 100.0, 4)
        trades.append(row)
    summary: Dict[str, Any] = {"signals": len(trades)}
    for horizon in horizons:
        values = [float(trade[f"return_{horizon}"]) for trade in trades]
        summary[f"h{horizon}"] = {
            "count": len(values),
            "win_rate": round(sum(value > 0 for value in values) / len(values) * 100.0, 2) if values else None,
            "mean": round(float(np.mean(values)), 4) if values else None,
            "median": round(float(np.median(values)), 4) if values else None,
            "expectancy": round(float(np.mean(values)), 4) if values else None,
        }
    if trades:
        summary["mean_mfe"] = round(float(np.mean([trade["mfe_pct"] for trade in trades])), 4)
        summary["mean_mae"] = round(float(np.mean([trade["mae_pct"] for trade in trades])), 4)
        summary["by_grade"] = {
            grade: {
                "count": len(items),
                "mean_10": round(float(np.mean([item["return_10"] for item in items])), 4),
            }
            for grade in sorted({trade["grade"] for trade in trades})
            if (items := [trade for trade in trades if trade["grade"] == grade])
        }
    completed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return BacktestResult(
        run_id=str(uuid.uuid4()),
        market=market,
        symbol=symbol,
        started_at=started,
        completed_at=completed,
        parameters={"horizons": list(horizons), "daily": [scanner_module.config.CONVERSION_LENGTH, scanner_module.config.BASE_LENGTH, scanner_module.config.SPAN_B_LENGTH, scanner_module.config.DISPLACEMENT]},
        trades=trades,
        summary=summary,
    )


def fetch_and_backtest(scanner_module: Any, market: str, symbols: Sequence[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if market == "crypto":
        for symbol in symbols:
            frame = scanner_module.fetch_binance_ohlcv(symbol, 1000)
            if frame is not None:
                results.append(run_frame_backtest(scanner_module, frame, "Crypto Spot", symbol).to_dict())
    else:
        frames = scanner_module.fetch_yfinance_batch(symbols)
        for symbol, frame in frames.items():
            results.append(run_frame_backtest(scanner_module, frame, "US Stock", symbol).to_dict())
    store = get_store()
    for row in results:
        store.save_backtest(row)
    return results
