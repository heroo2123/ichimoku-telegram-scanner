"""User-editable settings for the Ichimoku Telegram scanner.

Keep Telegram credentials in GitHub Actions secrets:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

CONVERSION_LENGTH = 20
BASE_LENGTH = 60
SPAN_B_LENGTH = 120
DISPLACEMENT = 30
INTERVAL = "1d"

WEEKLY_CONVERSION_LENGTH = 9
WEEKLY_BASE_LENGTH = 26
WEEKLY_SPAN_B_LENGTH = 52
WEEKLY_DISPLACEMENT = 26
LOOKBACK_DAYS = 900

ENABLED_SIGNAL_TYPES = [
    "cloud_breakout", "cloud_breakdown",
    "tk_cross_bullish", "tk_cross_bearish",
    "kijun_bounce_bullish", "kijun_bounce_bearish",
    "cloud_rejection_bullish", "cloud_rejection_bearish",
    "kumo_twist_bullish", "kumo_twist_bearish",
    "trend_continuation_bullish", "trend_continuation_bearish",
]

US_MIN_PRICE = 2.0
US_MIN_AVG_VOLUME_20D = 100_000
US_MIN_AVG_DOLLAR_VOLUME_20D = 2_000_000
US_LIQUIDITY_WINDOW = 20
US_INCLUDE_ETFS = True
US_EXCLUDED_SECURITY_NAME_TERMS = [
    "warrant", "right", "unit", "preferred", "preference",
    "depositary shares", "depositary share", "notes due", "senior notes",
    "subordinated notes", "debenture", "bond", "capital securities",
    "income shares",
]

CRYPTO_STABLE_ASSETS = {
    "USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USD1",
    "BFUSD", "AEUR", "EURI", "USDE", "PYUSD", "RLUSD",
}
CRYPTO_FIAT_ASSETS = {
    "USD", "EUR", "GBP", "AUD", "BRL", "TRY", "UAH", "RUB", "PLN",
    "RON", "ZAR", "NGN", "IDR", "JPY", "MXN", "ARS", "COP", "CZK",
    "HUF", "CAD", "CHF", "AED", "SAR",
}
CRYPTO_EXCLUDED_BASE_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
CRYPTO_EXCLUDED_BASES = set()

GRADE_A_MIN = 8
GRADE_B_MIN = 6
GRADE_C_MIN = 4
MIN_SCORE_TO_REPORT = 4
MIN_SCORE_FOR_DETAIL = 6
TOP_DETAILED_ALERTS = 10
DIGEST_SIGNALS_PER_MESSAGE = 24
MAX_REPORT_SIGNALS = 1000

ATR_LENGTH = 14
VOLUME_AVG_LENGTH = 20
EXTENDED_KIJUN_ATR = 2.5
EXTREME_CANDLE_ATR = 2.0
MIN_CLOUD_THICKNESS_ATR = 0.10

SEND_CHART_IMAGES = True
CHART_LOOKBACK_CANDLES = 180
SEND_CSV_REPORT = True
SEND_RUN_SUMMARY_WHEN_NO_SIGNALS = True
SEND_COMPLETION_SUMMARY = True
REPORT_DIR_NAME = "reports"

TELEGRAM_MAX_RETRIES = 5
TELEGRAM_RETRY_BASE_SECONDS = 2.0
TELEGRAM_REQUEST_TIMEOUT = 75
TELEGRAM_MESSAGE_PAUSE_SECONDS = 0.7

REQUEST_TIMEOUT = 30
HTTP_MAX_RETRIES = 4
HTTP_RETRY_BASE_SECONDS = 1.5
YFINANCE_BATCH_SIZE = 60
YFINANCE_BATCH_RETRIES = 3
YFINANCE_BATCH_PAUSE_SECONDS = 1.2
BINANCE_SYMBOL_PAUSE_SECONDS = 0.04

PERFORMANCE_HORIZONS = [1, 3, 5, 10, 20]
PERFORMANCE_MAX_HORIZON = 20
SIGNAL_HISTORY_RETENTION_DAYS = 730
MAX_SIGNAL_HISTORY_RECORDS = 5_000

CRYPTO_STALE_HOURS = 36
US_STALE_HOURS = 72
HEALTH_ALERT_COOLDOWN_HOURS = 24

US_INDEX_SYMBOLS = [
    "^GSPC", "^DJI", "^IXIC", "^NDX", "^RUT", "^VIX", "^SOX", "^NYA",
    "^XAX", "^MID", "^SML",
]
COMMODITY_FUTURES_SYMBOLS = [
    "GC=F", "SI=F", "HG=F", "PL=F", "PA=F", "CL=F", "BZ=F", "NG=F",
    "HO=F", "RB=F", "ZC=F", "ZW=F", "KE=F", "ZS=F", "ZM=F", "ZL=F",
    "KC=F", "CC=F", "CT=F", "SB=F", "OJ=F", "LE=F", "HE=F", "GF=F",
]

MAX_SYMBOLS_PER_MARKET = None

# Optional timing context for already-qualified daily signals. This never
# blocks or promotes the daily signal; it only labels lower-timeframe timing.
LOWER_TIMEFRAME_CONFIRMATION_ENABLED = True
LOWER_TIMEFRAME_CRYPTO_INTERVAL = "4h"
LOWER_TIMEFRAME_CRYPTO_LIMIT = 500
LOWER_TIMEFRAME_US_PERIOD = "1y"
LOWER_TIMEFRAME_US_INTERVAL = "1h"
