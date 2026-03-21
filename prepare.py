"""
Autotrader backtesting engine. Fixed evaluation harness — DO NOT MODIFY.
Downloads Hyperliquid historical data, runs backtests, computes scores.

Usage:
    python prepare.py                  # download data
    python prepare.py --symbols BTC    # download specific symbols
"""

import os
import sys
import time
import math
import signal
import hashlib
import json
import logging
import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------

TIME_BUDGET = 120              # backtest time budget in seconds (2 minutes)
INITIAL_CAPITAL = 100_000.0    # $100K starting capital
MAKER_FEE = 0.0002             # 2 bps
TAKER_FEE = 0.0005             # 5 bps
SLIPPAGE_BPS = 1.0             # 1 bps simulated slippage
MAX_LEVERAGE = 20              # max leverage allowed
LOOKBACK_BARS = 500            # history buffer provided to strategy
BAR_INTERVAL = "1h"

SYMBOLS = ["BTC", "ETH", "SOL"]

# Date splits (UTC timestamps)
TRAIN_START = "2023-06-01"
TRAIN_END = "2024-06-30"
VAL_START = "2024-07-01"
VAL_END = "2025-03-31"
TEST_START = "2025-04-01"
TEST_END = "2025-12-31"

HOURS_PER_YEAR = 8760

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
_data_dir_env = os.environ.get("AUTOTRADER_DATA_DIR")
if _data_dir_env:
    DATA_DIR = os.path.realpath(_data_dir_env)
    # Validate: must be under home dir or /app (Docker) to prevent path traversal
    # Use commonpath to prevent prefix attacks (e.g. /app-malicious, /home/user2)
    _home = os.path.realpath(os.path.expanduser("~"))
    _allowed_roots = [_home, "/app"]
    _path_ok = False
    for _root in _allowed_roots:
        try:
            if os.path.commonpath([DATA_DIR, _root]) == _root:
                _path_ok = True
                break
        except ValueError:
            continue
    if not _path_ok:
        raise ValueError(
            f"AUTOTRADER_DATA_DIR must be under home directory or /app, "
            f"got: {DATA_DIR}"
        )
else:
    DATA_DIR = os.path.join(CACHE_DIR, "data")

CHECKSUM_FILE = os.path.join(DATA_DIR, "data_checksums.json")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BarData:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float
    history: pd.DataFrame  # last LOOKBACK_BARS bars

@dataclass
class Signal:
    symbol: str
    target_position: float   # target USD notional (signed: +long, -short)
    order_type: str = "market"

@dataclass
class PortfolioState:
    cash: float
    positions: dict          # symbol -> signed USD notional
    entry_prices: dict       # symbol -> avg entry price
    equity: float = 0.0
    timestamp: int = 0

@dataclass
class BacktestResult:
    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    annual_turnover: float = 0.0
    backtest_seconds: float = 0.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)

# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/histohour"

# Secure HTTP session with TLS verification, retries, and backoff
_http_session = requests.Session()
_http_session.verify = True  # Explicit TLS verification
_retry = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    respect_retry_after_header=True,
)
_http_session.mount("https://", HTTPAdapter(max_retries=_retry))
_http_session.mount("http://", HTTPAdapter(max_retries=_retry))

def _validate_api_response(data, expected_type=list):
    """Validate API response structure."""
    if data is None:
        raise ValueError("API returned null response")
    if not isinstance(data, expected_type) and expected_type == list:
        if isinstance(data, dict) and "Data" in data:
            return  # CryptoCompare wraps in dict
        raise ValueError(f"Expected {expected_type.__name__}, got {type(data).__name__}")


