# Ichimoku Scanner V3.1

A production multi-market Ichimoku scanner that ranks fresh daily signals, stores lifecycle and research data in Supabase, sends Telegram results at 3 PM Kuwait, and exposes a private Render dashboard. It does not place live trades.

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
- Marks a signal delivered only after its digest or full CSV report succeeds
- Keeps unsuccessful alerts pending for the next run
- Uses an atomic Supabase queue with the existing JSON state as a fallback
- Starts the primary runner off-peak and time-gates it to 3 PM Kuwait, with a quiet 3:20 PM catch-up
- Reconciles queued rows against the latest lifecycle state before sending
- Excludes invalidated/completed setups and keeps extended setups in the dashboard/CSV
- Sends one ranked top-10 digest, up to three detailed charts, a full CSV report, and a final health summary

Crypto is scanned after its UTC daily candle closes. US markets are scanned after the completed cash session. Scanning and Telegram delivery remain separate, so the dashboard can update before the scheduled message without using an incomplete candle.

## Performance tracking

Delivered signals are evaluated after 1, 3, 5, 10, and 20 sessions, including directional return, maximum favorable/adverse excursion, cloud re-entry, and Kijun invalidation. Aggregated grade and weekly-alignment statistics are written to `data/last_run_summary.json`.

## Health monitoring

Each production scan and delivery writes a durable run record. The dashboard and `/status` show recent run state and queue health. Completed runs also retain the existing cooldown-protected stale-market warning.

## Dashboard and commands

The private Render dashboard keeps the same URL and remembers a revocable server-side session on each browser. Telegram supports `/status`, `/top`, `/active`, `/performance`, `/paper`, and `/help`.

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
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

## Configuration

Edit `config.py` to adjust liquidity thresholds, stable/fiat asset sets, enabled signal types, grade cutoffs, detail limits, retry behavior, performance horizons, and stale-heartbeat thresholds.

This remains a free-data scanner. Binance and Yahoo Finance can be delayed, incomplete, or temporarily rate-limited, so the code retries missing batches and important index/futures symbols, separates provider misses from processing errors, rejects stale/invalid candles, and isolates failures but cannot guarantee institutional-grade coverage. Official settlement closes for indices and commodity futures are accepted when they fall outside the session high/low; ordinary stocks and crypto retain strict OHLC validation. US OHLC data is split-adjusted; crypto data remains native Binance spot data.
