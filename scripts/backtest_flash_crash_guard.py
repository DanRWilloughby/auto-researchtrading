"""
Backtest the FlashCrashGuard triggers against real paper trading data.

Answers two questions:
  1. How many times would the 3% per-position guard have fired?
  2. How many times would the -2% portfolio guard have fired?

For each trigger: compute the P&L that WOULD have been realized at the
guard's exit point vs the P&L actually achieved at the strategy's natural
exit. Positive delta = guard saved money. Negative delta = guard killed
a recovering trade.

Data sources:
  - Trade log: 30m-concentrated paper_state.json (1048 trades, Mar 27 - Apr 11)
  - Historical bars: Hyperliquid API (30m BTC/ETH/SOL OHLCV)

Uses bar OHLC to approximate intra-bar moves. This OVERESTIMATES guard
triggers because a 5-second-poll guard wouldn't catch every wick — so
actual trigger rates will be somewhat lower than shown here.
"""
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests

HL_URL = "https://api.hyperliquid.xyz/info"
INTERVAL_MS = 30 * 60 * 1000

PAPER_STATE = Path(
    "/Users/danielwilloughby/Projects/Claude Code Folders/overnight-lab/"
    "projects/2026-03-22_autoresearch-trading-dashboard/dashboard/public/data/"
    "strategies/30m-concentrated/paper_state.json"
)

PER_POSITION_THRESHOLD_PCT = 3.0
PORTFOLIO_THRESHOLD_PCT = -2.0


@dataclass
class PositionCycle:
    """A full round-trip: open to close."""
    symbol: str
    side: str  # LONG or SHORT
    open_ts: int
    open_price: float
    open_size: float  # signed notional
    close_ts: int = 0
    close_price: float = 0.0
    realized_pnl: float = 0.0


