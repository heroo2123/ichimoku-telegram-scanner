"""Small scheduling helpers for reliable time-gated GitHub workflows."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone


def seconds_until_utc(hour: int, minute: int, *, now: datetime | None = None) -> float:
    """Return seconds until today's UTC gate, or zero once the gate has passed."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return max(0.0, (target - current).total_seconds())


def wait_until_utc(hour: int, minute: int) -> None:
    """Wait in short intervals so a delayed runner proceeds immediately."""
    while True:
        remaining = seconds_until_utc(hour, minute)
        if remaining <= 0:
            return
        if remaining > 30:
            print(f"Waiting for {hour:02d}:{minute:02d} UTC ({remaining:.0f}s remaining)")
        time.sleep(min(30.0, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait until today's UTC delivery gate")
    parser.add_argument("--hour", type=int, required=True, choices=range(24))
    parser.add_argument("--minute", type=int, required=True, choices=range(60))
    args = parser.parse_args()
    wait_until_utc(args.hour, args.minute)


if __name__ == "__main__":
    main()
