from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

FEATURES = ("score", "weekly_aligned", "volume_ratio", "cloud_distance_atr", "kijun_distance_atr", "cloud_thickness_atr")


def _feature_row(trade: Dict[str, Any]) -> List[float]:
    metrics = trade.get("metrics") or {}
    return [
        float(trade.get("score") or 0.0) / 10.0,
        1.0 if trade.get("weekly_alignment") == "aligned" else 0.0,
        min(float(metrics.get("volume_ratio") or 0.0), 5.0) / 5.0,
        min(float(metrics.get("cloud_distance_atr") or 0.0), 5.0) / 5.0,
        min(float(metrics.get("kijun_distance_atr") or 0.0), 5.0) / 5.0,
        min(float(metrics.get("cloud_thickness_atr") or 0.0), 3.0) / 3.0,
    ]


def fit_logistic_calibrator(trades: Sequence[Dict[str, Any]], horizon: int = 10, iterations: int = 1500, learning_rate: float = 0.08, l2: float = 0.02) -> Dict[str, Any]:
    usable = [trade for trade in trades if trade.get(f"return_{horizon}") is not None]
    if len(usable) < 30:
        return {"ready": False, "reason": "at_least_30_trades_required", "count": len(usable)}
    x = np.asarray([_feature_row(trade) for trade in usable], dtype=float)
    x = np.c_[np.ones(len(x)), x]
    y = np.asarray([1.0 if float(trade[f"return_{horizon}"]) > 0 else 0.0 for trade in usable], dtype=float)
    weights = np.zeros(x.shape[1], dtype=float)
    for _ in range(iterations):
        logits = np.clip(x @ weights, -30, 30)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        gradient = (x.T @ (probabilities - y)) / len(y)
        gradient[1:] += l2 * weights[1:]
        weights -= learning_rate * gradient
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(x @ weights, -30, 30)))
    brier = float(np.mean((probabilities - y) ** 2))
    accuracy = float(np.mean((probabilities >= 0.5) == y))
    return {
        "ready": True,
        "count": len(usable),
        "horizon": horizon,
        "features": ["intercept", *FEATURES],
        "weights": [round(float(value), 8) for value in weights],
        "brier_score": round(brier, 6),
        "accuracy": round(accuracy, 6),
    }


def predict_probability(model: Dict[str, Any], trade: Dict[str, Any]) -> float | None:
    if not model.get("ready"):
        return None
    row = np.asarray([1.0, *_feature_row(trade)], dtype=float)
    weights = np.asarray(model["weights"], dtype=float)
    probability = 1.0 / (1.0 + np.exp(-np.clip(row @ weights, -30, 30)))
    return round(float(probability), 4)
