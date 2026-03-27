"""
Paper trading bot — runs the strategy against live Hyperliquid data.

Pulls candles every interval, runs strategy.on_bar(), logs simulated trades.
Persists state to JSON so it survives restarts.

Usage:
    uv run paper/trader.py                                          # default: 1h, BTC/ETH/SOL
    uv run paper/trader.py --interval 15m --symbols BTC ETH         # 15m on BTC/ETH
    uv run paper/trader.py --once                                   # run once and exit (for cron)
    uv run paper/trader.py --strategy strategies/1h-btc-eth-sol/strategy.py
"""

import os
import sys
import time
import json
import signal
import argparse
import importlib.util
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
import requests

# Add project root + engine to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "engine"))

from engine.prepare import (
    BarData, Signal, PortfolioState, INTERVAL_CONFIG,
    INITIAL_CAPITAL, MAKER_FEE, TAKER_FEE, SLIPPAGE_BPS, MAX_LEVERAGE,
    LOOKBACK_BARS, DEFAULT_SYMBOLS, ALL_SYMBOLS, VALID_INTERVALS,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper-trader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Shadow execution scenarios: {name: (slippage_bps, fee_rate)}
# Tracks how equity would differ under different execution assumptions
SHADOW_SCENARIOS = {
    "ideal":       (0.0, MAKER_FEE),         # 0 bps slip, maker fee (2 bps)
    "paper":       (SLIPPAGE_BPS, TAKER_FEE), # 1 bps slip, taker fee (5 bps) — current default
    "realistic":   (3.0, TAKER_FEE),          # 3 bps slip, taker fee
    "pessimistic": (5.0, 0.0008),             # 5 bps slip, 8 bps fee (worst case)
}

# ---------------------------------------------------------------------------
# Live data fetching
# ---------------------------------------------------------------------------

def fetch_recent_candles(symbol: str, interval: str, count: int = 600) -> pd.DataFrame:
    """Fetch recent candles from Hyperliquid."""
    interval_ms = INTERVAL_CONFIG[interval]["minutes"] * 60 * 1000
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - count * interval_ms

    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        }
    }

    resp = requests.post(HL_INFO_URL, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list) or not data:
        return pd.DataFrame()

    rows = []
    for row in data:
        rows.append({
            "timestamp": int(row["t"]),
            "open": float(row["o"]),
            "high": float(row["h"]),
            "low": float(row["l"]),
            "close": float(row["c"]),
            "volume": float(row["v"]),
        })

    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_current_funding(symbol: str) -> float:
    """Fetch current funding rate from Hyperliquid."""
    body = {"type": "metaAndAssetCtxs"}
    try:
        resp = requests.post(HL_INFO_URL, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        meta = data[0]["universe"]
        ctxs = data[1]
        for m, c in zip(meta, ctxs):
            if m["name"] == symbol:
                return float(c.get("funding", 0.0))
    except Exception as e:
        logger.warning(f"Failed to fetch funding for {symbol}: {e}")
    return 0.0

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def derive_strategy_dir(strategy_path: str) -> str:
    """Derive the strategy directory from the strategy file path."""
    return os.path.dirname(os.path.abspath(strategy_path))


def state_path(strategy_dir: str) -> str:
    return os.path.join(strategy_dir, "paper_state.json")


def load_state(strategy_dir: str, interval: str) -> dict:
    """Load persisted paper trading state from the strategy directory."""
    path = state_path(strategy_dir)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "interval": interval,
        "cash": INITIAL_CAPITAL,
        "positions": {},
        "entry_prices": {},
        "equity_curve": [{"ts": int(time.time() * 1000), "equity": INITIAL_CAPITAL}],
        "trade_log": [],
        "last_bar_ts": {},
        "history_buffers": {},
        "peak_equity": INITIAL_CAPITAL,
        "strategy_state": {},
        "shadow_cost_deltas": {name: 0.0 for name in SHADOW_SCENARIOS},
        "shadow_curves": {name: [{"ts": int(time.time() * 1000), "equity": INITIAL_CAPITAL}] for name in SHADOW_SCENARIOS},
    }


