from __future__ import annotations
import argparse
import csv
import html
import json
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
try:
    import mplfinance as mpf
except ImportError:
    mpf = None
import numpy as np
import pandas as pd
import requests
try:
    import yfinance as yf
except ImportError:
    yf = None
import config
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'data'
CHART_DIR = ROOT / 'charts'
REPORT_DIR = ROOT / getattr(config, 'REPORT_DIR_NAME', 'reports')
STATE_PATH = DATA_DIR / 'scan_state.json'
HEARTBEAT_PATH = DATA_DIR / 'heartbeat.json'
SUMMARY_PATH = DATA_DIR / 'last_run_summary.json'
BINANCE_BASES = ['https://data-api.binance.vision', 'https://api.binance.com', 'https://api1.binance.com', 'https://api2.binance.com', 'https://api3.binance.com']
NASDAQ_LISTED_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
OTHER_LISTED_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'
USER_AGENT = 'Mozilla/5.0 ichimoku-telegram-scanner/2.0'
STATE_HISTORY_KEY = '_signal_history'
STATE_PENDING_KEY = '_pending_signals'
STATE_FAILURES_KEY = '_delivery_failures'
STATE_HEALTH_KEY = '_health_alerts'
INSTRUMENT_NAMES: Dict[str, str] = {}
HISTORY_INDEX: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

class ScannerError(Exception):
    pass

@dataclass
class ScanStats:
    market: str
    symbols_discovered: int = 0
    symbols_filtered_universe: int = 0
    symbols_attempted: int = 0
    symbols_with_data: int = 0
    symbols_filtered_liquidity: int = 0
    symbols_filtered_quality: int = 0
    breadth_total: int = 0
    breadth_above_cloud: int = 0
    breadth_below_cloud: int = 0
    symbols_failed: int = 0
    fresh_candidates: int = 0
    pending_loaded: int = 0
    digest_delivered: int = 0
    digest_failed: int = 0
    details_delivered: int = 0
    details_failed: int = 0
    delivery_deferred: int = 0
    provider_errors: List[str] = field(default_factory=list)
    started_utc: str = field(default_factory=lambda: now_utc_iso())
    elapsed_seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        breadth_above_pct = round(self.breadth_above_cloud / self.breadth_total * 100.0, 2) if self.breadth_total else None
        breadth_below_pct = round(self.breadth_below_cloud / self.breadth_total * 100.0, 2) if self.breadth_total else None
        return {'market': self.market, 'symbols_discovered': self.symbols_discovered, 'symbols_filtered_universe': self.symbols_filtered_universe, 'symbols_attempted': self.symbols_attempted, 'symbols_with_data': self.symbols_with_data, 'symbols_filtered_liquidity': self.symbols_filtered_liquidity, 'symbols_filtered_quality': self.symbols_filtered_quality, 'symbols_failed': self.symbols_failed, 'fresh_candidates': self.fresh_candidates, 'pending_loaded': self.pending_loaded, 'digest_delivered': self.digest_delivered, 'digest_failed': self.digest_failed, 'details_delivered': self.details_delivered, 'details_failed': self.details_failed, 'delivery_deferred': self.delivery_deferred, 'breadth_total': self.breadth_total, 'breadth_above_cloud': self.breadth_above_cloud, 'breadth_below_cloud': self.breadth_below_cloud, 'breadth_above_pct': breadth_above_pct, 'breadth_below_pct': breadth_below_pct, 'provider_errors': self.provider_errors[-30:], 'started_utc': self.started_utc, 'elapsed_seconds': self.elapsed_seconds}


def update_breadth(stats: ScanStats, frame: pd.DataFrame) -> None:
    enriched = add_ichimoku(frame)
    context = ichimoku_context_at(enriched, -1, int(config.DISPLACEMENT))
    if not context:
        return
    stats.breadth_total += 1
    if context.get('price_above_cloud'):
        stats.breadth_above_cloud += 1
    elif context.get('price_below_cloud'):
        stats.breadth_below_cloud += 1

@dataclass
class Candidate:
    id: str
    symbol: str
    name: str
    market: str
    direction: str
    signal_type: str
    date: str
    close: float
    score: int
    grade: str
    weekly_alignment: str
    reasons: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]
    lagging_compare_date: str
    chart_df: Optional[pd.DataFrame] = field(default=None, repr=False)

    def serializable(self) -> Dict[str, Any]:
        return {'id': self.id, 'symbol': self.symbol, 'name': self.name, 'market': self.market, 'direction': self.direction, 'signal_type': self.signal_type, 'date': self.date, 'close': self.close, 'score': self.score, 'grade': self.grade, 'weekly_alignment': self.weekly_alignment, 'reasons': self.reasons, 'warnings': self.warnings, 'metrics': self.metrics, 'lagging_compare_date': self.lagging_compare_date}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> 'Candidate':
        return cls(id=str(raw['id']), symbol=str(raw['symbol']), name=str(raw.get('name', raw.get('symbol', ''))), market=str(raw['market']), direction=str(raw['direction']), signal_type=str(raw['signal_type']), date=str(raw['date']), close=float(raw.get('close', 0.0)), score=int(raw.get('score', 0)), grade=str(raw.get('grade', 'D')), weekly_alignment=str(raw.get('weekly_alignment', 'unknown')), reasons=list(raw.get('reasons', [])), warnings=list(raw.get('warnings', [])), metrics=dict(raw.get('metrics', {})), lagging_compare_date=str(raw.get('lagging_compare_date', '')))

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_utc_iso() -> str:
    return now_utc().replace(microsecond=0).isoformat()

def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding='utf-8').strip()
        if not text:
            return default
        return json.loads(text)
    except Exception as exc:
        print(f'Warning: failed to load {path}: {exc}', file=sys.stderr)
        return default

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding='utf-8')
    temp.replace(path)

def safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except Exception:
        return None

def pct_change(current: Optional[float], reference: Optional[float]) -> Optional[float]:
    if current is None or reference in {None, 0}:
        return None
    return (current - reference) / reference * 100.0

def chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    size = max(1, int(size))
    for index in range(0, len(items), size):
        yield items[index:index + size]

def retry_sleep(base: float, attempt: int) -> None:
    time.sleep(min(45.0, float(base) * 2 ** max(0, attempt - 1)))

def http_get(url: str, *, params: Optional[Dict[str, Any]]=None) -> requests.Response:
    errors: List[str] = []
    for attempt in range(1, int(config.HTTP_MAX_RETRIES) + 1):
        try:
            response = requests.get(url, params=params, timeout=int(config.REQUEST_TIMEOUT), headers={'User-Agent': USER_AGENT})
            if response.status_code in {408, 418, 425, 429, 500, 502, 503, 504}:
                raise ScannerError(f'HTTP {response.status_code}: {response.text[:160]}')
            response.raise_for_status()
            return response
        except Exception as exc:
            errors.append(str(exc))
            if attempt < int(config.HTTP_MAX_RETRIES):
                retry_sleep(config.HTTP_RETRY_BASE_SECONDS, attempt)
    raise ScannerError(f"GET failed for {url}: {' | '.join(errors[-3:])}")

def binance_get(path: str, params: Optional[Dict[str, Any]]=None) -> requests.Response:
    errors: List[str] = []
    for base in BINANCE_BASES:
        try:
            return http_get(f'{base}{path}', params=params)
        except Exception as exc:
            errors.append(f'{base}: {exc}')
    raise ScannerError('All Binance endpoints failed: ' + ' | '.join(errors[-3:]))

def clean_yahoo_symbol(symbol: Any) -> Optional[str]:
    if symbol is None:
        return None
    value = str(symbol).strip().upper().replace('.', '-')
    if not value or value in {'SYMBOL', 'ACT SYMBOL'}:
        return None
    if '$' in value or ' ' in value:
        return None
    if not re.fullmatch('[A-Z0-9=^\\-]+', value):
        return None
    return value

def read_pipe_symbol_file(url: str) -> pd.DataFrame:
    response = http_get(url)
    lines = [line for line in response.text.splitlines() if '|' in line and (not line.startswith('File Creation Time'))]
    if not lines:
        return pd.DataFrame()
    frame = pd.read_csv(StringIO('\n'.join(lines)), sep='|', dtype=str)
    return frame.loc[:, ~frame.columns.astype(str).str.startswith('Unnamed')]

def useful_us_security_name(name: Any, is_etf: bool=False) -> bool:
    text = str(name or '').strip().lower()
    if not text:
        return False
    if is_etf and bool(config.US_INCLUDE_ETFS):
        return True
    return not any((term.lower() in text for term in config.US_EXCLUDED_SECURITY_NAME_TERMS))

def get_us_stock_symbols(stats: ScanStats) -> List[str]:
    symbols: List[str] = []
    frames: List[Tuple[pd.DataFrame, str]] = []
    for url, column in [(NASDAQ_LISTED_URL, 'Symbol'), (OTHER_LISTED_URL, 'ACT Symbol')]:
        try:
            frames.append((read_pipe_symbol_file(url), column))
        except Exception as exc:
            stats.provider_errors.append(f'Symbol directory failed: {exc}')
    for frame, symbol_column in frames:
        if frame.empty or symbol_column not in frame.columns:
            continue
        for _, row in frame.iterrows():
            stats.symbols_discovered += 1
            test_issue = str(row.get('Test Issue', 'N')).upper()
            financial_status = str(row.get('Financial Status', 'N')).upper()
            if test_issue == 'Y' or financial_status not in {'', 'N', 'NAN'}:
                stats.symbols_filtered_universe += 1
                continue
            is_etf = str(row.get('ETF', 'N')).upper() == 'Y'
            if not useful_us_security_name(row.get('Security Name', ''), is_etf=is_etf):
                stats.symbols_filtered_universe += 1
                continue
            symbol = clean_yahoo_symbol(row.get(symbol_column))
            if symbol:
                symbols.append(symbol)
                INSTRUMENT_NAMES[f'US Stock|{symbol}'] = str(row.get('Security Name', symbol)).strip()
            else:
                stats.symbols_filtered_universe += 1
    return list(dict.fromkeys(symbols))

