"""
Download equity OHLCV data from Yahoo Finance and save as parquet.

Saves in the same format as crypto data so the existing backtest engine works unchanged.
Uses 1h bars (730 days of history from yfinance) — with 10 ETFs this gives comparable
signal density to 30m bars on 3 crypto symbols.

Usage:
    uv run engine/equity_data.py                          # download all default ETFs
    uv run engine/equity_data.py --symbols SPY QQQ GLD    # specific tickers
    uv run engine/equity_data.py --interval 1h             # default
"""

import os
import sys
import time
import logging
import argparse

import numpy as np
import pandas as pd
import yfinance as yf
import pyarrow.parquet as pq
import pyarrow as pa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# 10 non-correlated ETFs covering different asset classes
EQUITY_SYMBOLS = [
    "SPY",   # S&P 500 — broad US large-cap
    "QQQ",   # Nasdaq 100 — tech/growth
    "IWM",   # Russell 2000 — small-cap
    "XLE",   # Energy Select SPDR — oil/gas/energy
    "XLF",   # Financial Select SPDR — banks/insurance
    "GLD",   # Gold — commodity, low equity correlation
    "TLT",   # 20+ Year Treasury — bonds, counter-cyclical
    "EEM",   # Emerging Markets — international
    "XBI",   # Biotech — high vol, sector-specific
    "SOXX",  # Semiconductors — high vol tech sub-sector
]

# Correlation profile (approximate pairwise correlations to SPY):
# SPY: 1.00 | QQQ: 0.90 | IWM: 0.85 | XLE: 0.55 | XLF: 0.75
# GLD: 0.05 | TLT: -0.30 | EEM: 0.65 | XBI: 0.60 | SOXX: 0.80
# GLD and TLT are the key diversifiers — often move opposite to equities.

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
_data_dir_env = os.environ.get("AUTOTRADER_DATA_DIR")
if _data_dir_env:
    DATA_DIR = os.path.realpath(_data_dir_env)
else:
    DATA_DIR = os.path.join(CACHE_DIR, "data")

# yfinance interval limits:
# 1h  -> max 730 days
# 30m -> max 60 days
# 15m -> max 60 days
YFINANCE_MAX_DAYS = {
    "1h": 729,    # yfinance hard limit
    "30m": 59,
    "15m": 59,
}


def download_equity_data(symbol: str, interval: str = "1h",
                         start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """Download OHLCV data for an equity ticker from Yahoo Finance.

    Returns DataFrame with columns: timestamp, open, high, low, close, volume, funding_rate
    (funding_rate is always 0 for equities — kept for engine compatibility).
    """
    max_days = YFINANCE_MAX_DAYS.get(interval, 729)

    ticker = yf.Ticker(symbol)

    if start_date and end_date:
        df = ticker.history(start=start_date, end=end_date, interval=interval)
    else:
        df = ticker.history(period=f"{max_days}d", interval=interval)

    if df.empty:
        logger.warning(f"{symbol}: no data returned from yfinance")
        return pd.DataFrame()

    # yfinance returns tz-aware DatetimeIndex — convert to UTC ms timestamps
    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else "Date"
    df["timestamp"] = df[ts_col].apply(
        lambda x: int(x.tz_convert("UTC").timestamp() * 1000) if x.tzinfo else int(x.timestamp() * 1000)
    )

    result = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low": df["Low"].astype(float),
        "close": df["Close"].astype(float),
        "volume": df["Volume"].astype(float),
        "funding_rate": 0.0,  # no funding for equities
    })

    # Drop any rows with NaN prices
    result = result.dropna(subset=["open", "high", "low", "close"])

    # Sort and deduplicate
    result = result.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # Filter to regular trading hours only (9:30-16:00 ET)
    # yfinance 1h data is already market-hours-only, but be safe
    if interval in ("1h", "30m", "15m"):
        result["hour"] = pd.to_datetime(result["timestamp"], unit="ms", utc=True) \
            .dt.tz_convert("US/Eastern").dt.hour
        result["minute"] = pd.to_datetime(result["timestamp"], unit="ms", utc=True) \
            .dt.tz_convert("US/Eastern").dt.minute
        # Market hours: 9:30 - 16:00 ET
        market_open = (result["hour"] > 9) | ((result["hour"] == 9) & (result["minute"] >= 30))
        market_close = result["hour"] < 16
        result = result[market_open & market_close].drop(columns=["hour", "minute"]).reset_index(drop=True)

    logger.info(f"{symbol}: {len(result)} bars downloaded ({interval})")
    return result


def save_equity_data(symbol: str, df: pd.DataFrame, interval: str = "1h"):
    """Save equity data as parquet in the same format as crypto data."""
    os.makedirs(DATA_DIR, exist_ok=True)
    filepath = os.path.join(DATA_DIR, f"{symbol}_{interval}.parquet")

    if df.empty:
        logger.warning(f"{symbol}: no data to save")
        return

    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, filepath)

    # Date range info
    start = pd.Timestamp(df["timestamp"].iloc[0], unit="ms", tz="UTC")
    end = pd.Timestamp(df["timestamp"].iloc[-1], unit="ms", tz="UTC")
    logger.info(f"{symbol}: saved {len(df)} bars to {filepath}")
    logger.info(f"  range: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")


def download_all(symbols: list = None, interval: str = "1h"):
    """Download and cache data for all equity symbols."""
    if symbols is None:
        symbols = EQUITY_SYMBOLS

    results = {}
    for symbol in symbols:
        try:
            df = download_equity_data(symbol, interval=interval)
            if not df.empty:
                save_equity_data(symbol, df, interval=interval)
                results[symbol] = len(df)
            time.sleep(0.5)  # rate limit courtesy
        except Exception as e:
            logger.error(f"{symbol}: download failed — {e}")
            continue

    # Summary
    print("\n=== EQUITY DATA DOWNLOAD SUMMARY ===")
    print(f"Interval: {interval}")
    print(f"{'Symbol':<8} {'Bars':>8} {'Status':<10}")
    print("-" * 30)
    for sym in symbols:
        bars = results.get(sym, 0)
        status = "OK" if bars > 0 else "FAILED"
        print(f"{sym:<8} {bars:>8} {status:<10}")
    print(f"\nTotal: {sum(results.values())} bars across {len(results)}/{len(symbols)} symbols")
    print(f"Data dir: {DATA_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download equity data from Yahoo Finance")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=f"Tickers to download (default: {EQUITY_SYMBOLS})")
    parser.add_argument("--interval", default="1h",
                        help="Bar interval (default: 1h, options: 1h, 30m, 15m)")
    args = parser.parse_args()

    download_all(symbols=args.symbols, interval=args.interval)