def _download_cryptocompare_candles(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download hourly OHLCV from CryptoCompare (no geo-restrictions)."""
    all_rows = []
    current_end = end_ms // 1000
    start_s = start_ms // 1000

    while current_end > start_s:
        params = {
            "fsym": symbol,
            "tsym": "USD",
            "limit": 2000,
            "toTs": current_end,
        }
        resp = _http_session.get(CRYPTOCOMPARE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Validate response structure
        if not isinstance(data, dict) or "Data" not in data:
            logger.error(f"Unexpected CryptoCompare response for {symbol}: {str(data)[:200]}")
            break

        bars = data.get("Data", {}).get("Data", [])
        if not isinstance(bars, list) or not bars:
            break

        for bar in bars:
            ts_s = bar.get("time")
            if ts_s is None or ts_s < start_s:
                continue
            try:
                all_rows.append({
                    "timestamp": ts_s * 1000,
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": float(bar.get("volumefrom", 0)),
                })
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed bar for {symbol}: {e}")
                continue

        earliest = bars[0].get("time", current_end)
        if earliest >= current_end:
            break
        current_end = earliest - 1
        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def _download_hl_funding(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download funding rate history from Hyperliquid."""
    all_rows = []
    current = start_ms
    while current < end_ms:
        body = {
            "type": "fundingHistory",
            "coin": symbol,
            "startTime": current,
            "endTime": min(current + 30 * 24 * 3600 * 1000, end_ms),
        }
        try:
            resp = _http_session.post(HL_INFO_URL, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                logger.warning(f"Empty or invalid funding response for {symbol} at {current}")
                break
            for row in data:
                all_rows.append({
                    "timestamp": int(row["time"]),
                    "funding_rate": float(row["fundingRate"]),
                })
            current = int(data[-1]["time"]) + 1
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout downloading {symbol} funding at {current}")
            break
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning(f"Rate limited on {symbol} funding, backing off 60s")
                time.sleep(60)
                continue
            logger.error(f"HTTP error downloading {symbol} funding: {e}")
            raise
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Malformed funding data for {symbol}: {e}")
            break
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    return pd.DataFrame(all_rows)


def _download_hl_candles(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download OHLCV candles from Hyperliquid."""
    all_rows = []
    current = start_ms
    chunk_ms = 30 * 24 * 3600 * 1000
    while current < end_ms:
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": current,
                "endTime": min(current + chunk_ms, end_ms),
            }
        }
        try:
            resp = _http_session.post(HL_INFO_URL, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                current += chunk_ms
                continue
            for row in data:
                all_rows.append({
                    "timestamp": int(row["t"]),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row["v"]),
                })
            current = int(data[-1]["t"]) + 3600 * 1000
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout downloading {symbol} candles at {current}")
            current += chunk_ms
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning(f"Rate limited on {symbol} candles, backing off 60s")
                time.sleep(60)
                continue
            logger.error(f"HTTP error downloading {symbol} candles: {e}")
            raise
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Malformed candle data for {symbol}: {e}")
            current += chunk_ms
        time.sleep(0.2)
    return pd.DataFrame(all_rows)


def _validate_downloaded_data(df: pd.DataFrame, symbol: str) -> list[str]:
    """Validate downloaded data before caching. Returns list of errors."""
    errors = []

    required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        errors.append(f"Missing columns: {missing}")
        return errors

    if len(df) < 100:
        errors.append(f"Insufficient data: only {len(df)} bars")

    # Check for NaN in price columns
    for col in ["open", "high", "low", "close"]:
        nan_count = df[col].isna().sum()
        if nan_count > 0:
            errors.append(f"{col} has {nan_count} NaN values")

    # Check high >= low
    violations = (df["high"] < df["low"]).sum()
    if violations > 0:
        errors.append(f"{violations} bars where high < low")

    # Price sanity bounds
    bounds = {"BTC": (1_000, 5_000_000), "ETH": (50, 500_000), "SOL": (0.1, 50_000)}
    if symbol in bounds:
        lo, hi = bounds[symbol]
        for col in ["open", "high", "low", "close"]:
            col_min, col_max = df[col].min(), df[col].max()
            if col_min < lo:
                errors.append(f"{col} min ({col_min:.2f}) below bound ({lo})")
            if col_max > hi:
                errors.append(f"{col} max ({col_max:.2f}) above bound ({hi})")

    # Check volume is non-negative
    if (df["volume"] < 0).any():
        errors.append("Negative volume values found")

    return errors


def _save_data_checksum(symbol: str, filepath: str):
    """Store SHA-256 checksum of downloaded data file."""
    os.makedirs(os.path.dirname(CHECKSUM_FILE), exist_ok=True)
    checksums = {}
    if os.path.exists(CHECKSUM_FILE):
        with open(CHECKSUM_FILE) as f:
            checksums = json.load(f)

    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    checksums[os.path.basename(filepath)] = h.hexdigest()

    with open(CHECKSUM_FILE, "w") as f:
        json.dump(checksums, f, indent=2)


def download_data(symbols=None):
    """Download historical OHLCV + funding data for all symbols."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if symbols is None:
        symbols = SYMBOLS

    start_ms = int(pd.Timestamp(TRAIN_START, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(TEST_END, tz="UTC").timestamp() * 1000)

    for symbol in symbols:
        filepath = os.path.join(DATA_DIR, f"{symbol}_1h.parquet")
        if os.path.exists(filepath):
            existing = pd.read_parquet(filepath)
            logger.info(f"{symbol}: already have {len(existing)} bars")
            continue

        logger.info(f"{symbol}: downloading candles from CryptoCompare...")

        df = _download_cryptocompare_candles(symbol, start_ms, end_ms)
        if len(df) < 100:
            logger.info(f"{symbol}: CryptoCompare insufficient ({len(df)} bars), trying HL...")
            df = _download_hl_candles(symbol, "1h", start_ms, end_ms)

        if df.empty:
            logger.warning(f"{symbol}: NO DATA AVAILABLE, skipping")
            continue

        logger.info(f"{symbol}: downloading funding rates...")
        funding = _download_hl_funding(symbol, start_ms, end_ms)

        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if not funding.empty:
            funding = funding.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
            df = pd.merge_asof(df, funding, on="timestamp", direction="backward")
        if "funding_rate" not in df.columns:
            df["funding_rate"] = 0.0
        df["funding_rate"] = df["funding_rate"].fillna(0.0)

        # Validate data integrity BEFORE caching
        validation_errors = _validate_downloaded_data(df, symbol)
        if validation_errors:
            logger.error(f"{symbol}: data validation failed, NOT caching:")
            for err in validation_errors:
                logger.error(f"  - {err}")
            raise ValueError(f"Data validation failed for {symbol}: {validation_errors}")

        df.to_parquet(filepath, index=False)
        _save_data_checksum(symbol, filepath)
        logger.info(f"{symbol}: saved {len(df)} bars to {filepath} (checksum recorded)")


def load_data(split: str = "val") -> dict:
    """Load OHLCV+funding data for the given split. Returns {symbol: DataFrame}."""
    splits = {
        "train": (TRAIN_START, TRAIN_END),
        "val": (VAL_START, VAL_END),
        "test": (TEST_START, TEST_END),
    }
    assert split in splits, f"split must be one of {list(splits.keys())}"
    start_str, end_str = splits[split]
    start_ms = int(pd.Timestamp(start_str, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end_str, tz="UTC").timestamp() * 1000)

    result = {}
    for symbol in SYMBOLS:
        filepath = os.path.join(DATA_DIR, f"{symbol}_1h.parquet")
        if not os.path.exists(filepath):
            continue
        df = pd.read_parquet(filepath)
        mask = (df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)
        split_df = df[mask].reset_index(drop=True)
        if len(split_df) > 0:
            result[symbol] = split_df
    return result

# ---------------------------------------------------------------------------
# Backtesting engine (DO NOT CHANGE)
# ---------------------------------------------------------------------------

def run_backtest(strategy, data: dict) -> BacktestResult:
    """
    Run strategy over data. Returns BacktestResult with full metrics.
    Enforces TIME_BUDGET.
    """
    t_start = time.time()

    # Build unified timeline
    all_timestamps = set()
    for symbol, df in data.items():
        all_timestamps.update(df["timestamp"].tolist())
    timestamps = sorted(all_timestamps)

    if not timestamps:
        return BacktestResult()

    # Index data by (symbol, timestamp) for fast lookup
    indexed = {}
    for symbol, df in data.items():
        indexed[symbol] = df.set_index("timestamp")

    # Portfolio state
    portfolio = PortfolioState(
        cash=INITIAL_CAPITAL,
        positions={},
        entry_prices={},
        equity=INITIAL_CAPITAL,
        timestamp=0,
    )

    equity_curve = [INITIAL_CAPITAL]
    hourly_returns = []
    trade_log = []
    total_volume = 0.0
    prev_equity = INITIAL_CAPITAL

    # History buffers
    history_buffers = {symbol: [] for symbol in data}

    for ts in timestamps:
        elapsed = time.time() - t_start
        if elapsed > TIME_BUDGET:
            break

        portfolio.timestamp = ts

        # Build bar data
        bar_data = {}
        for symbol in data:
            if symbol not in indexed or ts not in indexed[symbol].index:
                continue
            row = indexed[symbol].loc[ts]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            # Update history buffer
            bar_dict = {
                "timestamp": ts,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "funding_rate": row.get("funding_rate", 0.0),
            }
            history_buffers[symbol].append(bar_dict)
            if len(history_buffers[symbol]) > LOOKBACK_BARS:
                history_buffers[symbol] = history_buffers[symbol][-LOOKBACK_BARS:]

            hist_df = pd.DataFrame(history_buffers[symbol])

            bar_data[symbol] = BarData(
                symbol=symbol,
                timestamp=ts,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                funding_rate=row.get("funding_rate", 0.0),
                history=hist_df,
            )

        if not bar_data:
            continue

        # Update portfolio equity (mark-to-market)
        unrealized_pnl = 0.0
        for sym, pos_notional in portfolio.positions.items():
            if sym in bar_data:
                current_price = bar_data[sym].close
                entry_price = portfolio.entry_prices.get(sym, current_price)
                if entry_price > 0:
                    price_change = (current_price - entry_price) / entry_price
                    unrealized_pnl += pos_notional * price_change

        portfolio.equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl

        # Apply funding rates (on open positions)
        # Funding is applied every 8 hours on Hyperliquid.
        # Only apply at 8-hour boundaries (0:00, 8:00, 16:00 UTC)
        ts_hours = (ts // (3600 * 1000)) % 24  # hour of day from ms timestamp
        is_funding_hour = ts_hours in (0, 8, 16)
        if is_funding_hour:
            for sym, pos_notional in list(portfolio.positions.items()):
                if sym in bar_data:
                    fr = bar_data[sym].funding_rate
                    # Funding: longs pay when positive, shorts receive
                    funding_payment = pos_notional * fr
                    portfolio.cash -= funding_payment

        # Get signals from strategy
        try:
            signals = strategy.on_bar(bar_data, portfolio)
        except Exception as e:
            logger.warning(f"strategy.on_bar() raised {type(e).__name__}: {e} at ts={ts}")
            signals = []

        # Execute signals
        for sig in (signals or []):
            if sig.symbol not in bar_data:
                continue

            current_price = bar_data[sig.symbol].close
            current_pos = portfolio.positions.get(sig.symbol, 0.0)
            delta = sig.target_position - current_pos

            if abs(delta) < 1.0:  # < $1 change, skip
                continue

            # Check leverage constraint
            new_positions = dict(portfolio.positions)
            new_positions[sig.symbol] = sig.target_position
            total_exposure = sum(abs(v) for v in new_positions.values())
            if total_exposure > portfolio.equity * MAX_LEVERAGE:
                continue

            # Apply slippage and fees
            slippage = current_price * SLIPPAGE_BPS / 10000
            fee_rate = TAKER_FEE
            if delta > 0:  # buying
                exec_price = current_price + slippage
            else:  # selling
                exec_price = current_price - slippage

            fee = abs(delta) * fee_rate
            portfolio.cash -= fee
            total_volume += abs(delta)

            # Update position
            pnl = 0.0  # Initialize per-signal to avoid scope leakage

            if sig.target_position == 0:
                # Closing position — realize PnL
                if sig.symbol in portfolio.entry_prices:
                    entry = portfolio.entry_prices[sig.symbol]
                    if entry > 0:
                        pnl = current_pos * (exec_price - entry) / entry
                        portfolio.cash += abs(current_pos) + pnl
                    del portfolio.entry_prices[sig.symbol]
                if sig.symbol in portfolio.positions:
                    del portfolio.positions[sig.symbol]
                trade_log.append(("close", sig.symbol, delta, exec_price, pnl))
            else:
                if current_pos == 0:
                    # Opening new position
                    portfolio.cash -= abs(sig.target_position)
                    portfolio.positions[sig.symbol] = sig.target_position
                    portfolio.entry_prices[sig.symbol] = exec_price
                    trade_log.append(("open", sig.symbol, delta, exec_price, 0))

                elif (current_pos > 0) != (sig.target_position > 0):
                    # Position REVERSAL (sign flip) — close old side, open new side
                    old_entry = portfolio.entry_prices.get(sig.symbol, exec_price)
                    # 1. Close the existing position entirely
                    if old_entry > 0 and abs(current_pos) > 0:
                        direction = 1.0 if current_pos > 0 else -1.0
                        pnl = direction * abs(current_pos) * (exec_price - old_entry) / old_entry
                    portfolio.cash += abs(current_pos) + pnl
                    trade_log.append(("close", sig.symbol, -current_pos, exec_price, pnl))
                    # 2. Open the new position on the other side
                    portfolio.cash -= abs(sig.target_position)
                    portfolio.positions[sig.symbol] = sig.target_position
                    portfolio.entry_prices[sig.symbol] = exec_price
                    trade_log.append(("open", sig.symbol, sig.target_position, exec_price, 0))

                else:
                    # Same-side modification (scaling up or down)
                    old_notional = abs(current_pos)
                    old_entry = portfolio.entry_prices.get(sig.symbol, exec_price)
                    if abs(sig.target_position) < abs(current_pos):
                        # Reducing position — realize PnL on reduced portion
                        reduced = abs(current_pos) - abs(sig.target_position)
                        if old_entry > 0 and abs(current_pos) > 0:
                            direction = 1.0 if current_pos > 0 else -1.0
                            pnl = direction * reduced * (exec_price - old_entry) / old_entry
                        portfolio.cash += reduced + pnl
                    elif abs(sig.target_position) > abs(current_pos):
                        # Increasing position — add notional, update weighted entry
                        added = abs(sig.target_position) - abs(current_pos)
                        portfolio.cash -= added
                        if old_notional + added > 0:
                            new_entry = (old_entry * old_notional + exec_price * added) / (old_notional + added)
                            portfolio.entry_prices[sig.symbol] = new_entry
                    portfolio.positions[sig.symbol] = sig.target_position
                    trade_log.append(("modify", sig.symbol, delta, exec_price, pnl))

        # Recalculate equity after trades
        unrealized_pnl = 0.0
        for sym, pos_notional in portfolio.positions.items():
            if sym in bar_data:
                current_price = bar_data[sym].close
                entry_price = portfolio.entry_prices.get(sym, current_price)
                if entry_price > 0:
                    price_change = (current_price - entry_price) / entry_price
                    unrealized_pnl += pos_notional * price_change

        current_equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl
        equity_curve.append(current_equity)

        # Hourly return
        if prev_equity > 0:
            hourly_returns.append((current_equity - prev_equity) / prev_equity)
        prev_equity = current_equity

        # Liquidation check
        if current_equity < INITIAL_CAPITAL * 0.01:
            break

    t_end = time.time()

    # Compute metrics
    returns = np.array(hourly_returns) if hourly_returns else np.array([0.0])
    eq = np.array(equity_curve)

    # Sharpe ratio (annualized from hourly)
    if returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * np.sqrt(HOURS_PER_YEAR)
    else:
        sharpe = 0.0

    # Total return
    final_equity = eq[-1] if len(eq) > 0 else INITIAL_CAPITAL
    total_return_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    drawdown = (peak - eq) / np.where(peak > 0, peak, 1)
    max_drawdown_pct = drawdown.max() * 100

    # Win rate and profit factor
    trade_pnls = [t[4] for t in trade_log if t[0] == "close"]
    num_trades = len(trade_log)
    if trade_pnls:
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        win_rate_pct = len(wins) / len(trade_pnls) * 100 if trade_pnls else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-10
        profit_factor = gross_profit / gross_loss
    else:
        win_rate_pct = 0.0
        profit_factor = 0.0

    # Annual turnover
    data_hours = len(timestamps)
    if data_hours > 0:
        annual_turnover = total_volume * (HOURS_PER_YEAR / data_hours)
    else:
        annual_turnover = 0.0

    return BacktestResult(
        sharpe=sharpe,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        num_trades=num_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        annual_turnover=annual_turnover,
        backtest_seconds=t_end - t_start,
        equity_curve=equity_curve,
        trade_log=trade_log,
    )

# ---------------------------------------------------------------------------
# Evaluation metric (DO NOT CHANGE — this is the fixed metric)
# ---------------------------------------------------------------------------

def compute_score(result: BacktestResult) -> float:
    """
    Composite risk-adjusted score (HIGHER is better).

    score = sharpe * sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty

    Hard cutoffs for degenerate strategies.
    """
    # Hard cutoffs
    if result.num_trades < 10:
        return -999.0
    if result.max_drawdown_pct > 50.0:
        return -999.0
    final_equity = result.equity_curve[-1] if result.equity_curve else INITIAL_CAPITAL
    if final_equity < INITIAL_CAPITAL * 0.5:
        return -999.0

    # Trade count factor: full credit at 50+ trades
    trade_count_factor = min(result.num_trades / 50.0, 1.0)

    # Drawdown penalty: no penalty below 15%, then 5x per additional percent
    drawdown_penalty = max(0, result.max_drawdown_pct - 15.0) * 0.05

    # Turnover penalty: penalize excessive churning (>500x annual)
    turnover_ratio = result.annual_turnover / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0
    turnover_penalty = max(0, turnover_ratio - 500) * 0.001

    score = result.sharpe * math.sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty
    return score

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare data for autotrader")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to download (default: all)")
    args = parser.parse_args()

    print(f"Cache directory: {CACHE_DIR}")
    print()

    print("Downloading data...")
    download_data(args.symbols)
    print()
    print("Done! Ready to backtest.")