def crypto_symbol_is_useful(item: Dict[str, Any]) -> bool:
    if item.get('status') != 'TRADING' or not item.get('isSpotTradingAllowed', True):
        return False
    base = str(item.get('baseAsset', '')).upper()
    quote = str(item.get('quoteAsset', '')).upper()
    if not base or not quote:
        return False
    stable = {str(x).upper() for x in config.CRYPTO_STABLE_ASSETS}
    fiat = {str(x).upper() for x in config.CRYPTO_FIAT_ASSETS}
    if base in stable or base in fiat or base in set(config.CRYPTO_EXCLUDED_BASES):
        return False
    if quote in fiat:
        return False
    if any((base.endswith(suffix) for suffix in config.CRYPTO_EXCLUDED_BASE_SUFFIXES)):
        return False
    return True

def get_binance_spot_symbols(stats: ScanStats) -> List[str]:
    data = binance_get('/api/v3/exchangeInfo').json()
    symbols: List[str] = []
    for item in data.get('symbols', []):
        stats.symbols_discovered += 1
        if not crypto_symbol_is_useful(item):
            stats.symbols_filtered_universe += 1
            continue
        symbol = str(item.get('symbol', '')).upper()
        if symbol:
            symbols.append(symbol)
            INSTRUMENT_NAMES[f'Crypto Spot|{symbol}'] = f"{str(item.get('baseAsset', '')).upper()}/{str(item.get('quoteAsset', '')).upper()}"
    return sorted(set(symbols))

def completed_crypto_daily_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    result = df.copy()
    result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
    today_utc = pd.Timestamp(now_utc().date())
    return result[result.index < today_utc]

