from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests
import yfinance as yf

import config

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CHART_DIR = ROOT / "charts"
STATE_PATH = DATA_DIR / "scan_state.json"
HEARTBEAT_PATH = DATA_DIR / "heartbeat.json"
SUMMARY_PATH = DATA_DIR / "last_run_summary.json"

BINANCE_BASE = "https://data-api.binance.vision"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

REQUEST_TIMEOUT = 25
USER_AGENT = "Mozilla/5.0 ichimoku-telegram-scanner/1.0"


class ScannerError(Exception):
    pass


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        txt = path.read_text(encoding="utf-8").strip()
        if not txt:
            return default
        return json.loads(txt)
    except Exception as exc:
        print(f"Warning: failed to load {path}: {exc}", file=sys.stderr)
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def safe_float(value) -> Optional[float]:
    try:
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def clean_yahoo_symbol(symbol: str) -> Optional[str]:
    """Convert exchange symbol to a yfinance-friendly symbol where possible."""
    if not symbol:
        return None
    s = str(symbol).strip()
    if not s or s in {"Symbol", "ACT Symbol"}:
        return None
    # Skip warrants/rights/preferred-like symbols that often break free data.
    # Keep normal classes like BRK.B by converting to Yahoo style BRK-B.
    if "$" in s or " " in s:
        return None
    s = s.replace(".", "-")
    if not re.match(r"^[A-Za-z0-9=^\-]+$", s):
        return None
    return s.upper()


