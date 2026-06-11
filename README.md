# Ichimoku Telegram Scanner

This scanner runs online using GitHub Actions and sends fresh daily Ichimoku cloud signals to Telegram.

## What it scans

- US listed stocks from Nasdaq Trader symbol directories
- Major US indices from `config.py`
- Major commodity futures from `config.py`
- Binance spot crypto pairs

## Signal rules

Ichimoku settings:

- Conversion Line: 20
- Base Line: 60
- Leading Span B: 120
- Displacement / Lagging Span: 30
- Timeframe: Daily only

Bullish signal:

- Today's closed candle is above today's visible cloud
- Today's lagging span is above the cloud at its standard plotted location
- Yesterday the combined bullish condition was not already true

Bearish signal:

- Today's closed candle is below today's visible cloud
- Today's lagging span is below the cloud at its standard plotted location
- Yesterday the combined bearish condition was not already true

## Important free-data note

This is a free scanner. Crypto uses Binance public market data. US stocks, indices, and commodity futures use yfinance/Yahoo Finance data through the yfinance library. Free data can fail, be delayed, rate-limited, or miss symbols. This is normal for a free setup.

## Files you may edit later

Most settings are in `config.py`.

Common changes:

```python
SEND_RUN_SUMMARY = False
MAX_ALERTS_PER_RUN = 100
CRYPTO_QUOTE_ASSETS = []      # all Binance spot pairs
# CRYPTO_QUOTE_ASSETS = ["USDT", "USDC"]  # less duplicate crypto noise
MAX_SYMBOLS_PER_MARKET = None # full scan
```

Do not put Telegram tokens inside any file. Use GitHub Secrets.

## GitHub Secrets needed

Create these two repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Workflows

- `Telegram Test`: manual only, sends one test message.
- `Crypto Daily Ichimoku Scan`: runs daily at 00:17 UTC.
- `US Markets Daily Ichimoku Scan`: runs Monday-Friday at 23:17 UTC.

## How duplicate prevention works

The scanner writes the last sent signal date into `data/scan_state.json`. GitHub Actions commits this file back to the repository after each run.

Example:

```json
{
  "US Stock|AAPL|1D|bullish": "2026-06-10",
  "Crypto Spot|BTCUSDT|1D|bearish": "2026-06-09"
}
```

If the same ticker/direction/date appears again, the scanner will not resend it.