def fetch_hl_candles(coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows = []
    cursor = start_ms
    chunk = 4900 * INTERVAL_MS
    while cursor < end_ms:
        w_end = min(cursor + chunk, end_ms)
        body = {"type": "candleSnapshot",
                "req": {"coin": coin, "interval": "30m",
                        "startTime": cursor, "endTime": w_end}}
        try:
            r = requests.post(HL_URL, json=body, timeout=15)
            r.raise_for_status()
            for c in r.json() or []:
                rows.append({
                    "timestamp": int(c["t"]),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                })
        except Exception as e:
            print(f"  HL fetch error for {coin}: {e}")
        cursor = w_end
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def reconstruct_cycles(trade_log: list) -> list[PositionCycle]:
    """
    Group trades into position cycles (open -> close).

    Handles flips (long -> short) by closing the current and opening a new one.
    Handles MODIFY by tracking net position per symbol.
    """
    cycles: list[PositionCycle] = []
    # Track open position per symbol: None if flat, else PositionCycle
    open_pos: dict[str, PositionCycle | None] = {"BTC": None, "ETH": None, "SOL": None}

    for t in trade_log:
        symbol = t.get("symbol", "")
        action = t.get("action", "")
        price = float(t.get("price", 0))
        size = float(t.get("size", 0))  # target notional after this action
        ts = int(t.get("ts", 0))
        pnl = float(t.get("pnl", 0))

        if symbol not in open_pos:
            continue

        if action == "OPEN_LONG":
            open_pos[symbol] = PositionCycle(
                symbol=symbol, side="LONG",
                open_ts=ts, open_price=price, open_size=size,
            )
        elif action == "OPEN_SHORT":
            open_pos[symbol] = PositionCycle(
                symbol=symbol, side="SHORT",
                open_ts=ts, open_price=price, open_size=size,
            )
        elif action == "CLOSE":
            pos = open_pos.get(symbol)
            if pos:
                pos.close_ts = ts
                pos.close_price = price
                pos.realized_pnl = pnl
                cycles.append(pos)
                open_pos[symbol] = None
        elif action == "MODIFY":
            # Skip MODIFYs for cycle-level analysis - just adjusts the open position
            pass

    return cycles


def analyze_per_position_trigger(cycles: list[PositionCycle], bars_by_symbol: dict[str, pd.DataFrame]):
    """For each cycle, check if 3% adverse move occurred at any bar during hold."""
    triggered_cycles = []
    saved_pnl = 0.0  # P&L difference: guard exit vs actual exit
    killed_profitable = 0
    killed_losses = 0

    for cycle in cycles:
        bars = bars_by_symbol.get(cycle.symbol)
        if bars is None or len(bars) == 0:
            continue

        # Bars during the hold period (inclusive start, exclusive end)
        mask = (bars["timestamp"] >= cycle.open_ts) & (bars["timestamp"] <= cycle.close_ts)
        hold_bars = bars[mask]
        if len(hold_bars) == 0:
            continue

        # For LONG: adverse = price dropping below entry
        # For SHORT: adverse = price rising above entry
        if cycle.side == "LONG":
            worst = hold_bars["low"].min()
            adverse_pct = (cycle.open_price - worst) / cycle.open_price * 100
            adverse_price = cycle.open_price * (1 - PER_POSITION_THRESHOLD_PCT / 100)
            # Find the first bar where low dipped below adverse_price
            trigger_bars = hold_bars[hold_bars["low"] <= adverse_price]
        else:  # SHORT
            worst = hold_bars["high"].max()
            adverse_pct = (worst - cycle.open_price) / cycle.open_price * 100
            adverse_price = cycle.open_price * (1 + PER_POSITION_THRESHOLD_PCT / 100)
            trigger_bars = hold_bars[hold_bars["high"] >= adverse_price]

        if adverse_pct >= PER_POSITION_THRESHOLD_PCT:
            # Guard would have fired
            trigger_bar = trigger_bars.iloc[0]
            exit_price_guard = adverse_price  # guard exits at threshold price
            # P&L at guard exit (rough - uses triggering price)
            notional = abs(cycle.open_size)
            contracts_notional = notional  # since open_size is in USD notional
            if cycle.side == "LONG":
                pnl_at_guard = (exit_price_guard - cycle.open_price) / cycle.open_price * contracts_notional
            else:
                pnl_at_guard = (cycle.open_price - exit_price_guard) / cycle.open_price * contracts_notional

            delta = pnl_at_guard - cycle.realized_pnl
            # If actual P&L was positive and guard killed it, that's a loss (delta < 0)
            # If actual P&L was more negative than guard exit, guard saved money (delta > 0)
            saved_pnl += delta
            if cycle.realized_pnl > 0:
                killed_profitable += 1
            else:
                killed_losses += 1

            triggered_cycles.append({
                "symbol": cycle.symbol,
                "side": cycle.side,
                "open_ts": cycle.open_ts,
                "open_price": cycle.open_price,
                "actual_exit": cycle.close_price,
                "guard_exit": exit_price_guard,
                "actual_pnl": cycle.realized_pnl,
                "guard_pnl": pnl_at_guard,
                "delta": delta,
                "adverse_pct": adverse_pct,
            })

    return triggered_cycles, saved_pnl, killed_profitable, killed_losses


def analyze_portfolio_trigger(equity_curve: list, starting_equity: float = 100000):
    """
    Check for portfolio-level -2% unrealized trigger.

    Using drawdown from local peak as a proxy for unrealized PnL:
    if the equity curve drops >2% from a recent peak, the guard might fire.
    """
    if not equity_curve:
        return []

    triggered = []
    peak = starting_equity
    last_trigger_idx = -100  # cooldown between triggers

    for i, point in enumerate(equity_curve):
        equity = point.get("equity", 0)
        ts = point.get("ts", 0)
        if equity > peak:
            peak = equity
        dd_pct = (equity - peak) / peak * 100 if peak > 0 else 0

        # Cooldown: don't count repeated triggers within 30 bars (15 hours)
        if dd_pct <= PORTFOLIO_THRESHOLD_PCT and i - last_trigger_idx > 30:
            triggered.append({
                "ts": ts,
                "equity": equity,
                "peak": peak,
                "dd_pct": dd_pct,
            })
            last_trigger_idx = i

    return triggered


def main():
    print("=" * 80)
    print("  FLASH CRASH GUARD BACKTEST")
    print("=" * 80)

    # Load paper state
    print(f"\n[1] Loading paper state from: {PAPER_STATE.name}")
    with open(PAPER_STATE) as f:
        state = json.load(f)

    trade_log = state.get("trade_log", [])
    equity_curve = state.get("equity_curve", [])
    started_at = state.get("started_at", "unknown")
    print(f"    Trades: {len(trade_log)}")
    print(f"    Equity curve points: {len(equity_curve)}")
    print(f"    Started: {started_at}")

    # Reconstruct position cycles
    print(f"\n[2] Reconstructing position cycles from trade log...")
    cycles = reconstruct_cycles(trade_log)
    print(f"    Total position cycles: {len(cycles)}")
    by_symbol = defaultdict(int)
    for c in cycles:
        by_symbol[f"{c.symbol} {c.side}"] += 1
    for k, v in sorted(by_symbol.items()):
        print(f"      {k}: {v}")

    # Fetch historical bars
    print(f"\n[3] Fetching 30m OHLCV bars from Hyperliquid...")
    ts_min = min((c.open_ts for c in cycles), default=0)
    ts_max = max((c.close_ts for c in cycles), default=int(time.time() * 1000))
    # Pad by 1 day
    ts_min -= 24 * 3600 * 1000
    ts_max += 24 * 3600 * 1000

    bars_by_symbol = {}
    for sym in ("BTC", "ETH", "SOL"):
        print(f"    Fetching {sym}...")
        bars = fetch_hl_candles(sym, ts_min, ts_max)
        bars_by_symbol[sym] = bars
        print(f"      {len(bars)} bars")

    # Per-position trigger analysis
    print(f"\n[4] Per-position trigger analysis ({PER_POSITION_THRESHOLD_PCT}% threshold)...")
    triggered, saved_pnl, killed_prof, killed_loss = analyze_per_position_trigger(cycles, bars_by_symbol)

    total_realized = sum(c.realized_pnl for c in cycles)
    total_realized_closed = sum(c.realized_pnl for c in cycles if c.close_ts > 0)

    print(f"\n  Total cycles analyzed    : {len(cycles)}")
    print(f"  Triggered cycles         : {len(triggered)} ({len(triggered)/max(len(cycles),1)*100:.1f}%)")
    print(f"  Would-have-killed profitable: {killed_prof}")
    print(f"  Would-have-killed losses    : {killed_loss}")
    print(f"  Strategy's actual P&L    : ${total_realized_closed:+,.2f}")
    print(f"  Delta from guard firing  : ${saved_pnl:+,.2f}")
    print(f"  Strategy P&L WITH guard  : ${total_realized_closed + saved_pnl:+,.2f}")

    if len(triggered) > 0:
        pct_impact = saved_pnl / abs(total_realized_closed) * 100 if total_realized_closed else 0
        print(f"  Impact on strategy P&L   : {pct_impact:+.1f}%")

        print(f"\n  Worst 5 triggered cycles (by adverse %):")
        worst = sorted(triggered, key=lambda x: -x["adverse_pct"])[:5]
        for t in worst:
            print(f"    {t['symbol']} {t['side']}: "
                  f"entry=${t['open_price']:,.2f} -> worst_adverse={t['adverse_pct']:.2f}% "
                  f"| actual_pnl=${t['actual_pnl']:+,.2f} | guard_pnl=${t['guard_pnl']:+,.2f} "
                  f"| delta=${t['delta']:+,.2f}")

        print(f"\n  Distribution by symbol:")
        by_sym = defaultdict(lambda: {"count": 0, "delta": 0.0, "killed_profit": 0, "killed_loss": 0})
        for t in triggered:
            by_sym[t["symbol"]]["count"] += 1
            by_sym[t["symbol"]]["delta"] += t["delta"]
            if t["actual_pnl"] > 0:
                by_sym[t["symbol"]]["killed_profit"] += 1
            else:
                by_sym[t["symbol"]]["killed_loss"] += 1
        for sym, stats in sorted(by_sym.items()):
            print(f"    {sym}: {stats['count']} triggers, "
                  f"{stats['killed_profit']} killed profits, "
                  f"{stats['killed_loss']} avoided losses, "
                  f"delta=${stats['delta']:+,.2f}")

    # Portfolio trigger analysis
    print(f"\n[5] Portfolio trigger analysis ({PORTFOLIO_THRESHOLD_PCT}% threshold)...")
    portfolio_triggers = analyze_portfolio_trigger(equity_curve, starting_equity=100000)
    print(f"\n  Portfolio-level triggers: {len(portfolio_triggers)}")
    if portfolio_triggers:
        print(f"  First 5:")
        for t in portfolio_triggers[:5]:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(t["ts"]/1000, tz=timezone.utc)
            print(f"    {dt.strftime('%Y-%m-%d %H:%M')} | equity=${t['equity']:,.0f} "
                  f"| peak=${t['peak']:,.0f} | DD={t['dd_pct']:.2f}%")

    # Summary verdict
    print("\n" + "=" * 80)
    print("  VERDICT")
    print("=" * 80)

    trigger_rate = len(triggered) / max(len(cycles), 1) * 100
    if trigger_rate < 5:
        print(f"  ✅ Per-position guard fires on {trigger_rate:.1f}% of trades — LOW impact")
    elif trigger_rate < 20:
        print(f"  ⚠️  Per-position guard fires on {trigger_rate:.1f}% of trades — MODERATE impact")
    else:
        print(f"  ❌ Per-position guard fires on {trigger_rate:.1f}% of trades — HIGH impact, too tight")

    if saved_pnl > 0:
        print(f"  ✅ Guard would have SAVED ${saved_pnl:+,.2f} total")
    else:
        print(f"  ⚠️  Guard would have COST ${-saved_pnl:+,.2f} in killed profitable trades")

    if len(portfolio_triggers) == 0:
        print(f"  ✅ Portfolio guard never fires — safe at current threshold")
    elif len(portfolio_triggers) < 3:
        print(f"  ✅ Portfolio guard fires {len(portfolio_triggers)} times — LOW frequency")
    else:
        print(f"  ⚠️  Portfolio guard fires {len(portfolio_triggers)} times — consider widening")


if __name__ == "__main__":
    main()
