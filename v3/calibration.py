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


def _fit_weights(x: np.ndarray, y: np.ndarray, iterations: int, learning_rate: float, l2: float) -> np.ndarray:
    weights = np.zeros(x.shape[1], dtype=float)
    for _ in range(iterations):
        logits = np.clip(x @ weights, -30, 30)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        gradient = (x.T @ (probabilities - y)) / len(y)
        gradient[1:] += l2 * weights[1:]
        weights -= learning_rate * gradient
    return weights


def fit_logistic_calibrator(trades: Sequence[Dict[str, Any]], horizon: int = 10, iterations: int = 1500, learning_rate: float = 0.08, l2: float = 0.02) -> Dict[str, Any]:
    usable = sorted(
        [trade for trade in trades if trade.get(f"return_{horizon}") is not None],
        key=lambda trade: (str(trade.get("date") or ""), str(trade.get("symbol") or "")),
    )
    if len(usable) < 60:
        return {"ready": False, "reason": "at_least_60_trades_required", "count": len(usable)}
    x = np.asarray([_feature_row(trade) for trade in usable], dtype=float)
    x = np.c_[np.ones(len(x)), x]
    y = np.asarray([1.0 if float(trade[f"return_{horizon}"]) > 0 else 0.0 for trade in usable], dtype=float)
    minimum_train = max(30, int(len(y) * 0.5))
    fold_size = max(10, (len(y) - minimum_train) // 3)
    out_of_sample_probabilities: List[float] = []
    out_of_sample_targets: List[float] = []
    train_end = minimum_train
    while train_end < len(y):
        test_end = min(len(y), train_end + fold_size)
        fold_weights = _fit_weights(x[:train_end], y[:train_end], iterations, learning_rate, l2)
        fold_probabilities = 1.0 / (1.0 + np.exp(-np.clip(x[train_end:test_end] @ fold_weights, -30, 30)))
        out_of_sample_probabilities.extend(float(value) for value in fold_probabilities)
        out_of_sample_targets.extend(float(value) for value in y[train_end:test_end])
        train_end = test_end
    oos_p = np.asarray(out_of_sample_probabilities, dtype=float)
    oos_y = np.asarray(out_of_sample_targets, dtype=float)
    brier = float(np.mean((oos_p - oos_y) ** 2))
    accuracy = float(np.mean((oos_p >= 0.5) == oos_y))
    weights = _fit_weights(x, y, iterations, learning_rate, l2)
    return {
        "ready": True,
        "count": len(usable),
        "validation": "expanding_window_walk_forward",
        "validation_count": len(oos_y),
        "horizon": horizon,
        "features": ["intercept", *FEATURES],
        "weights": [round(float(value), 8) for value in weights],
        "brier_score": round(brier, 6),
        "accuracy": round(accuracy, 6),
        "base_rate": round(float(np.mean(oos_y)), 6),
    }


def predict_probability(model: Dict[str, Any], trade: Dict[str, Any]) -> float | None:
    if not model.get("ready"):
        return None
    row = np.asarray([1.0, *_feature_row(trade)], dtype=float)
    weights = np.asarray(model["weights"], dtype=float)
    probability = 1.0 / (1.0 + np.exp(-np.clip(row @ weights, -30, 30)))
    return round(float(probability), 4)
