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


def _max_drawdown(returns_pct: Sequence[float]) -> float | None:
    if not returns_pct:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns_pct:
        equity *= max(0.000001, 1.0 + float(value) / 100.0)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return round(worst * 100.0, 4)


def _weekly_alignment_at(scanner_module: Any, weekly: pd.DataFrame, as_of: Any, direction: str) -> str:
    history = weekly[weekly.index <= pd.Timestamp(as_of).normalize()]
    minimum = int(scanner_module.config.WEEKLY_SPAN_B_LENGTH) + 2 * int(scanner_module.config.WEEKLY_DISPLACEMENT) + 5
    if len(history) < minimum:
        return "unknown"
    current = scanner_module.ichimoku_context_at(history, -1, int(scanner_module.config.WEEKLY_DISPLACEMENT))
    if not current:
        return "unknown"
    bullish = bool(current["price_above_cloud"] and current["chikou_above_cloud"])
    bearish = bool(current["price_below_cloud"] and current["chikou_below_cloud"])
    if direction == "bullish":
        return "aligned" if bullish else "opposed" if bearish else "neutral"
    return "aligned" if bearish else "opposed" if bullish else "neutral"


def run_frame_backtest(
    scanner_module: Any,
    frame: pd.DataFrame,
    market: str,
    symbol: str,
    horizons: Sequence[int] = (1, 3, 5, 10, 20),
    *,
    entry_model: str = "next_open",
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> BacktestResult:
    if entry_model not in {"next_open", "signal_close"}:
        raise ValueError("entry_model must be 'next_open' or 'signal_close'")
    if not horizons or any(int(horizon) <= 0 for horizon in horizons):
        raise ValueError("horizons must contain positive session counts")
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    clean = frame.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    trades: List[Dict[str, Any]] = []
    minimum = scanner_module.minimum_daily_rows()
    max_horizon = max(horizons)
    entry_offset = 1 if entry_model == "next_open" else 0
    round_trip_cost_pct = 2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0
    enriched_full = scanner_module.add_ichimoku(clean)
    weekly_full = scanner_module.weekly_frame(clean)
    for pos in range(minimum - 1, len(clean) - max_horizon - entry_offset):
        enriched = enriched_full.iloc[: pos + 1]
        classification = scanner_module.classify_signal(enriched)
        if not classification:
            continue
        signal_type, direction = classification
        weekly_status = _weekly_alignment_at(scanner_module, weekly_full, clean.index[pos], direction)
        score, reasons, warnings, metrics = scanner_module.score_signal(enriched, direction, signal_type, weekly_status)
        if score < int(scanner_module.config.MIN_SCORE_TO_REPORT):
            continue
        entry_pos = pos + entry_offset
        entry_column = "Open" if entry_model == "next_open" else "Close"
        entry = float(clean[entry_column].iloc[entry_pos])
        if direction == "bullish":
            entry *= 1.0 + float(slippage_bps) / 10_000.0
        else:
            entry *= 1.0 - float(slippage_bps) / 10_000.0
        excursion_start = entry_pos if entry_model == "next_open" else entry_pos + 1
        future = clean.iloc[excursion_start : entry_pos + max_horizon + 1]
        row: Dict[str, Any] = {
            "date": pd.Timestamp(clean.index[pos]).strftime("%Y-%m-%d"),
            "entry_date": pd.Timestamp(clean.index[entry_pos]).strftime("%Y-%m-%d"),
            "market": market,
            "symbol": symbol,
            "direction": direction,
            "signal_type": signal_type,
            "score": score,
            "grade": scanner_module.grade_for_score(score),
            "weekly_alignment": weekly_status,
            "entry": entry,
            "entry_model": entry_model,
            "round_trip_cost_pct": round(round_trip_cost_pct, 4),
            "reasons": reasons,
            "warnings": warnings,
            "metrics": metrics,
        }
        for horizon in horizons:
            exit_price = float(clean["Close"].iloc[entry_pos + horizon])
            if direction == "bullish":
                exit_price *= 1.0 - float(slippage_bps) / 10_000.0
            else:
                exit_price *= 1.0 + float(slippage_bps) / 10_000.0
            net_return = _return(direction, entry, exit_price) - 2.0 * float(fee_bps) / 100.0
            row[f"return_{horizon}"] = round(net_return, 4)
        if direction == "bullish":
            row["mfe_pct"] = round((float(future["High"].max()) / entry - 1.0) * 100.0, 4)
            row["mae_pct"] = round((float(future["Low"].min()) / entry - 1.0) * 100.0, 4)
        else:
            row["mfe_pct"] = round((1.0 - float(future["Low"].min()) / entry) * 100.0, 4)
            row["mae_pct"] = round((1.0 - float(future["High"].max()) / entry) * 100.0, 4)
        trades.append(row)
    summary: Dict[str, Any] = {"signals": len(trades), "drawdown_method": "chronological_signal_sequence"}
    for horizon in horizons:
        values = [float(trade[f"return_{horizon}"]) for trade in trades]
        summary[f"h{horizon}"] = {
            "count": len(values),
            "win_rate": round(sum(value > 0 for value in values) / len(values) * 100.0, 2) if values else None,
            "mean": round(float(np.mean(values)), 4) if values else None,
            "median": round(float(np.median(values)), 4) if values else None,
            "expectancy": round(float(np.mean(values)), 4) if values else None,
            "profit_factor": round(sum(value for value in values if value > 0) / abs(sum(value for value in values if value < 0)), 4) if any(value < 0 for value in values) else None,
            "max_drawdown": _max_drawdown(values),
            "standard_deviation": round(float(np.std(values)), 4) if values else None,
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
        parameters={"horizons": list(horizons), "daily": [scanner_module.config.CONVERSION_LENGTH, scanner_module.config.BASE_LENGTH, scanner_module.config.SPAN_B_LENGTH, scanner_module.config.DISPLACEMENT], "entry_model": entry_model, "fee_bps": fee_bps, "slippage_bps": slippage_bps},
        trades=trades,
        summary=summary,
    )


def fetch_and_backtest(scanner_module: Any, market: str, symbols: Sequence[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if market == "crypto":
        for symbol in symbols:
            frame = scanner_module.fetch_binance_history(symbol, int(scanner_module.config.BACKTEST_CRYPTO_DAYS))
            if frame is not None:
                results.append(run_frame_backtest(scanner_module, frame, "Crypto Spot", symbol).to_dict())
    else:
        frames = scanner_module.fetch_yfinance_batch(symbols, period=str(scanner_module.config.BACKTEST_US_PERIOD))
        for symbol, frame in frames.items():
            results.append(run_frame_backtest(scanner_module, frame, "US Stock", symbol).to_dict())
    store = get_store()
    for row in results:
        store.save_backtest(row)
    return results