def read_pipe_symbol_file(url: str, symbol_column: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    lines = [ln for ln in resp.text.splitlines() if "|" in ln and not ln.startswith("File Creation Time")]
    if not lines:
        return pd.DataFrame()
    return pd.read_csv(StringIO("\n".join(lines)), sep="|")


def get_us_stock_symbols() -> List[str]:
    symbols: List[str] = []

    try:
        nasdaq = read_pipe_symbol_file(NASDAQ_LISTED_URL, "Symbol")
        if not nasdaq.empty:
            if "Test Issue" in nasdaq.columns:
                nasdaq = nasdaq[nasdaq["Test Issue"].astype(str).str.upper().eq("N")]
            for raw in nasdaq.get("Symbol", []):
                s = clean_yahoo_symbol(raw)
                if s:
                    symbols.append(s)
    except Exception as exc:
        print(f"Warning: could not load Nasdaq listed symbols: {exc}", file=sys.stderr)

    try:
        other = read_pipe_symbol_file(OTHER_LISTED_URL, "ACT Symbol")
        if not other.empty:
            if "Test Issue" in other.columns:
                other = other[other["Test Issue"].astype(str).str.upper().eq("N")]
            for raw in other.get("ACT Symbol", []):
                s = clean_yahoo_symbol(raw)
                if s:
                    symbols.append(s)
    except Exception as exc:
        print(f"Warning: could not load other listed symbols: {exc}", file=sys.stderr)

    # Deduplicate while keeping order
    seen = set()
    out = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def get_binance_spot_symbols() -> List[str]:
    url = f"{BINANCE_BASE}/api/v3/exchangeInfo"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    data = resp.json()
    quote_filter = [q.upper() for q in getattr(config, "CRYPTO_QUOTE_ASSETS", [])]
    symbols = []
    for item in data.get("symbols", []):
        if item.get("status") != "TRADING":
            continue
        if not item.get("isSpotTradingAllowed", True):
            continue
        symbol = item.get("symbol")
        quote = str(item.get("quoteAsset", "")).upper()
        if quote_filter and quote not in quote_filter:
            continue
        if symbol:
            symbols.append(symbol)
    return sorted(set(symbols))


def fetch_binance_ohlcv(symbol: str, limit: int) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "interval": "1d", "limit": min(max(limit, 10), 1000)}
    url = f"{BINANCE_BASE}/api/v3/klines"
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        if resp.status_code in {418, 429}:
            raise ScannerError(f"Binance rate limit/block status {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        now_ms = int(time.time() * 1000)
        clean_rows = []
        for r in rows:
            # Exclude the still-open daily candle. Kline close time must be in the past.
            if int(r[6]) > now_ms:
                continue
            clean_rows.append(r)
        if len(clean_rows) < config.SPAN_B_LENGTH + 2 * config.DISPLACEMENT + 5:
            return None
        df = pd.DataFrame(clean_rows, columns=[
            "OpenTime", "Open", "High", "Low", "Close", "Volume", "CloseTime",
            "QuoteVolume", "Trades", "TakerBase", "TakerQuote", "Ignore",
        ])
        df["Date"] = pd.to_datetime(df["CloseTime"], unit="ms", utc=True).dt.tz_convert(None)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df
    except Exception as exc:
        print(f"Warning: Binance fetch failed for {symbol}: {exc}", file=sys.stderr)
        return None


def fetch_yfinance_batch(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    try:
        raw = yf.download(
            tickers=" ".join(symbols),
            period=f"{int(config.LOOKBACK_DAYS)}d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            timeout=30,
        )
    except Exception as exc:
        print(f"Warning: yfinance batch failed ({symbols[:3]}...): {exc}", file=sys.stderr)
        return {}

    out: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = extract_symbol_from_yfinance(raw, symbol)
        if df is not None and len(df) >= config.SPAN_B_LENGTH + 2 * config.DISPLACEMENT + 5:
            out[symbol] = df
    return out


def extract_symbol_from_yfinance(raw: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    if raw is None or raw.empty:
        return None
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            # group_by='ticker' usually creates level 0 = ticker, level 1 = OHLCV.
            if symbol in raw.columns.get_level_values(0):
                df = raw[symbol].copy()
            elif symbol in raw.columns.get_level_values(1):
                df = raw.xs(symbol, axis=1, level=1).copy()
            else:
                return None
        else:
            df = raw.copy()
        needed = ["Open", "High", "Low", "Close"]
        if not all(c in df.columns for c in needed):
            return None
        if "Volume" not in df.columns:
            df["Volume"] = 0
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as exc:
        print(f"Warning: could not parse yfinance data for {symbol}: {exc}", file=sys.stderr)
        return None


def add_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    high = df["High"]
    low = df["Low"]
    conv = (high.rolling(config.CONVERSION_LENGTH).max() + low.rolling(config.CONVERSION_LENGTH).min()) / 2
    base = (high.rolling(config.BASE_LENGTH).max() + low.rolling(config.BASE_LENGTH).min()) / 2
    span_a_raw = (conv + base) / 2
    span_b_raw = (high.rolling(config.SPAN_B_LENGTH).max() + low.rolling(config.SPAN_B_LENGTH).min()) / 2

    # Visible cloud at each candle: raw spans shifted forward by displacement.
    span_a = span_a_raw.shift(config.DISPLACEMENT)
    span_b = span_b_raw.shift(config.DISPLACEMENT)

    df["Tenkan"] = conv
    df["Kijun"] = base
    df["SpanA"] = span_a
    df["SpanB"] = span_b
    df["CloudTop"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["CloudBottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)

    # For chart display only: current close plotted backward.
    df["LaggingDisplay"] = df["Close"].shift(-config.DISPLACEMENT)
    return df


def combined_condition_at(df: pd.DataFrame, pos: int) -> Tuple[bool, bool, Dict[str, Optional[float]]]:
    """
    Returns bullish, bearish, info.

    Price condition:
      close[pos] above/below visible cloud at pos.

    Lagging span condition, standard chart logic:
      close[pos] is plotted DISPLACEMENT candles back, so compare close[pos]
      with the visible cloud at pos - DISPLACEMENT.
    """
    if pos < 0:
        pos = len(df) + pos
    lag_pos = pos - config.DISPLACEMENT
    if pos <= 0 or lag_pos < 0 or pos >= len(df):
        return False, False, {}

    close = safe_float(df["Close"].iloc[pos])
    cloud_top = safe_float(df["CloudTop"].iloc[pos])
    cloud_bottom = safe_float(df["CloudBottom"].iloc[pos])
    lag_cloud_top = safe_float(df["CloudTop"].iloc[lag_pos])
    lag_cloud_bottom = safe_float(df["CloudBottom"].iloc[lag_pos])

    if None in {close, cloud_top, cloud_bottom, lag_cloud_top, lag_cloud_bottom}:
        return False, False, {}

    price_above = close > cloud_top
    price_below = close < cloud_bottom
    lag_above = close > lag_cloud_top
    lag_below = close < lag_cloud_bottom

    bullish = bool(price_above and lag_above)
    bearish = bool(price_below and lag_below)
    info = {
        "close": close,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "lag_cloud_top": lag_cloud_top,
        "lag_cloud_bottom": lag_cloud_bottom,
        "price_above": price_above,
        "price_below": price_below,
        "lag_above": lag_above,
        "lag_below": lag_below,
    }
    return bullish, bearish, info


def detect_fresh_signal(df: pd.DataFrame) -> Optional[Dict]:
    df = add_ichimoku(df)
    if len(df) < config.SPAN_B_LENGTH + 2 * config.DISPLACEMENT + 5:
        return None

    today_pos = len(df) - 1
    prev_pos = len(df) - 2
    bull_today, bear_today, info_today = combined_condition_at(df, today_pos)
    bull_prev, bear_prev, _ = combined_condition_at(df, prev_pos)

    signal_type = None
    if bull_today and not bull_prev:
        signal_type = "bullish"
    elif bear_today and not bear_prev:
        signal_type = "bearish"
    else:
        return None

    signal_date = pd.Timestamp(df.index[today_pos]).strftime("%Y-%m-%d")
    lag_date = pd.Timestamp(df.index[today_pos - config.DISPLACEMENT]).strftime("%Y-%m-%d")
    return {
        "type": signal_type,
        "date": signal_date,
        "lagging_compare_date": lag_date,
        "info": info_today,
        "df": df,
    }


def make_chart(symbol: str, market: str, signal: Dict) -> Optional[Path]:
    if not config.SEND_CHART_IMAGES:
        return None
    try:
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        df = signal["df"].tail(config.CHART_LOOKBACK_CANDLES).copy()
        if df.empty:
            return None
        signal_date = pd.Timestamp(signal["date"])
        sig_series = pd.Series(np.nan, index=df.index)
        if signal_date in df.index:
            close_val = df.loc[signal_date, "Close"]
            sig_series.loc[signal_date] = close_val

        apds = [
            mpf.make_addplot(df["Tenkan"], width=0.9, label="Tenkan 20"),
            mpf.make_addplot(df["Kijun"], width=0.9, label="Kijun 60"),
            mpf.make_addplot(df["LaggingDisplay"], width=0.8, label="Lagging 30"),
        ]
        marker = "^" if signal["type"] == "bullish" else "v"
        apds.append(mpf.make_addplot(sig_series, type="scatter", marker=marker, markersize=130, label="Signal"))

        title = f"{symbol} | {market} | 1D | {signal['type'].upper()} | {signal['date']}"
        filename = re.sub(r"[^A-Za-z0-9_.=-]+", "_", f"{market}_{symbol}_{signal['type']}_{signal['date']}.png")
        path = CHART_DIR / filename

        fill_between = dict(y1=df["SpanA"].values, y2=df["SpanB"].values, alpha=0.20)
        mpf.plot(
            df[["Open", "High", "Low", "Close", "Volume"]],
            type="candle",
            volume=True,
            style="yahoo",
            addplot=apds,
            fill_between=fill_between,
            title=title,
            ylabel="Price",
            ylabel_lower="Volume",
            figsize=(13, 8),
            warn_too_much_data=10000,
            savefig=dict(fname=str(path), dpi=140, bbox_inches="tight"),
        )
        plt.close("all")
        return path
    except Exception as exc:
        print(f"Warning: chart failed for {symbol}: {exc}", file=sys.stderr)
        return None


def telegram_request(method: str, data: Dict, files: Optional[Dict] = None) -> Dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ScannerError("Missing TELEGRAM_BOT_TOKEN environment variable / GitHub Secret")
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, data=data, files=files, timeout=60)
    if not resp.ok:
        raise ScannerError(f"Telegram {method} failed: {resp.status_code} {resp.text[:500]}")
    return resp.json()


def send_telegram_message(text: str) -> None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise ScannerError("Missing TELEGRAM_CHAT_ID environment variable / GitHub Secret")
    telegram_request("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def send_telegram_photo(caption: str, path: Path) -> None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise ScannerError("Missing TELEGRAM_CHAT_ID environment variable / GitHub Secret")
    with path.open("rb") as f:
        telegram_request(
            "sendPhoto",
            {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"photo": f},
        )


def format_alert(symbol: str, market: str, signal: Dict) -> str:
    info = signal.get("info", {})
    direction = signal["type"].upper()
    emoji = "🟢" if signal["type"] == "bullish" else "🔴"
    close = info.get("close")
    cloud_top = info.get("cloud_top")
    cloud_bottom = info.get("cloud_bottom")
    lag_cloud_top = info.get("lag_cloud_top")
    lag_cloud_bottom = info.get("lag_cloud_bottom")
    cloud_ref = cloud_top if signal["type"] == "bullish" else cloud_bottom
    lag_ref = lag_cloud_top if signal["type"] == "bullish" else lag_cloud_bottom
    dist = None
    if close and cloud_ref:
        dist = (close - cloud_ref) / cloud_ref * 100
        if signal["type"] == "bearish":
            dist = (cloud_ref - close) / cloud_ref * 100

    lines = [
        f"{emoji} <b>{direction} ICHIMOKU CLOUD SIGNAL</b>",
        "",
        f"<b>Ticker:</b> {symbol}",
        f"<b>Market:</b> {market}",
        "<b>Timeframe:</b> 1D",
        f"<b>Signal candle:</b> {signal['date']}",
        f"<b>Close:</b> {close:.8g}" if isinstance(close, (float, int)) else f"<b>Close:</b> {close}",
        "",
        "<b>Criteria:</b>",
    ]
    if signal["type"] == "bullish":
        lines.append(f"• Price closed above today's cloud top: {cloud_top:.8g}")
        lines.append(f"• Lagging span closed above cloud top at {signal['lagging_compare_date']}: {lag_cloud_top:.8g}")
    else:
        lines.append(f"• Price closed below today's cloud bottom: {cloud_bottom:.8g}")
        lines.append(f"• Lagging span closed below cloud bottom at {signal['lagging_compare_date']}: {lag_cloud_bottom:.8g}")
    if dist is not None:
        lines.append(f"• Distance beyond cloud: {dist:.2f}%")
    lines.extend([
        "",
        f"<b>Ichimoku:</b> {config.CONVERSION_LENGTH}/{config.BASE_LENGTH}/{config.SPAN_B_LENGTH}/{config.DISPLACEMENT}",
        "<b>Fresh rule:</b> combined condition became true on this closed candle.",
    ])
    return "\n".join(lines)


def state_key(market: str, symbol: str, signal_type: str) -> str:
    return f"{market}|{symbol}|1D|{signal_type}"


def should_send_and_update_state(state: Dict, market: str, symbol: str, signal: Dict) -> bool:
    key = state_key(market, symbol, signal["type"])
    last_date = state.get(key)
    if last_date == signal["date"]:
        return False
    state[key] = signal["date"]
    return True


def scan_dataframe(symbol: str, market: str, df: pd.DataFrame, state: Dict, dry_run: bool = False) -> Optional[Dict]:
    signal = detect_fresh_signal(df)
    if not signal:
        return None
    if not should_send_and_update_state(state, market, symbol, signal):
        return None

    alert = {
        "symbol": symbol,
        "market": market,
        "type": signal["type"],
        "date": signal["date"],
        "close": signal["info"].get("close"),
    }
    caption = format_alert(symbol, market, signal)
    chart = make_chart(symbol, market, signal)
    if dry_run:
        print("DRY RUN SIGNAL:", json.dumps(alert, default=str))
    else:
        if chart and chart.exists():
            send_telegram_photo(caption, chart)
        else:
            send_telegram_message(caption)
    return alert


def chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def limit_symbols(symbols: List[str]) -> List[str]:
    max_n = getattr(config, "MAX_SYMBOLS_PER_MARKET", None)
    if max_n is not None:
        return symbols[: int(max_n)]
    return symbols


def scan_crypto(state: Dict, dry_run: bool = False) -> List[Dict]:
    symbols = limit_symbols(get_binance_spot_symbols())
    print(f"Crypto symbols: {len(symbols)}")
    alerts: List[Dict] = []
    for idx, symbol in enumerate(symbols, 1):
        if len(alerts) >= config.MAX_ALERTS_PER_RUN:
            print("Reached MAX_ALERTS_PER_RUN; stopping crypto scan.")
            break
        df = fetch_binance_ohlcv(symbol, int(config.LOOKBACK_DAYS))
        if df is not None:
            alert = scan_dataframe(symbol, "Crypto Spot", df, state, dry_run=dry_run)
            if alert:
                alerts.append(alert)
        if idx % 100 == 0:
            print(f"Crypto progress: {idx}/{len(symbols)}, alerts={len(alerts)}")
        # Be gentle with the free public API.
        time.sleep(0.06)
    return alerts


def scan_yfinance_symbols(symbols: List[str], market: str, state: Dict, dry_run: bool = False) -> List[Dict]:
    symbols = limit_symbols(symbols)
    print(f"{market} symbols: {len(symbols)}")
    alerts: List[Dict] = []
    batch_size = int(getattr(config, "YFINANCE_BATCH_SIZE", 80))
    for batch_no, batch in enumerate(chunks(symbols, batch_size), 1):
        if len(alerts) >= config.MAX_ALERTS_PER_RUN:
            print(f"Reached MAX_ALERTS_PER_RUN; stopping {market} scan.")
            break
        data = fetch_yfinance_batch(batch)
        for symbol, df in data.items():
            alert = scan_dataframe(symbol, market, df, state, dry_run=dry_run)
            if alert:
                alerts.append(alert)
                if len(alerts) >= config.MAX_ALERTS_PER_RUN:
                    break
        print(f"{market} batch {batch_no}: checked {len(batch)} symbols, alerts={len(alerts)}")
        time.sleep(1.0)
    return alerts


def scan_us_markets(state: Dict, dry_run: bool = False) -> List[Dict]:
    alerts: List[Dict] = []
    stocks = get_us_stock_symbols()
    alerts.extend(scan_yfinance_symbols(stocks, "US Stock", state, dry_run=dry_run))
    alerts.extend(scan_yfinance_symbols(config.US_INDEX_SYMBOLS, "US Index", state, dry_run=dry_run))
    alerts.extend(scan_yfinance_symbols(config.COMMODITY_FUTURES_SYMBOLS, "Commodity Future", state, dry_run=dry_run))
    return alerts


def write_heartbeat(market: str, alerts: List[Dict]) -> None:
    hb = load_json(HEARTBEAT_PATH, {})
    hb[market] = {
        "last_run_utc": now_utc_iso(),
        "alerts_count": len(alerts),
    }
    save_json(HEARTBEAT_PATH, hb)

    summary = load_json(SUMMARY_PATH, {})
    summary[market] = {
        "last_run_utc": now_utc_iso(),
        "alerts": alerts,
    }
    save_json(SUMMARY_PATH, summary)


def test_telegram() -> None:
    text = (
        "✅ <b>Ichimoku scanner Telegram test successful</b>\n\n"
        f"Time UTC: {now_utc_iso()}\n"
        f"Settings: {config.CONVERSION_LENGTH}/{config.BASE_LENGTH}/{config.SPAN_B_LENGTH}/{config.DISPLACEMENT}\n"
        "If you received this, your bot token and chat ID are working."
    )
    send_telegram_message(text)
    print("Telegram test sent.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Ichimoku cloud scanner with Telegram alerts")
    parser.add_argument("--market", choices=["crypto", "us", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Print signals instead of sending Telegram alerts")
    parser.add_argument("--test-telegram", action="store_true", help="Send one test Telegram message and exit")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    CHART_DIR.mkdir(exist_ok=True)

    if args.test_telegram:
        test_telegram()
        return 0

    state = load_json(STATE_PATH, {})
    start = time.time()
    all_alerts: List[Dict] = []

    try:
        if args.market in {"crypto", "all"}:
            crypto_alerts = scan_crypto(state, dry_run=args.dry_run)
            write_heartbeat("crypto", crypto_alerts)
            all_alerts.extend(crypto_alerts)
        if args.market in {"us", "all"}:
            us_alerts = scan_us_markets(state, dry_run=args.dry_run)
            write_heartbeat("us", us_alerts)
            all_alerts.extend(us_alerts)

        save_json(STATE_PATH, state)
        elapsed = round(time.time() - start, 1)
        print(f"Done. Alerts sent/found: {len(all_alerts)}. Elapsed seconds: {elapsed}")

        if config.SEND_RUN_SUMMARY and not args.dry_run:
            send_telegram_message(
                f"✅ <b>Ichimoku scanner run complete</b>\n"
                f"Market: {args.market}\n"
                f"Alerts: {len(all_alerts)}\n"
                f"Time UTC: {now_utc_iso()}\n"
                f"Elapsed: {elapsed}s"
            )
        return 0
    except Exception as exc:
        # Try to notify Telegram about fatal failure, then fail the GitHub Action.
        msg = f"❌ <b>Ichimoku scanner failed</b>\nMarket: {args.market}\nError: {str(exc)[:800]}\nTime UTC: {now_utc_iso()}"
        print(msg, file=sys.stderr)
        try:
            if not args.dry_run:
                send_telegram_message(msg)
        except Exception as send_exc:
            print(f"Could not send failure alert: {send_exc}", file=sys.stderr)
        save_json(STATE_PATH, state)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
