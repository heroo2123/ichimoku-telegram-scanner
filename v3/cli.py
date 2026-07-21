from __future__ import annotations

import argparse
import json

import scanner

from .backtest import fetch_and_backtest
from .calibration import fit_logistic_calibrator
from .ingest import ingest_summary
from .lifecycle_refresh import refresh_lifecycle
from .paper import update_paper_portfolio
from .regime import refresh_regimes
from .storage import get_store


def main() -> int:
    parser = argparse.ArgumentParser(description="Ichimoku V3 platform commands")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--market", choices=["crypto", "us", "all"], default="all")
    regime = sub.add_parser("regime")
    regime.add_argument("--market", choices=["crypto", "us", "all"], default="all")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--market", choices=["crypto", "us"], required=True)
    backtest.add_argument("--symbols", nargs="+", required=True)
    sub.add_parser("paper")
    lifecycle = sub.add_parser("lifecycle")
    lifecycle.add_argument("--limit", type=int, default=50)
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--horizon", type=int, default=10)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "ingest":
        result = ingest_summary(args.market)
    elif args.command == "regime":
        result = refresh_regimes(scanner, args.market)
    elif args.command == "backtest":
        result = fetch_and_backtest(scanner, args.market, args.symbols)
    elif args.command == "paper":
        result = update_paper_portfolio(get_store().list_signals(limit=1000))
    elif args.command == "lifecycle":
        result = refresh_lifecycle(scanner, args.limit)
    elif args.command == "calibrate":
        trades = []
        for run in get_store().list_backtests(limit=100):
            trades.extend(run.get("trades") or [])
        result = fit_logistic_calibrator(trades, horizon=args.horizon)
        if result.get("ready"):
            get_store().save_calibration({"market": "all", "direction": "all", "horizon": args.horizon, "model": result, "metrics": {"count": result.get("count"), "brier_score": result.get("brier_score"), "accuracy": result.get("accuracy")}})
    else:
        store = get_store()
        result = {"signals": len(store.list_signals(limit=1000)), "regimes": store.list_regimes(5), "paper": store.load_paper_state()}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
