"""
User-editable settings for the Ichimoku Telegram scanner.
Do NOT put Telegram tokens here. Tokens go into GitHub Secrets.
"""

# Ichimoku settings from your screenshot
CONVERSION_LENGTH = 20
BASE_LENGTH = 60
SPAN_B_LENGTH = 120
DISPLACEMENT = 30

# Daily candles only
INTERVAL = "1d"

# How many daily candles to request.
# Needs at least SPAN_B + 2*DISPLACEMENT. 420 gives enough chart/history buffer.
LOOKBACK_DAYS = 420

# Chart image settings
CHART_LOOKBACK_CANDLES = 180

# Telegram behavior
SEND_CHART_IMAGES = True
SEND_RUN_SUMMARY = False  # Set True if you want a Telegram summary even when there are no signals.
MAX_ALERTS_PER_RUN = 100  # Safety cap so Telegram does not get spammed if something goes wrong.

# Crypto universe.
# Empty list [] = all Binance spot pairs.
# Example to reduce duplicates: ["USDT", "USDC"]
CRYPTO_QUOTE_ASSETS = []

# US index symbols from Yahoo Finance / yfinance
US_INDEX_SYMBOLS = [
    "^GSPC",   # S&P 500
    "^DJI",    # Dow Jones Industrial Average
    "^IXIC",   # Nasdaq Composite
    "^NDX",    # Nasdaq 100
    "^RUT",    # Russell 2000
    "^VIX",    # CBOE Volatility Index
    "^SOX",    # PHLX Semiconductor Index
    "^NYA",    # NYSE Composite
    "^XAX",    # NYSE American Composite
    "^MID",    # S&P 400 MidCap
    "^SML",    # S&P 600 SmallCap
]

# Commodity futures symbols from Yahoo Finance / yfinance
COMMODITY_FUTURES_SYMBOLS = [
    "GC=F",  # Gold
    "SI=F",  # Silver
    "HG=F",  # Copper
    "PL=F",  # Platinum
    "PA=F",  # Palladium
    "CL=F",  # WTI Crude Oil
    "BZ=F",  # Brent Crude Oil
    "NG=F",  # Natural Gas
    "HO=F",  # Heating Oil
    "RB=F",  # RBOB Gasoline
    "ZC=F",  # Corn
    "ZW=F",  # Wheat
    "KE=F",  # KC Wheat
    "ZS=F",  # Soybeans
    "ZM=F",  # Soybean Meal
    "ZL=F",  # Soybean Oil
    "KC=F",  # Coffee
    "CC=F",  # Cocoa
    "CT=F",  # Cotton
    "SB=F",  # Sugar
    "OJ=F",  # Orange Juice
    "LE=F",  # Live Cattle
    "HE=F",  # Lean Hogs
    "GF=F",  # Feeder Cattle
]

# yfinance download batching. Lower is slower but more reliable.
YFINANCE_BATCH_SIZE = 80

# For testing only. Leave None for full market scan.
# Example: MAX_SYMBOLS_PER_MARKET = 50
MAX_SYMBOLS_PER_MARKET = None