def save_state(state: dict, strategy_dir: str):
    """Persist paper trading state to the strategy directory."""
    os.makedirs(strategy_dir, exist_ok=True)
    path = state_path(strategy_dir)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def log_trade(trade: dict, strategy_dir: str):
    """Append trade to daily log file in the strategy directory."""
    logs_dir = os.path.join(strategy_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = os.path.join(logs_dir, f"trades_{date_str}.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(trade) + "\n")

# ---------------------------------------------------------------------------
# Strategy loading
# ---------------------------------------------------------------------------

def load_strategy(path: str):
    """Dynamically load a Strategy class from a file path."""
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()

# ---------------------------------------------------------------------------
# Core paper trading loop
# ---------------------------------------------------------------------------

def run_one_tick(strategy, state: dict, symbols: list, interval: str, strategy_dir: str = "") -> dict:
    """Execute one paper trading tick: fetch data, run strategy, simulate trades."""
    interval_min = INTERVAL_CONFIG[interval]["minutes"]
    funding_divisor = 8.0 * (60.0 / interval_min)
    now_ms = int(time.time() * 1000)

    # Build portfolio state
    portfolio = PortfolioState(
        cash=state["cash"],
        positions={k: v for k, v in state["positions"].items()},
        entry_prices={k: v for k, v in state["entry_prices"].items()},
        equity=state["cash"],
        timestamp=now_ms,
    )

    # Fetch candles and build bar_data
    bar_data = {}
    for symbol in symbols:
        try:
            candles = fetch_recent_candles(symbol, interval, count=LOOKBACK_BARS + 10)
        except Exception as e:
            logger.error(f"Failed to fetch {symbol} candles: {e}")
            continue

        if candles.empty:
            continue

        # Check if we've already processed this bar
        latest_ts = int(candles["timestamp"].iloc[-1])
        last_seen = state["last_bar_ts"].get(symbol, 0)
        if latest_ts <= last_seen:
            continue

        # Add funding rate
        funding = fetch_current_funding(symbol)
        candles["funding_rate"] = funding

        latest = candles.iloc[-1]
        bar_data[symbol] = BarData(
            symbol=symbol,
            timestamp=latest_ts,
            open=float(latest["open"]),
            high=float(latest["high"]),
            low=float(latest["low"]),
            close=float(latest["close"]),
            volume=float(latest["volume"]),
            funding_rate=funding,
            history=candles,
        )

        state["last_bar_ts"][symbol] = latest_ts

    if not bar_data:
        return state

    # Mark-to-market
    unrealized_pnl = 0.0
    for sym, pos_notional in portfolio.positions.items():
        if sym in bar_data:
            current_price = bar_data[sym].close
            entry_price = portfolio.entry_prices.get(sym, current_price)
            if entry_price > 0:
                price_change = (current_price - entry_price) / entry_price
                unrealized_pnl += pos_notional * price_change

    portfolio.equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl

    # Apply funding
    for sym, pos_notional in list(portfolio.positions.items()):
        if sym in bar_data:
            fr = bar_data[sym].funding_rate
            funding_payment = pos_notional * fr / funding_divisor
            portfolio.cash -= funding_payment

    # Run strategy
    try:
        signals = strategy.on_bar(bar_data, portfolio)
    except Exception as e:
        logger.error(f"strategy.on_bar() raised {type(e).__name__}: {e}")
        signals = []

    # Execute simulated trades
    for sig in (signals or []):
        if sig.symbol not in bar_data:
            continue

        current_price = bar_data[sig.symbol].close
        current_pos = portfolio.positions.get(sig.symbol, 0.0)
        delta = sig.target_position - current_pos

        if abs(delta) < 1.0:
            continue

        # Leverage check
        new_positions = dict(portfolio.positions)
        new_positions[sig.symbol] = sig.target_position
        total_exposure = sum(abs(v) for v in new_positions.values())
        if total_exposure > portfolio.equity * MAX_LEVERAGE:
            logger.warning(f"Leverage limit reached, skipping {sig.symbol} trade")
            continue

        # Simulate execution with slippage + fees
        slippage = current_price * SLIPPAGE_BPS / 10000
        exec_price = current_price + slippage if delta > 0 else current_price - slippage
        fee = abs(delta) * TAKER_FEE
        portfolio.cash -= fee

        pnl = 0.0

        if sig.target_position == 0:
            # Close position
            if sig.symbol in portfolio.entry_prices:
                entry = portfolio.entry_prices[sig.symbol]
                if entry > 0:
                    pnl = current_pos * (exec_price - entry) / entry
                    portfolio.cash += abs(current_pos) + pnl
                del portfolio.entry_prices[sig.symbol]
            portfolio.positions.pop(sig.symbol, None)
            action = "CLOSE"
        elif current_pos == 0:
            # Open position
            portfolio.cash -= abs(sig.target_position)
            portfolio.positions[sig.symbol] = sig.target_position
            portfolio.entry_prices[sig.symbol] = exec_price
            action = "OPEN_LONG" if sig.target_position > 0 else "OPEN_SHORT"
        else:
            # Modify
            old_notional = abs(current_pos)
            old_entry = portfolio.entry_prices.get(sig.symbol, exec_price)
            if abs(sig.target_position) < abs(current_pos):
                reduced = abs(current_pos) - abs(sig.target_position)
                if old_entry > 0:
                    direction = 1.0 if current_pos > 0 else -1.0
                    pnl = direction * reduced * (exec_price - old_entry) / old_entry
                portfolio.cash += reduced + pnl
            elif abs(sig.target_position) > abs(current_pos):
                added = abs(sig.target_position) - abs(current_pos)
                portfolio.cash -= added
                if old_notional + added > 0:
                    new_entry = (old_entry * old_notional + exec_price * added) / (old_notional + added)
                    portfolio.entry_prices[sig.symbol] = new_entry
            portfolio.positions[sig.symbol] = sig.target_position
            action = "MODIFY"

        # Shadow execution: track cost deltas for each scenario
        if "shadow_cost_deltas" not in state:
            state["shadow_cost_deltas"] = {name: 0.0 for name in SHADOW_SCENARIOS}
        for scenario_name, (scen_slip_bps, scen_fee_rate) in SHADOW_SCENARIOS.items():
            scen_slip = current_price * scen_slip_bps / 10000
            scen_fee = abs(delta) * scen_fee_rate
            # Base cost was: slippage effect on PnL + fee
            base_slip_cost = abs(delta) * (SLIPPAGE_BPS / 10000)
            base_fee_cost = fee
            scen_slip_cost = abs(delta) * (scen_slip_bps / 10000)
            # Delta = how much MORE this scenario costs vs the base paper execution
            cost_delta = (scen_slip_cost + scen_fee) - (base_slip_cost + base_fee_cost)
            state["shadow_cost_deltas"][scenario_name] += cost_delta

        trade = {
            "ts": now_ms,
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": sig.symbol,
            "action": action,
            "price": round(exec_price, 4),
            "size": round(delta, 2),
            "target_pos": round(sig.target_position, 2),
            "pnl": round(pnl, 2),
            "fee": round(fee, 2),
        }
        state["trade_log"].append(trade)
        log_trade(trade, strategy_dir)

        side = "LONG" if sig.target_position > 0 else "SHORT" if sig.target_position < 0 else "FLAT"
        logger.info(f"TRADE: {action} {sig.symbol} @ ${exec_price:,.2f} | {side} ${abs(sig.target_position):,.0f} | PnL: ${pnl:,.2f}")

    # Update state
    # Recalculate equity
    unrealized_pnl = 0.0
    for sym, pos_notional in portfolio.positions.items():
        if sym in bar_data:
            current_price = bar_data[sym].close
            entry_price = portfolio.entry_prices.get(sym, current_price)
            if entry_price > 0:
                price_change = (current_price - entry_price) / entry_price
                unrealized_pnl += pos_notional * price_change

    equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl

    state["cash"] = portfolio.cash
    state["positions"] = portfolio.positions
    state["entry_prices"] = portfolio.entry_prices
    state["peak_equity"] = max(state.get("peak_equity", INITIAL_CAPITAL), equity)

    state["equity_curve"].append({
        "ts": now_ms,
        "equity": round(equity, 2),
    })

    # Update shadow equity curves
    if "shadow_curves" not in state:
        state["shadow_curves"] = {name: [] for name in SHADOW_SCENARIOS}
    if "shadow_cost_deltas" not in state:
        state["shadow_cost_deltas"] = {name: 0.0 for name in SHADOW_SCENARIOS}
    for scenario_name in SHADOW_SCENARIOS:
        shadow_eq = round(equity - state["shadow_cost_deltas"].get(scenario_name, 0.0), 2)
        if scenario_name not in state["shadow_curves"]:
            state["shadow_curves"][scenario_name] = []
        state["shadow_curves"][scenario_name].append({"ts": now_ms, "equity": shadow_eq})

    # Print status
    dd = (state["peak_equity"] - equity) / state["peak_equity"] * 100 if state["peak_equity"] > 0 else 0
    ret = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    pos_str = ", ".join(f"{s}: ${v:,.0f}" for s, v in portfolio.positions.items()) if portfolio.positions else "flat"

    logger.info(f"EQUITY: ${equity:,.2f} ({ret:+.2f}%) | DD: {dd:.2f}% | Positions: {pos_str}")

    # Shadow summary
    shadow_parts = []
    for name in ["ideal", "realistic", "pessimistic"]:
        delta = state["shadow_cost_deltas"].get(name, 0.0)
        shadow_parts.append(f"{name}: {'+'if delta<=0 else ''}{-delta:,.0f}")
    logger.info(f"Shadow cost deltas: {' | '.join(shadow_parts)}")
    logger.info(f"Trades this session: {len(state['trade_log'])} | Bars processed: {len(bar_data)}")

    return state


def run_loop(strategy, symbols: list, interval: str, strategy_dir: str, once: bool = False):
    """Main paper trading loop."""
    state = load_state(strategy_dir, interval)
    interval_sec = INTERVAL_CONFIG[interval]["minutes"] * 60

    logger.info(f"Paper trader starting: {interval} on {symbols}")
    logger.info(f"State: {state_path(strategy_dir)}")
    logger.info(f"Capital: ${state['cash']:,.2f} | Trades so far: {len(state['trade_log'])}")

    running = True
    def shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received, saving state...")
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while running:
        try:
            state = run_one_tick(strategy, state, symbols, interval, strategy_dir)
            save_state(state, strategy_dir)
        except Exception as e:
            logger.error(f"Tick failed: {type(e).__name__}: {e}")

        if once:
            break

        # Sleep until next bar + small offset to ensure candle is closed
        next_bar_sec = interval_sec - (time.time() % interval_sec) + 5
        logger.info(f"Next tick in {next_bar_sec:.0f}s")
        time.sleep(next_bar_sec)

    save_state(state, strategy_dir)
    logger.info("Paper trader stopped. State saved.")

# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def show_status(strategy_dir: str, interval: str):
    """Print current paper trading status."""
    state = load_state(strategy_dir, interval)
    equity_points = state.get("equity_curve", [])
    trades = state.get("trade_log", [])

    if not equity_points:
        print("No paper trading data yet.")
        return

    strategy_name = os.path.basename(strategy_dir)
    latest_equity = equity_points[-1]["equity"]
    started = state.get("started_at", "unknown")
    peak = state.get("peak_equity", INITIAL_CAPITAL)
    dd = (peak - latest_equity) / peak * 100 if peak > 0 else 0
    ret = (latest_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    print(f"=== Paper Trading Status: {strategy_name} ({interval}) ===")
    print(f"State file:   {state_path(strategy_dir)}")
    print(f"Started:      {started}")
    print(f"Equity:       ${latest_equity:,.2f} ({ret:+.2f}%)")
    print(f"Peak:         ${peak:,.2f}")
    print(f"Drawdown:     {dd:.2f}%")
    print(f"Total trades: {len(trades)}")

    positions = state.get("positions", {})
    if positions:
        print(f"Open positions:")
        for sym, val in positions.items():
            side = "LONG" if val > 0 else "SHORT"
            entry = state["entry_prices"].get(sym, 0)
            print(f"  {sym}: {side} ${abs(val):,.0f} @ ${entry:,.2f}")
    else:
        print("Positions:    flat")

    if trades:
        recent = trades[-5:]
        print(f"\nLast {len(recent)} trades:")
        for t in recent:
            print(f"  {t.get('time', '?')} | {t['action']:>6s} {t['symbol']} @ ${t['price']:,.2f} | PnL: ${t['pnl']:,.2f}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper trading bot")
    parser.add_argument("--strategy", default=None,
                        help="Path to strategy.py (state stored in same directory)")
    parser.add_argument("--interval", default="1h",
                        help=f"Bar interval (default: 1h)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=f"Symbols to trade (default: {DEFAULT_SYMBOLS})")
    parser.add_argument("--all-symbols", action="store_true",
                        help=f"Trade all symbols: {ALL_SYMBOLS}")
    parser.add_argument("--once", action="store_true",
                        help="Run one tick and exit (for cron usage)")
    parser.add_argument("--status", action="store_true",
                        help="Show current paper trading status and exit")
    parser.add_argument("--reset", action="store_true",
                        help="Reset paper trading state (start fresh)")
    args = parser.parse_args()

    symbols = ALL_SYMBOLS if args.all_symbols else (args.symbols or DEFAULT_SYMBOLS)

    # Resolve strategy path — all state is scoped to the strategy directory
    if args.strategy:
        strategy_path = os.path.join(PROJECT_ROOT, args.strategy) if not os.path.isabs(args.strategy) else args.strategy
    else:
        strategy_path = os.path.join(PROJECT_ROOT, "strategies", "1h-btc-eth-sol", "strategy.py")

    strategy_dir = derive_strategy_dir(strategy_path)

    if args.status:
        show_status(strategy_dir, args.interval)
        sys.exit(0)

    if args.reset:
        path = state_path(strategy_dir)
        if os.path.exists(path):
            os.remove(path)
            print(f"Reset paper trading state: {path}")
        else:
            print("No state to reset.")
        sys.exit(0)

    strategy = load_strategy(strategy_path)
    run_loop(strategy, symbols, args.interval, strategy_dir, once=args.once)
