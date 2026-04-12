"""
Compare 4 strategy variants: 30m baseline, 1h candles, MIN_VOTES=4, no-trade hours.
Extracts fee/gross ratio from each backtest's trade log.

Usage:
    uv run scripts/compare_fee_variants.py
"""

import sys
import os

_engine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine")
sys.path.insert(0, _engine_dir)

import importlib.util
from prepare import load_data, download_data, run_backtest, INTERVAL_CONFIG, INITIAL_CAPITAL

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STRATEGY_DIR = os.path.join(PROJECT_ROOT, "strategies", "30m-concentrated")


def load_strategy(filename):
    path = os.path.join(STRATEGY_DIR, filename)
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


def analyze_result(name, result, interval):
    """Extract fee/gross metrics from a BacktestResult's trade_log."""
    trade_log = result.trade_log

    total_fees = 0.0
    total_gross_pnl = 0.0
    for t in trade_log:
        # trade record: (action, symbol, delta, exec_price, pnl, ts, fee, metadata)
        fee = t[6] if len(t) > 6 else 0.0
        pnl = t[4]
        total_fees += fee
        if t[0] == "close" or (t[0] == "modify" and pnl != 0):
            total_gross_pnl += pnl

    net_pnl = total_gross_pnl - total_fees
    fee_gross_ratio = (total_fees / total_gross_pnl * 100) if total_gross_pnl > 0 else float('inf')

    # Per-bar return for fair comparison across timeframes
    import math
    final_eq = result.equity_curve[-1] if result.equity_curve else INITIAL_CAPITAL
    n_bars = len(result.equity_curve) - 1
    if n_bars > 0 and final_eq > INITIAL_CAPITAL:
        per_bar_log_ret = math.log(final_eq / INITIAL_CAPITAL) / n_bars
    else:
        per_bar_log_ret = 0.0

    return {
        "name": name,
        "sharpe": result.sharpe,
        "win_rate": result.win_rate_pct,
        "trades": result.num_trades,
        "max_dd": result.max_drawdown_pct,
        "profit_factor": result.profit_factor,
        "return_pct": result.total_return_pct,
        "total_fees": total_fees,
        "total_gross": total_gross_pnl,
        "total_net": net_pnl,
        "fee_gross_ratio": fee_gross_ratio,
        "per_bar_bps": per_bar_log_ret * 10000,
        "n_bars": n_bars,
    }


# ---------------------------------------------------------------------------
# Run all 4 variants
# ---------------------------------------------------------------------------

variants = [
    ("30m Baseline", "strategy.py", "30m"),
    ("1h Candles", "strategy.py", "1h"),
    ("MIN_VOTES=4", "strategy_min4.py", "30m"),
    ("No-trade 02-08 UTC", "strategy_notrade_hours.py", "30m"),
]

results = []
for name, strategy_file, interval in variants:
    print(f"\n--- Running: {name} ({interval}) ---")
    download_data(symbols=["BTC", "ETH", "SOL"], interval=interval, source="coinbase")
    data = load_data("test", symbols=["BTC", "ETH", "SOL"], interval=interval, source="coinbase")
    strategy = load_strategy(strategy_file)
    result = run_backtest(strategy, data, interval=interval, taker_fee=0.0003)
    stats = analyze_result(name, result, interval)
    results.append(stats)
    print(f"  Sharpe: {stats['sharpe']:.2f}, Trades: {stats['trades']:,}, "
          f"Fee/Gross: {stats['fee_gross_ratio']:.1f}%")

# ---------------------------------------------------------------------------
# Print comparison table
# ---------------------------------------------------------------------------

print("\n" + "=" * 130)
print("FEE-REDUCTION VARIANT COMPARISON (Coinbase TEST split)")
print("=" * 130)

header = (f"{'Variant':<24} | {'Sharpe':>7} | {'Win%':>6} | {'Trades':>7} | "
          f"{'MaxDD%':>7} | {'PF':>5} | {'Total Fees':>13} | {'Gross PnL':>13} | "
          f"{'Net PnL':>13} | {'Fee/Gross':>9} | {'bps/bar':>8}")
print(header)
print("-" * 130)

for s in results:
    row = (f"{s['name']:<24} | {s['sharpe']:>7.2f} | {s['win_rate']:>5.1f}% | {s['trades']:>7,} | "
           f"{s['max_dd']:>6.2f}% | {s['profit_factor']:>5.2f} | ${s['total_fees']:>12,.0f} | "
           f"${s['total_gross']:>12,.0f} | ${s['total_net']:>12,.0f} | "
           f"{s['fee_gross_ratio']:>8.1f}% | {s['per_bar_bps']:>7.3f}")
    print(row)

# ---------------------------------------------------------------------------
# Relative comparison vs baseline
# ---------------------------------------------------------------------------

baseline = results[0]
print("\n" + "=" * 100)
print("RELATIVE TO 30m BASELINE")
print("=" * 100)
print(f"{'Variant':<24} | {'Sharpe Δ':>10} | {'Trade Δ':>10} | {'Fee Δ':>14} | "
      f"{'Net PnL Δ':>14} | {'Fee/Gross Δ':>11}")
print("-" * 100)

for s in results:
    sharpe_d = s["sharpe"] - baseline["sharpe"]
    trade_d = s["trades"] - baseline["trades"]
    fee_d = s["total_fees"] - baseline["total_fees"]
    net_d = s["total_net"] - baseline["total_net"]
    fg_d = s["fee_gross_ratio"] - baseline["fee_gross_ratio"]

    row = (f"{s['name']:<24} | {sharpe_d:>+9.2f} | {trade_d:>+9,} | "
           f"${fee_d:>+13,.0f} | ${net_d:>+13,.0f} | {fg_d:>+10.1f}%")
    print(row)

# ---------------------------------------------------------------------------
# Normalize for fair cross-timeframe comparison
# ---------------------------------------------------------------------------

print("\n" + "=" * 80)
print("PER-BAR RETURN (normalized for different bar counts)")
print("=" * 80)
print(f"{'Variant':<24} | {'Bars':>7} | {'bps/bar':>8} | {'Projected 22K-bar Return':>25}")
print("-" * 80)

import math
ref_bars = results[0]["n_bars"]  # 30m baseline bar count

for s in results:
    projected_mult = math.exp(s["per_bar_bps"] / 10000 * ref_bars)
    projected_ret = (projected_mult - 1) * 100
    print(f"{s['name']:<24} | {s['n_bars']:>7,} | {s['per_bar_bps']:>7.3f} | "
          f"{projected_ret:>24,.0f}%")