def completed_yfinance_daily_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop a same-day US candle only when the regular session may still be open."""
    if df.empty:
        return df
    result = df.copy()
    result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
    today_utc = pd.Timestamp(now_utc().date())
    if now_utc().hour < 22 and len(result) and (result.index[-1] >= today_utc):
        result = result.iloc[:-1]
    return result

def binance_rows_to_frame(rows: Sequence[Sequence[Any]], interval: str) -> Optional[pd.DataFrame]:
    if not rows:
        return None
    frame = pd.DataFrame(rows, columns=['OpenTime', 'Open', 'High', 'Low', 'Close', 'Volume', 'CloseTime', 'QuoteVolume', 'Trades', 'TakerBase', 'TakerQuote', 'Ignore'])
    frame['Date'] = pd.to_datetime(frame['OpenTime'], unit='ms', utc=True).dt.tz_convert(None)
    for column in ['Open', 'High', 'Low', 'Close', 'Volume']:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    if interval == '1d':
        frame['Date'] = frame['Date'].dt.normalize()
    else:
        now_ms = int(now_utc().timestamp() * 1000)
        frame = frame[pd.to_numeric(frame['CloseTime'], errors='coerce') < now_ms]
    frame = frame.set_index('Date')[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    if interval == '1d':
        frame = completed_crypto_daily_rows(frame)
    return frame


def fetch_binance_ohlcv(symbol: str, limit: int, interval: str='1d') -> Optional[pd.DataFrame]:
    try:
        response = binance_get('/api/v3/klines', params={'symbol': symbol, 'interval': interval, 'limit': min(max(int(limit), 200), 1000)})
        rows = response.json()
        frame = binance_rows_to_frame(rows, interval)
        return frame if frame is not None and len(frame) >= minimum_daily_rows() else None
    except Exception as exc:
        print(f'Warning: Binance fetch failed for {symbol}: {exc}', file=sys.stderr)
        return None


def fetch_binance_history(symbol: str, days: int) -> Optional[pd.DataFrame]:
    cutoff_ms = int((now_utc() - timedelta(days=max(200, int(days)))).timestamp() * 1000)
    end_time: Optional[int] = None
    pages: List[Sequence[Any]] = []
    try:
        for _ in range(max(1, math.ceil(int(days) / 1000) + 1)):
            params: Dict[str, Any] = {'symbol': symbol, 'interval': '1d', 'limit': 1000}
            if end_time is not None:
                params['endTime'] = end_time
            rows = binance_get('/api/v3/klines', params=params).json()
            if not rows:
                break
            pages[0:0] = rows
            earliest = int(rows[0][0])
            if earliest <= cutoff_ms or len(rows) < 1000:
                break
            end_time = earliest - 1
        selected = [row for row in pages if int(row[0]) >= cutoff_ms]
        frame = binance_rows_to_frame(selected, '1d')
        if frame is not None:
            frame = frame[~frame.index.duplicated(keep='last')].sort_index()
        return frame if frame is not None and len(frame) >= minimum_daily_rows() else None
    except Exception as exc:
        print(f'Warning: Binance history fetch failed for {symbol}: {exc}', file=sys.stderr)
        return None


def fetch_yfinance_lower_timeframe(symbol: str) -> Optional[pd.DataFrame]:
    if yf is None:
        return None
    try:
        raw = yf.download(
            tickers=symbol,
            period=str(config.LOWER_TIMEFRAME_US_PERIOD),
            interval=str(config.LOWER_TIMEFRAME_US_INTERVAL),
            group_by='ticker',
            auto_adjust=bool(config.YFINANCE_AUTO_ADJUST),
            threads=False,
            progress=False,
            timeout=int(config.REQUEST_TIMEOUT),
        )
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            levels0 = set(map(str, raw.columns.get_level_values(0)))
            frame = raw[symbol].copy() if symbol in levels0 else raw.xs(symbol, axis=1, level=1).copy()
        else:
            frame = raw.copy()
        required = ['Open', 'High', 'Low', 'Close']
        if not set(required).issubset(frame.columns):
            return None
        if 'Volume' not in frame.columns:
            frame['Volume'] = 0.0
        frame = frame[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
        frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(None)
        frame = frame.dropna(subset=required)
        four_hour = frame.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna(subset=required)
        return four_hour if len(four_hour) >= minimum_daily_rows() else None
    except Exception as exc:
        print(f'Warning: lower-timeframe fetch failed for {symbol}: {exc}', file=sys.stderr)
        return None


def attach_lower_timeframe_confirmation(candidate: Candidate) -> None:
    if not getattr(config, 'LOWER_TIMEFRAME_CONFIRMATION_ENABLED', False):
        return
    try:
        if candidate.market == 'Crypto Spot':
            frame = fetch_binance_ohlcv(candidate.symbol, int(config.LOWER_TIMEFRAME_CRYPTO_LIMIT), str(config.LOWER_TIMEFRAME_CRYPTO_INTERVAL))
        else:
            frame = fetch_yfinance_lower_timeframe(candidate.symbol)
        if frame is None:
            candidate.metrics['lower_timeframe'] = {'status': 'unknown', 'reason': 'data_unavailable'}
            return
        from v3.confirmation import lower_timeframe_confirmation
        candidate.metrics['lower_timeframe'] = lower_timeframe_confirmation(frame, candidate.direction, sys.modules[__name__])
    except Exception as exc:
        candidate.metrics['lower_timeframe'] = {'status': 'unknown', 'reason': str(exc)[:160]}

def fetch_yfinance_batch(symbols: Sequence[str], period: Optional[str]=None) -> Dict[str, pd.DataFrame]:
    if yf is None:
        raise ScannerError('yfinance is not installed')
    if not symbols:
        return {}
    last_error: Optional[Exception] = None
    raw: Optional[pd.DataFrame] = None
    for attempt in range(1, int(config.YFINANCE_BATCH_RETRIES) + 1):
        try:
            raw = yf.download(tickers=' '.join(symbols), period=period or f'{int(config.LOOKBACK_DAYS)}d', interval='1d', group_by='ticker', auto_adjust=bool(config.YFINANCE_AUTO_ADJUST), threads=True, progress=False, timeout=int(config.REQUEST_TIMEOUT))
            if raw is not None and (not raw.empty):
                break
            raise ScannerError('empty yfinance response')
        except Exception as exc:
            last_error = exc
            if attempt < int(config.YFINANCE_BATCH_RETRIES):
                retry_sleep(config.HTTP_RETRY_BASE_SECONDS, attempt)
    if raw is None or raw.empty:
        print(f'Warning: yfinance batch failed for {list(symbols)[:3]}: {last_error}', file=sys.stderr)
        return {}
    output: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        parsed = extract_symbol_from_yfinance(raw, symbol)
        if parsed is not None and len(parsed) >= minimum_daily_rows():
            output[symbol] = parsed
    return output

def extract_symbol_from_yfinance(raw: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    try:
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            levels0 = set(map(str, raw.columns.get_level_values(0)))
            levels1 = set(map(str, raw.columns.get_level_values(1)))
            if symbol in levels0:
                frame = raw[symbol].copy()
            elif symbol in levels1:
                frame = raw.xs(symbol, axis=1, level=1).copy()
            else:
                return None
        else:
            frame = raw.copy()
        required = ['Open', 'High', 'Low', 'Close']
        if not all((column in frame.columns for column in required)):
            return None
        if 'Volume' not in frame.columns:
            frame['Volume'] = 0.0
        frame = frame[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
        frame = frame.dropna(subset=required)
        frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
        return completed_yfinance_daily_rows(frame)
    except Exception as exc:
        print(f'Warning: parse failed for {symbol}: {exc}', file=sys.stderr)
        return None

def minimum_daily_rows() -> int:
    return int(config.SPAN_B_LENGTH) + 2 * int(config.DISPLACEMENT) + 5

def add_ichimoku(df: pd.DataFrame, conversion: int=config.CONVERSION_LENGTH, base: int=config.BASE_LENGTH, span_b_length: int=config.SPAN_B_LENGTH, displacement: int=config.DISPLACEMENT) -> pd.DataFrame:
    frame = df.copy()
    high = frame['High']
    low = frame['Low']
    tenkan = (high.rolling(conversion).max() + low.rolling(conversion).min()) / 2.0
    kijun = (high.rolling(base).max() + low.rolling(base).min()) / 2.0
    span_a_raw = (tenkan + kijun) / 2.0
    span_b_raw = (high.rolling(span_b_length).max() + low.rolling(span_b_length).min()) / 2.0
    frame['Tenkan'] = tenkan
    frame['Kijun'] = kijun
    frame['SpanARaw'] = span_a_raw
    frame['SpanBRaw'] = span_b_raw
    frame['SpanA'] = span_a_raw.shift(displacement)
    frame['SpanB'] = span_b_raw.shift(displacement)
    frame['CloudTop'] = frame[['SpanA', 'SpanB']].max(axis=1)
    frame['CloudBottom'] = frame[['SpanA', 'SpanB']].min(axis=1)
    frame['LaggingDisplay'] = frame['Close'].shift(-displacement)
    true_ranges = pd.concat([frame['High'] - frame['Low'], (frame['High'] - frame['Close'].shift(1)).abs(), (frame['Low'] - frame['Close'].shift(1)).abs()], axis=1)
    frame['ATR'] = true_ranges.max(axis=1).rolling(int(config.ATR_LENGTH)).mean()
    frame['VolumeAvg'] = frame['Volume'].rolling(int(config.VOLUME_AVG_LENGTH)).mean()
    return frame

def weekly_frame(daily: pd.DataFrame) -> pd.DataFrame:
    weekly = daily.resample('W-FRI').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna(subset=['Open', 'High', 'Low', 'Close'])
    if len(weekly) and len(daily):
        weekly = weekly[weekly.index <= pd.Timestamp(daily.index.max()).normalize()]
    return add_ichimoku(weekly, conversion=int(config.WEEKLY_CONVERSION_LENGTH), base=int(config.WEEKLY_BASE_LENGTH), span_b_length=int(config.WEEKLY_SPAN_B_LENGTH), displacement=int(config.WEEKLY_DISPLACEMENT))

def ichimoku_context_at(frame: pd.DataFrame, pos: int, displacement: int) -> Dict[str, Any]:
    if pos < 0:
        pos = len(frame) + pos
    lag_pos = pos - int(displacement)
    if pos <= 0 or lag_pos < 0 or pos >= len(frame):
        return {}
    keys = ['Close', 'Open', 'High', 'Low', 'Tenkan', 'Kijun', 'CloudTop', 'CloudBottom', 'ATR', 'Volume', 'VolumeAvg', 'SpanARaw', 'SpanBRaw']
    values = {key.lower(): safe_float(frame[key].iloc[pos]) if key in frame.columns else None for key in keys}
    values['lag_cloud_top'] = safe_float(frame['CloudTop'].iloc[lag_pos])
    values['lag_cloud_bottom'] = safe_float(frame['CloudBottom'].iloc[lag_pos])
    values['lag_pos'] = lag_pos
    close = values.get('close')
    cloud_top = values.get('cloudtop')
    cloud_bottom = values.get('cloudbottom')
    lag_top = values.get('lag_cloud_top')
    lag_bottom = values.get('lag_cloud_bottom')
    values.update({'price_above_cloud': close is not None and cloud_top is not None and (close > cloud_top), 'price_below_cloud': close is not None and cloud_bottom is not None and (close < cloud_bottom), 'chikou_above_cloud': close is not None and lag_top is not None and (close > lag_top), 'chikou_below_cloud': close is not None and lag_bottom is not None and (close < lag_bottom)})
    return values

def weekly_alignment(daily: pd.DataFrame, direction: str) -> Tuple[str, Dict[str, Any]]:
    weekly = weekly_frame(daily)
    minimum = int(config.WEEKLY_SPAN_B_LENGTH) + 2 * int(config.WEEKLY_DISPLACEMENT) + 5
    if len(weekly) < minimum:
        return ('unknown', {})
    current = ichimoku_context_at(weekly, -1, int(config.WEEKLY_DISPLACEMENT))
    if not current:
        return ('unknown', {})
    bullish = bool(current['price_above_cloud'] and current['chikou_above_cloud'])
    bearish = bool(current['price_below_cloud'] and current['chikou_below_cloud'])
    if direction == 'bullish':
        return ('aligned' if bullish else 'opposed' if bearish else 'neutral', current)
    return ('aligned' if bearish else 'opposed' if bullish else 'neutral', current)

def classify_signal(frame: pd.DataFrame) -> Optional[Tuple[str, str]]:
    if len(frame) < minimum_daily_rows():
        return None
    pos = len(frame) - 1
    prev = pos - 1
    current = ichimoku_context_at(frame, pos, int(config.DISPLACEMENT))
    prior = ichimoku_context_at(frame, prev, int(config.DISPLACEMENT))
    if not current or not prior:
        return None
    c = current
    p = prior
    enabled = set(config.ENABLED_SIGNAL_TYPES)
    bull_confirm = bool(c['chikou_above_cloud'])
    bear_confirm = bool(c['chikou_below_cloud'])
    conditions: List[Tuple[str, str, bool]] = [('cloud_breakout', 'bullish', bull_confirm and c['price_above_cloud'] and (not p['price_above_cloud'])), ('cloud_breakdown', 'bearish', bear_confirm and c['price_below_cloud'] and (not p['price_below_cloud'])), ('tk_cross_bullish', 'bullish', bull_confirm and c.get('tenkan') is not None and (c.get('kijun') is not None) and (p.get('tenkan') is not None) and (p.get('kijun') is not None) and (c['tenkan'] > c['kijun']) and (p['tenkan'] <= p['kijun']) and (not c['price_below_cloud'])), ('tk_cross_bearish', 'bearish', bear_confirm and c.get('tenkan') is not None and (c.get('kijun') is not None) and (p.get('tenkan') is not None) and (p.get('kijun') is not None) and (c['tenkan'] < c['kijun']) and (p['tenkan'] >= p['kijun']) and (not c['price_above_cloud'])), ('kijun_bounce_bullish', 'bullish', bull_confirm and c['price_above_cloud'] and (c.get('low') is not None) and (c.get('kijun') is not None) and (c['low'] <= c['kijun'] < c['close']) and (p.get('close') is not None) and (p.get('kijun') is not None) and (p['close'] >= p['kijun'])), ('kijun_bounce_bearish', 'bearish', bear_confirm and c['price_below_cloud'] and (c.get('high') is not None) and (c.get('kijun') is not None) and (c['high'] >= c['kijun'] > c['close']) and (p.get('close') is not None) and (p.get('kijun') is not None) and (p['close'] <= p['kijun'])), ('cloud_rejection_bullish', 'bullish', bull_confirm and c['price_above_cloud'] and p['price_above_cloud'] and (c.get('low') is not None) and (c.get('cloudtop') is not None) and (c['low'] <= c['cloudtop'])), ('cloud_rejection_bearish', 'bearish', bear_confirm and c['price_below_cloud'] and p['price_below_cloud'] and (c.get('high') is not None) and (c.get('cloudbottom') is not None) and (c['high'] >= c['cloudbottom'])), ('kumo_twist_bullish', 'bullish', bull_confirm and c.get('spanaraw') is not None and (c.get('spanbraw') is not None) and (p.get('spanaraw') is not None) and (p.get('spanbraw') is not None) and (c['spanaraw'] > c['spanbraw']) and (p['spanaraw'] <= p['spanbraw'])), ('kumo_twist_bearish', 'bearish', bear_confirm and c.get('spanaraw') is not None and (c.get('spanbraw') is not None) and (p.get('spanaraw') is not None) and (p.get('spanbraw') is not None) and (c['spanaraw'] < c['spanbraw']) and (p['spanaraw'] >= p['spanbraw'])), ('trend_continuation_bullish', 'bullish', bull_confirm and c['price_above_cloud'] and p['price_above_cloud'] and (c.get('close') is not None) and (c.get('tenkan') is not None) and (p.get('close') is not None) and (p.get('tenkan') is not None) and (c['close'] > c['tenkan']) and (p['close'] <= p['tenkan'])), ('trend_continuation_bearish', 'bearish', bear_confirm and c['price_below_cloud'] and p['price_below_cloud'] and (c.get('close') is not None) and (c.get('tenkan') is not None) and (p.get('close') is not None) and (p.get('tenkan') is not None) and (c['close'] < c['tenkan']) and (p['close'] >= p['tenkan']))]
    for signal_type, direction, active in conditions:
        if signal_type in enabled and active:
            return (signal_type, direction)
    return None

def liquidity_metrics(frame: pd.DataFrame) -> Dict[str, Optional[float]]:
    window = frame.tail(int(config.US_LIQUIDITY_WINDOW))
    close = safe_float(frame['Close'].iloc[-1])
    avg_volume = safe_float(window['Volume'].mean())
    avg_dollar_volume = safe_float((window['Close'] * window['Volume']).mean())
    return {'price': close, 'avg_volume_20d': avg_volume, 'avg_dollar_volume_20d': avg_dollar_volume}

def passes_us_liquidity(frame: pd.DataFrame) -> Tuple[bool, Dict[str, Optional[float]]]:
    metrics = liquidity_metrics(frame)
    passed = bool(metrics['price'] is not None and metrics['price'] >= float(config.US_MIN_PRICE) and (metrics['avg_volume_20d'] is not None) and (metrics['avg_volume_20d'] >= float(config.US_MIN_AVG_VOLUME_20D)) and (metrics['avg_dollar_volume_20d'] is not None) and (metrics['avg_dollar_volume_20d'] >= float(config.US_MIN_AVG_DOLLAR_VOLUME_20D)))
    return (passed, metrics)

def grade_for_score(score: int) -> str:
    if score >= int(config.GRADE_A_MIN):
        return 'A'
    if score >= int(config.GRADE_B_MIN):
        return 'B'
    if score >= int(config.GRADE_C_MIN):
        return 'C'
    return 'D'

def cloud_dwell_before_signal(frame: pd.DataFrame, max_bars: int=20) -> int:
    count = 0
    start = len(frame) - 2
    for pos in range(start, max(-1, start - max_bars), -1):
        close = safe_float(frame['Close'].iloc[pos])
        top = safe_float(frame['CloudTop'].iloc[pos])
        bottom = safe_float(frame['CloudBottom'].iloc[pos])
        if None in {close, top, bottom} or not bottom <= close <= top:
            break
        count += 1
    return count

def score_signal(frame: pd.DataFrame, direction: str, signal_type: str, weekly_status: str, extra_metrics: Optional[Dict[str, Any]]=None) -> Tuple[int, List[str], List[str], Dict[str, Any]]:
    current = ichimoku_context_at(frame, -1, int(config.DISPLACEMENT))
    previous = ichimoku_context_at(frame, -2, int(config.DISPLACEMENT))
    score = 0
    reasons: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = dict(extra_metrics or {})
    close = current.get('close')
    atr = current.get('atr')
    kijun = current.get('kijun')
    tenkan = current.get('tenkan')
    cloud_top = current.get('cloudtop')
    cloud_bottom = current.get('cloudbottom')
    volume = current.get('volume')
    volume_avg = current.get('volumeavg')
    cloud_fresh = signal_type in {'cloud_breakout', 'cloud_breakdown'}
    if cloud_fresh:
        score += 2
        reasons.append('Fresh price/cloud break')
    chikou_confirmed = current['chikou_above_cloud'] if direction == 'bullish' else current['chikou_below_cloud']
    if chikou_confirmed:
        score += 2
        reasons.append('Chikou confirms')
    tk_aligned = tenkan is not None and kijun is not None and (tenkan > kijun if direction == 'bullish' else tenkan < kijun)
    if tk_aligned:
        score += 1
        reasons.append('Tenkan/Kijun aligned')
    price_kijun = close is not None and kijun is not None and (close > kijun if direction == 'bullish' else close < kijun)
    if price_kijun:
        score += 1
        reasons.append('Price beyond Kijun')
    future_bull = current.get('spanaraw') is not None and current.get('spanbraw') is not None and (current['spanaraw'] > current['spanbraw'])
    future_aligned = future_bull if direction == 'bullish' else not future_bull
    if future_aligned:
        score += 1
        reasons.append('Future cloud aligned')
    volume_ratio = None
    if volume is not None and volume_avg not in {None, 0}:
        volume_ratio = volume / volume_avg
        metrics['volume_ratio'] = round(volume_ratio, 3)
        if volume_ratio >= 1.2:
            score += 1
            reasons.append('Volume above average')
    if weekly_status == 'aligned':
        score += 2
        reasons.append('Weekly trend aligned')
    elif weekly_status == 'opposed':
        score -= 2
        warnings.append('Weekly trend opposed')
    elif weekly_status == 'unknown':
        warnings.append('Weekly history unavailable')
    cloud_reference = cloud_top if direction == 'bullish' else cloud_bottom
    cloud_distance_pct = None
    if close is not None and cloud_reference not in {None, 0}:
        cloud_distance_pct = abs(close - cloud_reference) / abs(cloud_reference) * 100.0
        metrics['cloud_distance_pct'] = round(cloud_distance_pct, 3)
        if atr not in {None, 0}:
            metrics['cloud_distance_atr'] = round(abs(close - cloud_reference) / atr, 3)
        if cloud_distance_pct >= 0.25:
            score += 1
            reasons.append('Clear cloud separation')
    dwell = cloud_dwell_before_signal(frame) if signal_type in {'cloud_breakout', 'cloud_breakdown'} else 0
    metrics['cloud_dwell_candles'] = dwell
    if dwell:
        reasons.append(f'Breakout followed {dwell} cloud candle(s)')
    if current.get('open') not in {None, 0} and previous.get('close') not in {None, 0}:
        gap = (current['open'] - previous['close']) / previous['close'] * 100.0
        metrics['gap_pct'] = round(gap, 3)
        if abs(gap) >= 5.0:
            warnings.append(f'Large opening gap: {gap:.1f}%')
    kijun_distance_atr = None
    if close is not None and kijun is not None and (atr not in {None, 0}):
        kijun_distance_atr = abs(close - kijun) / atr
        metrics['kijun_distance_atr'] = round(kijun_distance_atr, 3)
        if kijun_distance_atr >= float(config.EXTENDED_KIJUN_ATR):
            score -= 2
            warnings.append(f'Extended {kijun_distance_atr:.1f} ATR from Kijun')
    candle_atr = None
    if current.get('high') is not None and current.get('low') is not None and (atr not in {None, 0}):
        candle_atr = (current['high'] - current['low']) / atr
        metrics['candle_size_atr'] = round(candle_atr, 3)
        if candle_atr >= float(config.EXTREME_CANDLE_ATR):
            warnings.append(f'Large signal candle: {candle_atr:.1f} ATR')
    cloud_thickness_atr = None
    if cloud_top is not None and cloud_bottom is not None and (atr not in {None, 0}):
        cloud_thickness_atr = abs(cloud_top - cloud_bottom) / atr
        metrics['cloud_thickness_atr'] = round(cloud_thickness_atr, 3)
        if cloud_thickness_atr < float(config.MIN_CLOUD_THICKNESS_ATR):
            warnings.append('Very thin cloud')
    score = max(0, min(10, int(score)))
    metrics.update({'atr': atr, 'atr_pct': round(abs(atr / close) * 100.0, 3) if atr not in {None, 0} and close not in {None, 0} else None, 'tenkan': tenkan, 'kijun': kijun, 'cloud_top': cloud_top, 'cloud_bottom': cloud_bottom, 'future_cloud': 'bullish' if future_bull else 'bearish', 'previous_close': previous.get('close'), 'signal_open': current.get('open')})
    return (score, reasons, warnings, metrics)

def candidate_id(market: str, symbol: str, direction: str, signal_type: str, date: str) -> str:
    return f'{market}|{symbol}|1D|{direction}|{signal_type}|{date}'

def legacy_state_key(market: str, symbol: str, direction: str, signal_type: str) -> str:
    return f'{market}|{symbol}|1D|{direction}|{signal_type}'

def original_state_key(market: str, symbol: str, direction: str) -> str:
    return f'{market}|{symbol}|1D|{direction}'

def candidate_from_frame(symbol: str, market: str, raw_frame: pd.DataFrame, extra_metrics: Optional[Dict[str, Any]]=None) -> Optional[Candidate]:
    frame = add_ichimoku(raw_frame)
    classification = classify_signal(frame)
    if not classification:
        return None
    signal_type, direction = classification
    weekly_status, _ = weekly_alignment(raw_frame, direction)
    score, reasons, warnings, metrics = score_signal(frame, direction, signal_type, weekly_status, extra_metrics=extra_metrics)
    if score < int(config.MIN_SCORE_TO_REPORT):
        return None
    date = pd.Timestamp(frame.index[-1]).strftime('%Y-%m-%d')
    lag_date = pd.Timestamp(frame.index[-1 - int(config.DISPLACEMENT)]).strftime('%Y-%m-%d')
    close = float(frame['Close'].iloc[-1])
    return Candidate(id=candidate_id(market, symbol, direction, signal_type, date), symbol=symbol, name=INSTRUMENT_NAMES.get(f'{market}|{symbol}', symbol), market=market, direction=direction, signal_type=signal_type, date=date, close=close, score=score, grade=grade_for_score(score), weekly_alignment=weekly_status, reasons=reasons, warnings=warnings, metrics=metrics, lagging_compare_date=lag_date, chart_df=frame)

def pending_store(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = state.setdefault(STATE_PENDING_KEY, {})
    if not isinstance(value, dict):
        state[STATE_PENDING_KEY] = {}
    return state[STATE_PENDING_KEY]

def history_store(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = state.setdefault(STATE_HISTORY_KEY, {})
    if not isinstance(value, dict):
        state[STATE_HISTORY_KEY] = {}
    return state[STATE_HISTORY_KEY]

def is_delivered(state: Dict[str, Any], candidate: Candidate) -> bool:
    typed_key = legacy_state_key(candidate.market, candidate.symbol, candidate.direction, candidate.signal_type)
    original_key = original_state_key(candidate.market, candidate.symbol, candidate.direction)
    return state.get(typed_key) == candidate.date or state.get(original_key) == candidate.date

def queue_candidate(state: Dict[str, Any], candidate: Candidate) -> bool:
    if is_delivered(state, candidate):
        return False
    pending = pending_store(state)
    if candidate.id not in pending:
        pending[candidate.id] = candidate.serializable()
    return True

def mark_delivered(state: Dict[str, Any], candidate: Candidate) -> None:
    state[legacy_state_key(candidate.market, candidate.symbol, candidate.direction, candidate.signal_type)] = candidate.date
    state[original_state_key(candidate.market, candidate.symbol, candidate.direction)] = candidate.date
    pending_store(state).pop(candidate.id, None)
    history = history_store(state)
    if candidate.id not in history:
        history[candidate.id] = {'id': candidate.id, 'symbol': candidate.symbol, 'name': candidate.name, 'market': candidate.market, 'direction': candidate.direction, 'signal_type': candidate.signal_type, 'date': candidate.date, 'grade': candidate.grade, 'score': candidate.score, 'weekly_alignment': candidate.weekly_alignment, 'entry_close': candidate.close, 'delivered_utc': now_utc_iso(), 'outcomes': {}, 'mfe_pct': None, 'mae_pct': None, 'returned_into_cloud': False, 'kijun_invalidated': False, 'complete': False}

def record_delivery_failure(state: Dict[str, Any], candidate_ids: Sequence[str], error: str) -> None:
    failures = state.setdefault(STATE_FAILURES_KEY, [])
    if not isinstance(failures, list):
        failures = []
        state[STATE_FAILURES_KEY] = failures
    failures.append({'time_utc': now_utc_iso(), 'candidate_ids': list(candidate_ids), 'error': str(error)[:600]})
    del failures[:-100]

def rebuild_history_index(state: Dict[str, Any]) -> None:
    HISTORY_INDEX.clear()
    for record in history_store(state).values():
        key = (str(record.get('market', '')), str(record.get('symbol', '')))
        HISTORY_INDEX.setdefault(key, []).append(record)

def update_history_for_symbol(state: Dict[str, Any], market: str, symbol: str, raw_frame: pd.DataFrame) -> None:
    records = HISTORY_INDEX.get((market, symbol), [])
    if not records:
        return
    frame = add_ichimoku(raw_frame)
    date_index = pd.DatetimeIndex(frame.index).normalize()
    for record in records:
        if record.get('market') != market or record.get('symbol') != symbol or record.get('complete'):
            continue
        try:
            signal_date = pd.Timestamp(record['date']).normalize()
            positions = np.where(date_index == signal_date)[0]
            if len(positions) == 0:
                continue
            start = int(positions[-1])
            entry = float(record.get('entry_close') or record.get('close'))
            direction = record.get('direction')
            outcomes = record.setdefault('outcomes', {})
            for horizon in config.PERFORMANCE_HORIZONS:
                end = start + int(horizon)
                if end < len(frame) and str(horizon) not in outcomes:
                    later = float(frame['Close'].iloc[end])
                    raw_return = (later - entry) / entry * 100.0
                    directional = raw_return if direction == 'bullish' else -raw_return
                    outcomes[str(horizon)] = {'close': later, 'raw_return_pct': round(raw_return, 4), 'directional_return_pct': round(directional, 4)}
            available_end = min(len(frame) - 1, start + int(config.PERFORMANCE_MAX_HORIZON))
            if available_end > start:
                segment = frame.iloc[start + 1:available_end + 1]
                if direction == 'bullish':
                    mfe = (float(segment['High'].max()) - entry) / entry * 100.0
                    mae = (float(segment['Low'].min()) - entry) / entry * 100.0
                    returned = bool((segment['Close'] <= segment['CloudTop']).fillna(False).any())
                    invalidated = bool((segment['Close'] < segment['Kijun']).fillna(False).any())
                else:
                    mfe = (entry - float(segment['Low'].min())) / entry * 100.0
                    mae = (entry - float(segment['High'].max())) / entry * 100.0
                    returned = bool((segment['Close'] >= segment['CloudBottom']).fillna(False).any())
                    invalidated = bool((segment['Close'] > segment['Kijun']).fillna(False).any())
                record['mfe_pct'] = round(mfe, 4)
                record['mae_pct'] = round(mae, 4)
                record['returned_into_cloud'] = returned
                record['kijun_invalidated'] = invalidated
            record['complete'] = str(config.PERFORMANCE_MAX_HORIZON) in outcomes
            record['last_updated_utc'] = now_utc_iso()
        except Exception as exc:
            print(f'Warning: history update failed for {market}/{symbol}: {exc}', file=sys.stderr)

def prune_history(state: Dict[str, Any]) -> None:
    history = history_store(state)
    cutoff = now_utc() - timedelta(days=int(config.SIGNAL_HISTORY_RETENTION_DAYS))
    ordered = sorted(history.items(), key=lambda pair: pair[1].get('date', ''), reverse=True)
    kept: Dict[str, Dict[str, Any]] = {}
    for key, record in ordered:
        try:
            date = datetime.fromisoformat(str(record.get('date'))).replace(tzinfo=timezone.utc)
        except Exception:
            date = now_utc()
        if date >= cutoff and len(kept) < int(config.MAX_SIGNAL_HISTORY_RECORDS):
            kept[key] = record
    state[STATE_HISTORY_KEY] = kept

def performance_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    records = list(history_store(state).values())
    summary: Dict[str, Any] = {'total_records': len(records), 'groups': {}}
    for group_name, selector in {'A_grade': lambda r: r.get('grade') == 'A', 'B_grade': lambda r: r.get('grade') == 'B', 'weekly_aligned': lambda r: r.get('weekly_alignment') == 'aligned', 'weekly_not_aligned': lambda r: r.get('weekly_alignment') != 'aligned'}.items():
        selected = [record for record in records if selector(record)]
        group: Dict[str, Any] = {'signals': len(selected)}
        for horizon in [5, 10, 20]:
            values = [safe_float(record.get('outcomes', {}).get(str(horizon), {}).get('directional_return_pct')) for record in selected]
            clean = [value for value in values if value is not None]
            group[f'{horizon}d_n'] = len(clean)
            group[f'{horizon}d_win_rate'] = round(sum((value > 0 for value in clean)) / len(clean) * 100.0, 2) if clean else None
            group[f'{horizon}d_avg_return'] = round(sum(clean) / len(clean), 3) if clean else None
        summary['groups'][group_name] = group
    return summary

def telegram_request(method: str, data: Dict[str, Any], files_factory: Optional[Any]=None) -> Dict[str, Any]:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise ScannerError('Missing TELEGRAM_BOT_TOKEN GitHub secret')
    url = f'https://api.telegram.org/bot{token}/{method}'
    errors: List[str] = []
    for attempt in range(1, int(config.TELEGRAM_MAX_RETRIES) + 1):
        opened_files: Optional[Dict[str, Any]] = None
        try:
            opened_files = files_factory() if files_factory else None
            response = requests.post(url, data=data, files=opened_files, timeout=int(config.TELEGRAM_REQUEST_TIMEOUT), headers={'User-Agent': USER_AGENT})
            if response.status_code == 429:
                retry_after = response.json().get('parameters', {}).get('retry_after', 5)
                time.sleep(min(60, int(retry_after) + 1))
                raise ScannerError('Telegram rate limited')
            if response.status_code in {408, 425, 500, 502, 503, 504}:
                raise ScannerError(f'Telegram temporary HTTP {response.status_code}')
            if not response.ok:
                raise ScannerError(f'Telegram {method} failed: {response.status_code} {response.text[:500]}')
            payload = response.json()
            if not payload.get('ok', False):
                raise ScannerError(f'Telegram {method} returned not-ok: {payload}')
            return payload
        except Exception as exc:
            errors.append(str(exc))
            if attempt < int(config.TELEGRAM_MAX_RETRIES):
                retry_sleep(config.TELEGRAM_RETRY_BASE_SECONDS, attempt)
        finally:
            if opened_files:
                for item in opened_files.values():
                    try:
                        item.close()
                    except Exception:
                        pass
    raise ScannerError(f"Telegram {method} failed after retries: {' | '.join(errors[-3:])}")

def telegram_chat_id() -> str:
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not chat_id:
        raise ScannerError('Missing TELEGRAM_CHAT_ID GitHub secret')
    return chat_id

def send_telegram_message(text: str) -> Dict[str, Any]:
    return telegram_request('sendMessage', {'chat_id': telegram_chat_id(), 'text': text[:4096], 'parse_mode': 'HTML', 'disable_web_page_preview': True})

def send_telegram_photo(caption: str, path: Path) -> None:
    telegram_request('sendPhoto', {'chat_id': telegram_chat_id(), 'caption': caption[:1024], 'parse_mode': 'HTML'}, files_factory=lambda: {'photo': path.open('rb')})

def send_telegram_document(caption: str, path: Path) -> None:
    telegram_request('sendDocument', {'chat_id': telegram_chat_id(), 'caption': caption[:1024], 'parse_mode': 'HTML'}, files_factory=lambda: {'document': path.open('rb')})

def signal_label(signal_type: str) -> str:
    return signal_type.replace('_', ' ').title()

def tradingview_link(candidate: Candidate) -> str:
    if candidate.market == 'Crypto Spot':
        return f'https://www.tradingview.com/chart/?symbol=BINANCE:{candidate.symbol}'
    symbol = candidate.symbol.replace('=F', '').replace('^', '')
    return f'https://www.tradingview.com/chart/?symbol={symbol}'

def compact_candidate_line(candidate: Candidate) -> str:
    emoji = '🟢' if candidate.direction == 'bullish' else '🔴'
    warning = ' ⚠️' if candidate.warnings else ''
    return f'{emoji} <b>{html.escape(candidate.symbol)}</b> · {candidate.grade}{candidate.score}/10 · {html.escape(signal_label(candidate.signal_type))} · W:{candidate.weekly_alignment}{warning}'

def detail_caption(candidate: Candidate) -> str:
    emoji = '🟢' if candidate.direction == 'bullish' else '🔴'
    metrics = candidate.metrics
    lines = [f'{emoji} <b>{candidate.grade}-GRADE {candidate.direction.upper()} ICHIMOKU SIGNAL</b>', '', f'<b>Ticker:</b> {html.escape(candidate.symbol)}', f'<b>Name:</b> {html.escape(candidate.name)}', f'<b>Market:</b> {html.escape(candidate.market)}', f'<b>Pattern:</b> {html.escape(signal_label(candidate.signal_type))}', f'<b>Score:</b> {candidate.score}/10', f'<b>Signal candle:</b> {candidate.date}', f'<b>Close:</b> {candidate.close:.8g}', f'<b>Weekly:</b> {html.escape(candidate.weekly_alignment)}']
    lower = candidate.metrics.get('lower_timeframe')
    if isinstance(lower, dict):
        lines.append(f"<b>Lower timeframe:</b> {html.escape(str(lower.get('status', 'unknown')))} — {html.escape(str(lower.get('reason', '')))}")
    for label, key, suffix in [('Volume', 'volume_ratio', '× avg'), ('Cloud distance', 'cloud_distance_pct', '%'), ('Kijun distance', 'kijun_distance_atr', ' ATR'), ('ATR', 'atr_pct', '% of price')]:
        value = metrics.get(key)
        if value is not None:
            lines.append(f'<b>{label}:</b> {value}{suffix}')
    lines.extend(['', '<b>Confirmed:</b>'])
    lines.extend((f'✓ {html.escape(reason)}' for reason in candidate.reasons[:8]))
    if candidate.warnings:
        lines.extend(['', '<b>Warnings:</b>'])
        lines.extend((f'⚠ {html.escape(warning)}' for warning in candidate.warnings[:5]))
    lines.extend(['', f'<a href="{tradingview_link(candidate)}">Open TradingView</a>'])
    return '\n'.join(lines)

def make_chart(candidate: Candidate) -> Optional[Path]:
    if not config.SEND_CHART_IMAGES or candidate.chart_df is None or mpf is None:
        return None
    try:
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        frame = candidate.chart_df.tail(int(config.CHART_LOOKBACK_CANDLES)).copy()
        if frame.empty:
            return None
        signal_date = pd.Timestamp(candidate.date).normalize()
        marker_series = pd.Series(np.nan, index=frame.index)
        positions = np.where(pd.DatetimeIndex(frame.index).normalize() == signal_date)[0]
        marker_pos = int(positions[-1]) if len(positions) else len(frame) - 1
        marker_series.iloc[marker_pos] = frame['Close'].iloc[marker_pos]
        addplots = []
        for column, label, width in [('Tenkan', 'Tenkan', 0.9), ('Kijun', 'Kijun', 0.9), ('LaggingDisplay', 'Chikou', 0.8)]:
            if column in frame.columns and frame[column].notna().any():
                addplots.append(mpf.make_addplot(frame[column], width=width, label=label))
        addplots.append(mpf.make_addplot(marker_series, type='scatter', marker='^' if candidate.direction == 'bullish' else 'v', markersize=130, label='Signal'))
        filename = re.sub('[^A-Za-z0-9_.=-]+', '_', f'{candidate.market}_{candidate.symbol}_{candidate.date}.png')
        path = CHART_DIR / filename
        mpf.plot(frame[['Open', 'High', 'Low', 'Close', 'Volume']], type='candle', volume=True, style='yahoo', addplot=addplots, fill_between={'y1': frame['SpanA'].values, 'y2': frame['SpanB'].values, 'alpha': 0.2}, title=f'{candidate.symbol} | {candidate.grade}{candidate.score}/10 | {signal_label(candidate.signal_type)}', ylabel='Price', ylabel_lower='Volume', figsize=(13, 8), warn_too_much_data=10000, savefig={'fname': str(path), 'dpi': 140, 'bbox_inches': 'tight'})
        plt.close('all')
        return path
    except Exception as exc:
        print(f'Warning: chart failed for {candidate.symbol}: {exc}', file=sys.stderr)
        plt.close('all')
        return None

def write_csv_report(candidates: Sequence[Candidate], market: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date = now_utc().strftime('%Y-%m-%d')
    path = REPORT_DIR / f'ichimoku_{market}_{date}.csv'
    columns = ['symbol', 'name', 'market', 'date', 'direction', 'signal_type', 'grade', 'score', 'weekly_alignment', 'close', 'volume_ratio', 'cloud_distance_pct', 'cloud_distance_atr', 'cloud_dwell_candles', 'kijun_distance_atr', 'atr_pct', 'gap_pct', 'avg_volume_20d', 'avg_dollar_volume_20d', 'reasons', 'warnings', 'tradingview']
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({'symbol': candidate.symbol, 'name': candidate.name, 'market': candidate.market, 'date': candidate.date, 'direction': candidate.direction, 'signal_type': candidate.signal_type, 'grade': candidate.grade, 'score': candidate.score, 'weekly_alignment': candidate.weekly_alignment, 'close': candidate.close, 'volume_ratio': candidate.metrics.get('volume_ratio'), 'cloud_distance_pct': candidate.metrics.get('cloud_distance_pct'), 'cloud_distance_atr': candidate.metrics.get('cloud_distance_atr'), 'cloud_dwell_candles': candidate.metrics.get('cloud_dwell_candles'), 'kijun_distance_atr': candidate.metrics.get('kijun_distance_atr'), 'atr_pct': candidate.metrics.get('atr_pct'), 'gap_pct': candidate.metrics.get('gap_pct'), 'avg_volume_20d': candidate.metrics.get('avg_volume_20d'), 'avg_dollar_volume_20d': candidate.metrics.get('avg_dollar_volume_20d'), 'reasons': '; '.join(candidate.reasons), 'warnings': '; '.join(candidate.warnings), 'tradingview': tradingview_link(candidate)})
    return path

def performance_digest_line(summary: Dict[str, Any]) -> Optional[str]:
    aligned = summary.get('groups', {}).get('weekly_aligned', {})
    n = aligned.get('10d_n')
    win = aligned.get('10d_win_rate')
    avg = aligned.get('10d_avg_return')
    if not n:
        return None
    return f'Tracked weekly-aligned 10D: n={n}, win={win}%, avg={avg}%'

def deliver_candidates(state: Dict[str, Any], candidates: Sequence[Candidate], stats: ScanStats, dry_run: bool, delivery_claim: Optional[Any]=None) -> List[Candidate]:
    ordered = sorted(candidates, key=lambda item: (-item.score, item.grade, item.symbol))[:int(config.MAX_REPORT_SIGNALS)]
    if dry_run:
        for candidate in ordered:
            print('DRY RUN:', json.dumps(candidate.serializable(), default=str))
        return []
    delivered: List[Candidate] = []
    perf_line = performance_digest_line(performance_summary(state))
    groups = list(chunks(ordered, int(config.DIGEST_SIGNALS_PER_MESSAGE)))
    for index, group in enumerate(groups, 1):
        header = [f'📊 <b>Ichimoku Daily Digest — {html.escape(stats.market)}</b>', f"Part {index}/{len(groups)} · Signals {len(ordered)} · {now_utc().strftime('%Y-%m-%d UTC')}"]
        if index == 1:
            header.append(f'Scanned {stats.symbols_attempted} · Valid {stats.symbols_with_data} · Filtered {stats.symbols_filtered_liquidity + stats.symbols_filtered_universe + stats.symbols_filtered_quality} · Errors {stats.symbols_failed}')
            if perf_line:
                header.append(perf_line)
        text = '\n'.join(header + [''] + [compact_candidate_line(candidate) for candidate in group])
        ids = [candidate.id for candidate in group]
        try:
            response = send_telegram_message(text)
            if delivery_claim is not None:
                try:
                    result = dict(response.get('result') or {}) if isinstance(response, dict) else {}
                    chat = result.get('chat') if isinstance(result.get('chat'), dict) else {}
                    delivery_claim.complete(ids, {'telegram_message_id': result.get('message_id'), 'chat_id': chat.get('id')})
                except Exception as tracking_exc:
                    record_delivery_failure(state, ids, f'Digest sent but Supabase completion tracking failed: {tracking_exc}')
                    print(f'Warning: digest sent but queue completion failed: {tracking_exc}', file=sys.stderr)
            for candidate in group:
                mark_delivered(state, candidate)
                delivered.append(candidate)
            save_json(STATE_PATH, state)
            stats.digest_delivered += len(group)
        except Exception as exc:
            stats.digest_failed += len(group)
            if delivery_claim is not None:
                try:
                    delivery_claim.fail(ids, str(exc))
                except Exception as tracking_exc:
                    print(f'Warning: queue failure tracking failed: {tracking_exc}', file=sys.stderr)
            record_delivery_failure(state, ids, str(exc))
            save_json(STATE_PATH, state)
            print(f'Warning: digest part {index} failed: {exc}', file=sys.stderr)
        time.sleep(float(config.TELEGRAM_MESSAGE_PAUSE_SECONDS))
    if delivered and config.SEND_CSV_REPORT:
        try:
            report = write_csv_report(delivered, stats.market.replace(' ', '_').lower())
            send_telegram_document(f'Full {html.escape(stats.market)} Ichimoku report ({len(delivered)} signals)', report)
        except Exception as exc:
            print(f'Warning: CSV report delivery failed: {exc}', file=sys.stderr)
    detail_pool = [candidate for candidate in delivered if candidate.score >= int(config.MIN_SCORE_FOR_DETAIL)]
    for candidate in detail_pool[:int(config.TOP_DETAILED_ALERTS)]:
        try:
            chart = make_chart(candidate)
            if chart and chart.exists():
                send_telegram_photo(detail_caption(candidate), chart)
            else:
                send_telegram_message(detail_caption(candidate))
            stats.details_delivered += 1
        except Exception as exc:
            stats.details_failed += 1
            print(f'Warning: detail delivery failed for {candidate.symbol}: {exc}', file=sys.stderr)
        time.sleep(float(config.TELEGRAM_MESSAGE_PAUSE_SECONDS))
    return delivered

def check_stale_heartbeats(current_market: str, heartbeat: Dict[str, Any], state: Dict[str, Any], dry_run: bool) -> None:
    thresholds = {'crypto': int(config.CRYPTO_STALE_HOURS), 'us': int(config.US_STALE_HOURS)}
    health_state = state.setdefault(STATE_HEALTH_KEY, {})
    for market, threshold in thresholds.items():
        if market == current_market:
            continue
        raw = heartbeat.get(market, {}).get('last_run_utc')
        if not raw:
            continue
        try:
            last = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            age_hours = (now_utc() - last.astimezone(timezone.utc)).total_seconds() / 3600.0
        except Exception:
            continue
        if age_hours <= threshold:
            continue
        cooldown_raw = health_state.get(market)
        if cooldown_raw:
            try:
                prior = datetime.fromisoformat(cooldown_raw.replace('Z', '+00:00'))
                if (now_utc() - prior).total_seconds() < int(config.HEALTH_ALERT_COOLDOWN_HOURS) * 3600:
                    continue
            except Exception:
                pass
        message = f'⚠️ <b>Ichimoku scanner heartbeat stale</b>\nMarket: {market}\nLast completed: {html.escape(raw)}\nAge: {age_hours:.1f} hours'
        if dry_run:
            print('DRY RUN HEALTH:', message)
        else:
            try:
                send_telegram_message(message)
                health_state[market] = now_utc_iso()
            except Exception as exc:
                print(f'Warning: health alert failed: {exc}', file=sys.stderr)

def load_pending_for_market(state: Dict[str, Any], market_group: str) -> List[Candidate]:
    desired = {'Crypto Spot'} if market_group == 'crypto' else {'US Stock', 'US Index', 'Commodity Future'}
    candidates: List[Candidate] = []
    for raw in pending_store(state).values():
        try:
            candidate = Candidate.from_dict(raw)
            if candidate.market in desired and (not is_delivered(state, candidate)):
                candidates.append(candidate)
        except Exception:
            continue
    return candidates

def scan_crypto(state: Dict[str, Any], stats: ScanStats, dry_run: bool=False) -> Tuple[List[Candidate], Dict[str, pd.DataFrame]]:
    symbols = get_binance_spot_symbols(stats)
    max_symbols = getattr(config, 'MAX_SYMBOLS_PER_MARKET', None)
    if max_symbols is not None:
        symbols = symbols[:int(max_symbols)]
    frame_map: Dict[str, pd.DataFrame] = {}
    candidates: List[Candidate] = []
    for index, symbol in enumerate(symbols, 1):
        stats.symbols_attempted += 1
        try:
            frame = fetch_binance_ohlcv(symbol, int(config.LOOKBACK_DAYS))
            if frame is None:
                stats.symbols_failed += 1
                continue
            from v3.quality import validate_ohlcv
            quality_ok, quality_issues, quality_meta = validate_ohlcv(frame, minimum_rows=minimum_daily_rows(), max_age_days=int(config.CRYPTO_MAX_DATA_AGE_DAYS))
            if not quality_ok:
                stats.symbols_filtered_quality += 1
                stats.provider_errors.append(f'{symbol}: data quality failed: {"; ".join(quality_issues[:3])}')
                continue
            stats.symbols_with_data += 1
            update_breadth(stats, frame)
            frame_map[symbol] = frame
            update_history_for_symbol(state, 'Crypto Spot', symbol, frame)
            candidate = candidate_from_frame(symbol, 'Crypto Spot', frame, extra_metrics={'data_quality': quality_meta, 'data_quality_warnings': quality_issues})
            if candidate:
                attach_lower_timeframe_confirmation(candidate)
            if candidate and (dry_run or queue_candidate(state, candidate)):
                candidates.append(candidate)
                stats.fresh_candidates += 1
        except Exception as exc:
            stats.symbols_failed += 1
            stats.provider_errors.append(f'{symbol}: {exc}')
        if index % 100 == 0:
            if not dry_run:
                save_json(STATE_PATH, state)
            print(f'Crypto progress {index}/{len(symbols)} candidates={len(candidates)}')
        time.sleep(float(config.BINANCE_SYMBOL_PAUSE_SECONDS))
    return (candidates, frame_map)

def scan_yfinance_symbols(symbols: Sequence[str], market: str, state: Dict[str, Any], stats: ScanStats, apply_liquidity_filter: bool, dry_run: bool=False) -> Tuple[List[Candidate], Dict[str, pd.DataFrame]]:
    max_symbols = getattr(config, 'MAX_SYMBOLS_PER_MARKET', None)
    symbols = list(symbols[:int(max_symbols)]) if max_symbols is not None else list(symbols)
    candidates: List[Candidate] = []
    frame_map: Dict[str, pd.DataFrame] = {}
    for batch_no, batch in enumerate(chunks(symbols, int(config.YFINANCE_BATCH_SIZE)), 1):
        stats.symbols_attempted += len(batch)
        data = fetch_yfinance_batch(batch)
        missing = len(batch) - len(data)
        stats.symbols_failed += missing
        for symbol, frame in data.items():
            try:
                from v3.quality import validate_ohlcv
                quality_ok, quality_issues, quality_meta = validate_ohlcv(frame, minimum_rows=minimum_daily_rows(), max_age_days=int(config.US_MAX_DATA_AGE_DAYS))
                if not quality_ok:
                    stats.symbols_filtered_quality += 1
                    stats.provider_errors.append(f'{symbol}: data quality failed: {"; ".join(quality_issues[:3])}')
                    continue
                stats.symbols_with_data += 1
                update_history_for_symbol(state, market, symbol, frame)
                extra: Dict[str, Any] = {'data_quality': quality_meta, 'data_quality_warnings': quality_issues}
                if apply_liquidity_filter:
                    passed, liquidity = passes_us_liquidity(frame)
                    extra.update(liquidity)
                    if not passed:
                        stats.symbols_filtered_liquidity += 1
                        continue
                update_breadth(stats, frame)
                frame_map[symbol] = frame
                candidate = candidate_from_frame(symbol, market, frame, extra_metrics=extra)
                if candidate:
                    attach_lower_timeframe_confirmation(candidate)
                if candidate and (dry_run or queue_candidate(state, candidate)):
                    candidates.append(candidate)
                    stats.fresh_candidates += 1
            except Exception as exc:
                stats.symbols_failed += 1
                stats.provider_errors.append(f'{symbol}: {exc}')
        if not dry_run:
            save_json(STATE_PATH, state)
        print(f'{market} batch {batch_no}: checked={len(batch)} candidates={len(candidates)}')
        time.sleep(float(config.YFINANCE_BATCH_PAUSE_SECONDS))
    return (candidates, frame_map)

def scan_us(state: Dict[str, Any], stats: ScanStats, dry_run: bool=False) -> Tuple[List[Candidate], Dict[str, pd.DataFrame]]:
    stocks = get_us_stock_symbols(stats)
    all_candidates: List[Candidate] = []
    all_frames: Dict[str, pd.DataFrame] = {}
    for symbols, market, liquidity in [(stocks, 'US Stock', True), (config.US_INDEX_SYMBOLS, 'US Index', False), (config.COMMODITY_FUTURES_SYMBOLS, 'Commodity Future', False)]:
        candidates, frames = scan_yfinance_symbols(symbols, market, state, stats, liquidity, dry_run=dry_run)
        all_candidates.extend(candidates)
        all_frames.update({f'{market}|{key}': value for key, value in frames.items()})
    return (all_candidates, all_frames)

def attach_pending_frames(pending: Sequence[Candidate], frame_map: Dict[str, pd.DataFrame]) -> None:
    for candidate in pending:
        key = candidate.symbol if candidate.market == 'Crypto Spot' else f'{candidate.market}|{candidate.symbol}'
        raw = frame_map.get(key)
        if raw is not None:
            candidate.chart_df = add_ichimoku(raw)

def merge_candidates(*groups: Sequence[Candidate]) -> List[Candidate]:
    merged: Dict[str, Candidate] = {}
    for group in groups:
        for candidate in group:
            current = merged.get(candidate.id)
            if current is None or candidate.chart_df is not None:
                merged[candidate.id] = candidate
    return list(merged.values())

def refresh_pending_frames(pending: Sequence[Candidate]) -> Dict[str, pd.DataFrame]:
    frame_map: Dict[str, pd.DataFrame] = {}
    crypto = [candidate for candidate in pending if candidate.market == 'Crypto Spot']
    for candidate in crypto:
        try:
            frame = fetch_binance_ohlcv(candidate.symbol, int(config.LOOKBACK_DAYS))
            if frame is not None:
                frame_map[candidate.symbol] = frame
        except Exception as exc:
            print(f'Warning: could not refresh {candidate.symbol}: {exc}', file=sys.stderr)
    for market in ['US Stock', 'US Index', 'Commodity Future']:
        group = [candidate.symbol for candidate in pending if candidate.market == market]
        for batch in chunks(group, int(config.YFINANCE_BATCH_SIZE)):
            try:
                frames = fetch_yfinance_batch(batch)
                for symbol, frame in frames.items():
                    frame_map[f'{market}|{symbol}'] = frame
            except Exception as exc:
                print(f'Warning: could not refresh {market} pending frames: {exc}', file=sys.stderr)
    return frame_map

def deliver_pending_market(market: str, state: Dict[str, Any], dry_run: bool=False, quiet_when_empty: bool=False) -> Tuple[List[Candidate], ScanStats]:
    stats = ScanStats(market=f'{market}-delivery')
    started = time.time()
    local_pending = load_pending_for_market(state, market)
    pending = local_pending
    delivery_claim: Optional[Any] = None
    if not dry_run:
        try:
            from v3.queue import get_delivery_queue
            queue = get_delivery_queue()
            if queue is not None:
                if local_pending:
                    queue.enqueue([candidate.serializable() for candidate in local_pending], scheduled_for=now_utc_iso())
                    persisted_delivered = queue.delivered_signal_ids([candidate.id for candidate in local_pending])
                    if persisted_delivered:
                        for candidate in local_pending:
                            if candidate.id in persisted_delivered:
                                mark_delivered(state, candidate)
                        local_pending = [candidate for candidate in local_pending if candidate.id not in persisted_delivered]
                delivery_claim = queue.claim(market)
                claimed: List[Candidate] = []
                already_delivered: List[str] = []
                for raw in delivery_claim.candidates:
                    candidate = Candidate.from_dict(raw)
                    if is_delivered(state, candidate):
                        already_delivered.append(candidate.id)
                    else:
                        claimed.append(candidate)
                if already_delivered:
                    delivery_claim.complete(already_delivered, {'reconciled_from_local_state': True})
                pending = claimed
        except Exception as exc:
            delivery_claim = None
            pending = local_pending
            stats.provider_errors.append(f'Supabase queue fallback: {exc}')
            print(f'Warning: using local delivery queue fallback: {exc}', file=sys.stderr)
    stats.pending_loaded = len(pending)
    if pending:
        frame_map = refresh_pending_frames(pending)
        attach_pending_frames(pending, frame_map)
        delivered = deliver_candidates(state, pending, stats, dry_run=dry_run, delivery_claim=delivery_claim)
    else:
        delivered = []
        if not quiet_when_empty:
            send_no_signal_summary(stats, dry_run=dry_run)
    stats.elapsed_seconds = round(time.time() - started, 1)
    if pending:
        send_completion_summary(stats, len(load_pending_for_market(state, market)), dry_run=dry_run)
    if not dry_run:
        write_run_files(market, stats, delivered, state, observed=pending, mode='delivery')
        save_json(STATE_PATH, state)
    return delivered, stats

def write_run_files(market: str, stats: ScanStats, delivered: Sequence[Candidate], state: Dict[str, Any], observed: Optional[Sequence[Candidate]]=None, mode: str='live') -> None:
    heartbeat = load_json(HEARTBEAT_PATH, {})
    alerts = list(observed if observed is not None else delivered)
    heartbeat_key = f'{market}_delivery' if mode == 'delivery' else market
    heartbeat[heartbeat_key] = {'last_run_utc': now_utc_iso(), 'alerts_count': len(alerts), 'delivered_count': len(delivered), 'status': 'completed', 'mode': mode, 'stats': stats.as_dict()}
    save_json(HEARTBEAT_PATH, heartbeat)
    summary = load_json(SUMMARY_PATH, {})
    payload = {'last_run_utc': now_utc_iso(), 'alerts': [candidate.serializable() for candidate in alerts], 'delivered_alerts': [candidate.serializable() for candidate in delivered], 'delivery_mode': mode, 'stats': stats.as_dict(), 'performance': performance_summary(state)}
    if mode == 'delivery':
        market_summary = summary.setdefault(market, {})
        market_summary['last_delivery'] = payload
    else:
        summary[market] = {**payload, 'last_scan': payload}
    save_json(SUMMARY_PATH, summary)

def send_no_signal_summary(stats: ScanStats, dry_run: bool) -> None:
    if not config.SEND_RUN_SUMMARY_WHEN_NO_SIGNALS:
        return
    message = f'✅ <b>Ichimoku scan complete — no new reportable signals</b>\nMarket: {html.escape(stats.market)}\nAttempted: {stats.symbols_attempted}\nValid data: {stats.symbols_with_data}\nFiltered: {stats.symbols_filtered_universe + stats.symbols_filtered_liquidity + stats.symbols_filtered_quality}\nErrors: {stats.symbols_failed}'
    if dry_run:
        print('DRY RUN SUMMARY:', message)
    else:
        try:
            send_telegram_message(message)
        except Exception as exc:
            print(f'Warning: no-signal summary failed: {exc}', file=sys.stderr)

def send_completion_summary(stats: ScanStats, pending_count: int, dry_run: bool) -> None:
    if not getattr(config, 'SEND_COMPLETION_SUMMARY', True):
        return
    message = f'✅ <b>Ichimoku scanner run complete</b>\nMarket: {html.escape(stats.market)}\nSymbols attempted: {stats.symbols_attempted}\nValid data: {stats.symbols_with_data}\nFresh signals: {stats.fresh_candidates}\nDigest delivered: {stats.digest_delivered}\nDigest failures this run: {stats.digest_failed}\nPending for retry: {pending_count}\nDetail charts: {stats.details_delivered} sent, {stats.details_failed} failed\nProvider errors: {stats.symbols_failed}\nElapsed: {stats.elapsed_seconds:.1f}s'
    if dry_run:
        print('DRY RUN COMPLETION:', message)
    else:
        try:
            send_telegram_message(message)
        except Exception as exc:
            print(f'Warning: completion summary failed: {exc}', file=sys.stderr)

def run_market(market: str, state: Dict[str, Any], dry_run: bool, defer_delivery: bool=False) -> Tuple[List[Candidate], ScanStats]:
    stats = ScanStats(market=market)
    started = time.time()
    rebuild_history_index(state)
    pending = load_pending_for_market(state, market)
    stats.pending_loaded = len(pending)
    if market == 'crypto':
        fresh, frame_map = scan_crypto(state, stats, dry_run=dry_run)
    else:
        fresh, frame_map = scan_us(state, stats, dry_run=dry_run)
    attach_pending_frames(pending, frame_map)
    queued = merge_candidates(pending, fresh)
    if defer_delivery:
        delivered: List[Candidate] = []
        stats.delivery_deferred = len(queued)
        if queued and not dry_run:
            try:
                from v3.queue import get_delivery_queue
                queue = get_delivery_queue()
                if queue is not None:
                    queue.enqueue([candidate.serializable() for candidate in queued])
            except Exception as exc:
                stats.provider_errors.append(f'Supabase queue dual-write failed: {exc}')
                print(f'Warning: Supabase queue dual-write failed; JSON fallback retained: {exc}', file=sys.stderr)
    else:
        delivered = deliver_candidates(state, queued, stats, dry_run=dry_run) if queued else []
        if not queued:
            send_no_signal_summary(stats, dry_run=dry_run)
    prune_history(state)
    stats.elapsed_seconds = round(time.time() - started, 1)
    remaining_pending = len(load_pending_for_market(state, market)) if not dry_run else 0
    if queued and not defer_delivery:
        send_completion_summary(stats, remaining_pending, dry_run=dry_run)
    if not dry_run:
        write_run_files(market, stats, delivered, state, observed=queued, mode='deferred' if defer_delivery else 'live')
        save_json(STATE_PATH, state)
        if not defer_delivery:
            heartbeat = load_json(HEARTBEAT_PATH, {})
            check_stale_heartbeats(market, heartbeat, state, dry_run=False)
            save_json(STATE_PATH, state)
    return (delivered, stats)

def test_telegram() -> None:
    send_telegram_message(f'✅ <b>Ichimoku scanner Telegram test successful</b>\n\nTime UTC: {now_utc_iso()}\nDaily settings: {config.CONVERSION_LENGTH}/{config.BASE_LENGTH}/{config.SPAN_B_LENGTH}/{config.DISPLACEMENT}\nRetry-protected Telegram delivery is working.')
    print('Telegram test sent.')

def save_scanner_run_record(run_id: str, market: str, mode: str, status: str, stats: Optional[ScanStats]=None, error: Optional[str]=None, started_at: Optional[str]=None) -> None:
    try:
        from v3.storage import get_store
        effective_started_at = started_at or (stats.started_utc if stats is not None else now_utc_iso())
        get_store().save_scanner_run({
            'run_id': run_id,
            'market': market,
            'mode': mode,
            'status': status,
            'started_at': effective_started_at,
            'completed_at': now_utc_iso() if status != 'running' else None,
            'stats': stats.as_dict() if stats is not None else {},
            'error': error,
            'source': 'github-actions' if os.getenv('GITHUB_ACTIONS') == 'true' else 'local',
        })
    except Exception as exc:
        print(f'Warning: scanner run observability unavailable: {exc}', file=sys.stderr)

def main() -> int:
    parser = argparse.ArgumentParser(description='Ranked multi-timeframe Ichimoku scanner with Telegram delivery')
    parser.add_argument('--market', choices=['crypto', 'us', 'all'], default='all')
    parser.add_argument('--dry-run', action='store_true', help='Scan and print signals without Telegram delivery')
    parser.add_argument('--test-telegram', action='store_true', help='Send a retry-protected Telegram test')
    parser.add_argument('--defer-delivery', action='store_true', help='Scan and queue signals without sending Telegram messages')
    parser.add_argument('--deliver-pending', action='store_true', help='Deliver queued signals without running a full scan')
    parser.add_argument('--quiet-when-empty', action='store_true', help='Do not send a no-signal message when a catch-up delivery has no work')
    args = parser.parse_args()
    for directory in [DATA_DIR, CHART_DIR, REPORT_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
    if args.test_telegram:
        test_telegram()
        return 0
    state = load_json(STATE_PATH, {})
    try:
        if args.defer_delivery and args.deliver_pending:
            raise ScannerError('--defer-delivery and --deliver-pending cannot be combined')
        markets = ['crypto', 'us'] if args.market == 'all' else [args.market]
        for market in markets:
            run_id = str(uuid.uuid4())
            mode = 'delivery' if args.deliver_pending else ('deferred' if args.defer_delivery else 'live')
            run_started_at = now_utc_iso()
            if not args.dry_run:
                save_scanner_run_record(run_id, market, mode, 'running', started_at=run_started_at)
            try:
                if args.deliver_pending:
                    delivered, stats = deliver_pending_market(market, state, args.dry_run, quiet_when_empty=args.quiet_when_empty)
                else:
                    delivered, stats = run_market(market, state, args.dry_run, defer_delivery=args.defer_delivery)
            except Exception as exc:
                if not args.dry_run:
                    failed_stats = ScanStats(market=market)
                    save_scanner_run_record(run_id, market, mode, 'failed', failed_stats, str(exc)[:1000], started_at=run_started_at)
                raise
            if not args.dry_run:
                delivery_failures = stats.digest_failed + stats.details_failed
                save_scanner_run_record(run_id, market, mode, 'partial' if delivery_failures else 'completed', stats, started_at=run_started_at)
            print(f'Completed {market}: delivered={len(delivered)} deferred={stats.delivery_deferred} attempted={stats.symbols_attempted} valid={stats.symbols_with_data} errors={stats.symbols_failed} elapsed={stats.elapsed_seconds}s')
        return 0
    except Exception as exc:
        save_json(STATE_PATH, state)
        message = f'❌ <b>Ichimoku scanner failed</b>\nMarket: {html.escape(args.market)}\nError: {html.escape(str(exc)[:800])}\nTime UTC: {now_utc_iso()}'
        print(message, file=sys.stderr)
        if not args.dry_run:
            try:
                send_telegram_message(message)
            except Exception as send_exc:
                print(f'Could not send failure alert: {send_exc}', file=sys.stderr)
        return 1
if __name__ == '__main__':
    raise SystemExit(main())
