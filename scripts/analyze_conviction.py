"""
Analyze trade conviction (vote count) vs return from backtest trade log.

Pairs OPEN trades with their CLOSE, groups by entry conviction level,
and computes per-group stats. Also models hybrid maker/taker routing scenarios.

Usage:
    uv run scripts/analyze_conviction.py
"""

import sys
import os

_engine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine")
sys.path.insert(0, _engine_dir)

import importlib.util
from prepare import load_data, download_data, run_backtest, INTERVAL_CONFIG

# ---------------------------------------------------------------------------
# 1. Run the backtest and capture trade_log
# ---------------------------------------------------------------------------

strategy_path = os.path.join(
    os.path.dirname(__file__), "..", "strategies", "30m-concentrated", "strategy.py"
)
spec = importlib.util.spec_from_file_location("strategy_module", strategy_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
strategy = mod.Strategy()

download_data(symbols=["BTC", "ETH", "SOL"], interval="30m", source="coinbase")
data = load_data("test", symbols=["BTC", "ETH", "SOL"], interval="30m", source="coinbase")

total_bars = sum(len(df) for df in data.values())
print(f"Loaded {total_bars} bars across {list(data.keys())}")

result = run_backtest(strategy, data, interval="30m", taker_fee=0.0003)

trade_log = result.trade_log
print(f"Total trade records: {len(trade_log)}")
print(f"Record format: (action, symbol, delta, exec_price, pnl, timestamp, fee, metadata)")
print()

# Show a sample entry trade
for t in trade_log[:20]:
    if t[0] == "open" and t[7] is not None:
        print(f"Sample entry: action={t[0]}, sym={t[1]}, delta=${t[2]:,.0f}, "
              f"price=${t[3]:,.2f}, meta={t[7]}")
        break

# ---------------------------------------------------------------------------
# 2. Pair OPEN trades with their CLOSE trades
# ---------------------------------------------------------------------------

# Track open positions per symbol: {symbol: (open_trade_record, ...)}
open_positions = {}
paired_trades = []  # list of (open_record, close_record)

for t in trade_log:
    action, symbol, delta, exec_price, pnl, ts, fee, meta = t

    if action == "open":
        open_positions[symbol] = t
    elif action == "close":
        if symbol in open_positions:
            paired_trades.append((open_positions[symbol], t))
            del open_positions[symbol]
    elif action == "modify":
        # Flip: this is simultaneously a close and a new open
        # The pnl on the modify represents the close PnL
        if symbol in open_positions:
            paired_trades.append((open_positions[symbol], t))
        # The modify also opens a new position in the opposite direction
        open_positions[symbol] = t

print(f"\nPaired trades (open→close): {len(paired_trades)}")

# ---------------------------------------------------------------------------
# 3. Extract conviction and compute per-trade stats
# ---------------------------------------------------------------------------

interval_ms = INTERVAL_CONFIG["30m"]["minutes"] * 60 * 1000

trade_rows = []
for open_t, close_t in paired_trades:
    o_action, o_sym, o_delta, o_price, o_pnl, o_ts, o_fee, o_meta = open_t
    c_action, c_sym, c_delta, c_price, c_pnl, c_ts, c_fee, c_meta = close_t

    # Direction: positive delta = long, negative = short
    is_long = o_delta > 0
    notional = abs(o_delta)

    # Conviction = relevant vote count from entry metadata
    if o_meta is None:
        # Modify-originated entries may carry metadata from the flip
        conviction = None
    else:
        conviction = o_meta.get("bull_votes") if is_long else o_meta.get("bear_votes")

    # Holding time in bars
    hold_bars = (c_ts - o_ts) / interval_ms if interval_ms > 0 else 0

    # Fees: entry fee + exit fee
    total_fee = o_fee + c_fee

    # Gross PnL is on the close record
    gross_pnl = c_pnl

    # Net PnL
    net_pnl = gross_pnl - total_fee

    trade_rows.append({
        "symbol": o_sym,
        "is_long": is_long,
        "conviction": conviction,
        "notional": notional,
        "gross_pnl": gross_pnl,
        "total_fee": total_fee,
        "net_pnl": net_pnl,
        "hold_bars": hold_bars,
        "entry_price": o_price,
        "exit_price": c_price,
    })

# Filter to trades with known conviction
with_conviction = [t for t in trade_rows if t["conviction"] is not None]
without_conviction = [t for t in trade_rows if t["conviction"] is None]
print(f"Trades with conviction data: {len(with_conviction)}")
print(f"Trades without (modify-originated): {len(without_conviction)}")

# ---------------------------------------------------------------------------
# 4. Group by conviction and compute stats
# ---------------------------------------------------------------------------

from collections import defaultdict

groups = defaultdict(list)
for t in with_conviction:
    groups[int(t["conviction"])].append(t)

print("\n" + "=" * 90)
print("CONVICTION vs TRADE RETURN ANALYSIS")
print("=" * 90)
print(f"{'Votes':>5} | {'Trades':>7} | {'Win Rate':>9} | {'Avg Gross':>11} | "
      f"{'Avg Fee':>9} | {'Avg Net':>11} | {'Total Net':>14} | {'Avg Hold':>10}")
print("-" * 90)

all_stats = {}
for votes in sorted(groups.keys()):
    trades = groups[votes]
    n = len(trades)
    wins = sum(1 for t in trades if t["gross_pnl"] > 0)
    win_rate = wins / n * 100 if n > 0 else 0

    avg_gross = sum(t["gross_pnl"] for t in trades) / n
    avg_fee = sum(t["total_fee"] for t in trades) / n
    avg_net = sum(t["net_pnl"] for t in trades) / n
    total_net = sum(t["net_pnl"] for t in trades)
    avg_hold = sum(t["hold_bars"] for t in trades) / n

    all_stats[votes] = {
        "n": n, "win_rate": win_rate, "avg_gross": avg_gross,
        "avg_fee": avg_fee, "avg_net": avg_net, "total_net": total_net,
        "avg_hold": avg_hold, "trades": trades,
    }

    print(f"{votes:>3}/5 | {n:>7,} | {win_rate:>8.1f}% | ${avg_gross:>10,.2f} | "
          f"${avg_fee:>8,.2f} | ${avg_net:>10,.2f} | ${total_net:>13,.0f} | "
          f"{avg_hold:>8.1f} bars")

# Totals row
all_trades = with_conviction
total_n = len(all_trades)
total_wins = sum(1 for t in all_trades if t["gross_pnl"] > 0)
total_win_rate = total_wins / total_n * 100 if total_n > 0 else 0
total_avg_gross = sum(t["gross_pnl"] for t in all_trades) / total_n
total_avg_fee = sum(t["total_fee"] for t in all_trades) / total_n
total_avg_net = sum(t["net_pnl"] for t in all_trades) / total_n
total_total_net = sum(t["net_pnl"] for t in all_trades)
total_avg_hold = sum(t["hold_bars"] for t in all_trades) / total_n
print("-" * 90)
print(f"{'ALL':>5} | {total_n:>7,} | {total_win_rate:>8.1f}% | ${total_avg_gross:>10,.2f} | "
      f"${total_avg_fee:>8,.2f} | ${total_avg_net:>10,.2f} | ${total_total_net:>13,.0f} | "
      f"{total_avg_hold:>8.1f} bars")

# ---------------------------------------------------------------------------
# 5. Breakdown by direction (long vs short) within each conviction level
# ---------------------------------------------------------------------------

print("\n" + "=" * 90)
print("BREAKDOWN BY DIRECTION")
print("=" * 90)
print(f"{'Votes':>5} {'Dir':>5} | {'Trades':>7} | {'Win Rate':>9} | "
      f"{'Avg Net':>11} | {'Total Net':>14}")
print("-" * 90)

for votes in sorted(groups.keys()):
    for is_long, label in [(True, "LONG"), (False, "SHORT")]:
        subset = [t for t in groups[votes] if t["is_long"] == is_long]
        if not subset:
            continue
        n = len(subset)
        wins = sum(1 for t in subset if t["gross_pnl"] > 0)
        wr = wins / n * 100
        avg_net = sum(t["net_pnl"] for t in subset) / n
        total_net = sum(t["net_pnl"] for t in subset)
        print(f"{votes:>3}/5 {label:>5} | {n:>7,} | {wr:>8.1f}% | "
              f"${avg_net:>10,.2f} | ${total_net:>13,.0f}")

# ---------------------------------------------------------------------------
# 6. Hybrid maker/taker routing scenarios
# ---------------------------------------------------------------------------

TAKER_FEE_BPS = 3.0  # bps
MAKER_FEE_BPS = 0.0  # bps (maker rebate on CB is 0 for now)
MAKER_FILL_RATE = 0.68  # 68% fill rate from shadow data
# Regulatory fee not modeled (applies to both maker and taker equally)

print("\n" + "=" * 90)
print("HYBRID MAKER/TAKER ROUTING ANALYSIS")
print("=" * 90)

# For each scenario, compute what happens to each trade
scenarios = {
    "A: All Taker (current)": lambda votes: ("taker", 1.0),
    "B: Hybrid (3/5=maker, 4-5/5=taker)": lambda votes: ("maker", MAKER_FILL_RATE) if votes == 3 else ("taker", 1.0),
    "C: All Maker": lambda votes: ("maker", MAKER_FILL_RATE),
}

for scenario_name, route_fn in scenarios.items():
    total_pnl = 0.0
    total_fees = 0.0
    filled_trades = 0
    missed_trades = 0
    missed_pnl = 0.0

    for t in with_conviction:
        votes = int(t["conviction"])
        order_type, fill_rate = route_fn(votes)

        notional = t["notional"]
        if order_type == "taker":
            fee_per_side = notional * TAKER_FEE_BPS / 10000
        else:
            fee_per_side = notional * MAKER_FEE_BPS / 10000

        # Round-trip fee (entry + exit, assuming same order type both sides)
        rt_fee = fee_per_side * 2

        if fill_rate >= 1.0:
            # Always fills
            total_pnl += t["gross_pnl"] - rt_fee
            total_fees += rt_fee
            filled_trades += 1
        else:
            # Probabilistic fill — use expected value
            # filled portion gets the PnL minus reduced fees
            # missed portion gets nothing
            total_pnl += fill_rate * (t["gross_pnl"] - rt_fee)
            total_fees += fill_rate * rt_fee
            filled_trades += fill_rate
            missed_trades += (1 - fill_rate)
            missed_pnl += (1 - fill_rate) * t["gross_pnl"]

    print(f"\n{scenario_name}")
    print(f"  Filled trades (expected): {filled_trades:,.0f}")
    print(f"  Missed trades (expected): {missed_trades:,.0f}")
    print(f"  Total fees:              ${total_fees:>14,.0f}")
    print(f"  Total net PnL:           ${total_pnl:>14,.0f}")
    print(f"  Missed gross PnL:        ${missed_pnl:>14,.0f}")
    print(f"  Fee savings vs A:        ${total_total_net - total_pnl:>14,.0f}" if "A:" not in scenario_name else "")

# Compute fee savings explicitly
print("\n" + "-" * 60)
print("SCENARIO COMPARISON")
print("-" * 60)

scenario_results = {}
for scenario_name, route_fn in scenarios.items():
    total_pnl = 0.0
    total_fees = 0.0
    for t in with_conviction:
        votes = int(t["conviction"])
        order_type, fill_rate = route_fn(votes)
        notional = t["notional"]
        fee_per_side = notional * (TAKER_FEE_BPS if order_type == "taker" else MAKER_FEE_BPS) / 10000
        rt_fee = fee_per_side * 2
        total_pnl += fill_rate * (t["gross_pnl"] - rt_fee)
        total_fees += fill_rate * rt_fee
    scenario_results[scenario_name] = {"pnl": total_pnl, "fees": total_fees}

baseline_pnl = scenario_results["A: All Taker (current)"]["pnl"]
print(f"{'Scenario':<45} | {'Net PnL':>14} | {'vs Baseline':>12}")
print("-" * 78)
for name, r in scenario_results.items():
    diff = r["pnl"] - baseline_pnl
    print(f"{name:<45} | ${r['pnl']:>13,.0f} | ${diff:>11,.0f}")
