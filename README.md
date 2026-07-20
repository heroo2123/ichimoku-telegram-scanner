# Ichimoku Telegram Scanner 2.0

A daily multi-market Ichimoku scanner that runs on GitHub Actions, ranks fresh signals, sends concise Telegram digests, and tracks post-signal performance.

## Universe

**US:** common stocks and ETFs from the Nasdaq Trader directories, plus configured indices and commodity futures. Warrants, rights, units, preferred shares, debt instruments, test issues, low-priced stocks, and illiquid stocks are filtered.

**Crypto:** Binance spot pairs are retained when the base is a real crypto asset. Crypto/USDT, crypto/USDC, crypto/BTC, crypto/ETH, crypto/BNB, and other crypto-quoted markets remain included. Fiat and stablecoin base assets, fiat-quoted markets, and leveraged tokens are excluded.

## Signal engine

Daily settings remain `20/60/120/30`. Weekly confirmation uses completed weekly candles with `9/26/52/26` settings.

The scanner detects and classifies:

- Cloud breakouts and breakdowns
- Tenkan/Kijun crosses
- Kijun bounces
- Cloud rejections
- Kumo twists
- Trend continuations

Every candidate receives a 0–10 score and A/B/C/D grade using cloud position, Chikou confirmation, Tenkan/Kijun alignment, future cloud, volume, weekly trend, ATR, extension, and liquidity context.

## Telegram delivery

- Retries temporary Telegram failures with exponential backoff
- Does not terminate the scan when one chart fails
- Marks a signal delivered only after its digest succeeds
- Keeps unsuccessful alerts pending for the next run
- Sends a ranked digest, top detailed charts, CSV report, and final health summary

## Performance tracking

Delivered signals are evaluated after 1, 3, 5, 10, and 20 sessions, including directional return, maximum favorable/adverse excursion, cloud re-entry, and Kijun invalidation. Aggregated grade and weekly-alignment statistics are written to `data/last_run_summary.json`.

## Health monitoring

Each completed run checks the other market's heartbeat and sends a cooldown-protected warning when a scheduled scanner becomes stale.

## Commands

```bash
python scanner.py --market crypto
python scanner.py --market us
python scanner.py --market all
python scanner.py --market crypto --dry-run
python scanner.py --test-telegram
python -m unittest discover -s tests -v
```

`--dry-run` does not send Telegram messages or modify scanner state.

## Secrets

Configure these GitHub Actions repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Configuration

Edit `config.py` to adjust liquidity thresholds, stable/fiat asset sets, enabled signal types, grade cutoffs, detail limits, retry behavior, performance horizons, and stale-heartbeat thresholds.

This remains a free-data scanner. Binance and Yahoo Finance can be delayed, incomplete, or temporarily rate-limited, so the code retries and isolates failures but cannot guarantee institutional-grade coverage.
