"""
Deep validation suite for the equity strategy.

Tests:
1. Regime-specific performance (bull, bear, chop, crash)
2. Walk-forward rolling OOS (no look-ahead bias)
3. Cross-asset correlation analysis
4. Drawdown stress testing & anatomy
5. Monte Carlo trade shuffling & per-trade stats

Usage:
    uv run engine/validate.py                # run all validations
    uv run engine/validate.py --test regime  # run one test
"""

import sys
import os
import time
import argparse
import importlib.util
import random
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE_DIR)

from prepare import (
    load_data, run_backtest, compute_score_daily_return,
    INITIAL_CAPITAL, BarData, PortfolioState, Signal, LOOKBACK_BARS,
    INTERVAL_CONFIG,
)

PROJECT_ROOT = os.path.dirname(ENGINE_DIR)
STRATEGY_PATH = os.path.join(PROJECT_ROOT, "strategies", "1h-equities", "strategy.py")
SYMBOLS = ["SPY", "QQQ", "IWM", "XLE", "XLF", "GLD", "TLT", "EEM", "XBI", "SOXX"]
SLIPPAGE_BPS = 0.5
TAKER_FEE = 0.0


def load_strategy():
    spec = importlib.util.spec_from_file_location("strategy_module", STRATEGY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


def run_bt(start, end, symbols=None):
    """Run backtest, return (score, result)."""
    strategy = load_strategy()
    syms = symbols or SYMBOLS
    data = load_data("val", symbols=syms, interval="1h", start_date=start, end_date=end)
    if not data:
        return -999, None
    result = run_backtest(strategy, data, interval="1h",
                          slippage_bps=SLIPPAGE_BPS, taker_fee=TAKER_FEE)
    score = compute_score_daily_return(result, max_dd_pct=15.0)
    return score, result


def fmt_result(label, score, result):
    if result is None:
        return f"  {label:<35s} NO DATA"
    eq = result.equity_curve
    end_eq = f"${eq[-1]:,.0f}" if eq else "N/A"
    ret = result.total_return_pct
    dd = result.max_drawdown_pct
    wr = result.win_rate_pct
    pf = result.profit_factor
    sh = result.sharpe
    nt = result.num_trades
    return (f"  {label:<35s} score={score:>8.1f}  sharpe={sh:>6.2f}  "
            f"ret={ret:>9.1f}%  dd={dd:>5.2f}%  trades={nt:>5d}  "
            f"wr={wr:>5.1f}%  pf={pf:>6.2f}  {end_eq}")


# ============================================================================
# 1. REGIME-SPECIFIC BACKTESTS
# ============================================================================

def test_regime():
    """Test strategy across distinct market regimes."""
    print("\n" + "=" * 80)
    print("1. REGIME-SPECIFIC BACKTESTS")
    print("=" * 80)

    regimes = [
        # (label, start, end, description)
        ("2023-Q3 bull run",         "2023-07-01", "2023-09-30", "SPY +5%, steady uptrend"),
        ("2023-Q4 correction+rally", "2023-10-01", "2023-12-31", "Oct selloff then year-end rally"),
        ("2024-Q1 melt-up",          "2024-01-01", "2024-03-31", "AI-driven tech rally, low vol"),
        ("2024-Q2 chop",             "2024-04-01", "2024-06-30", "Sideways, rate uncertainty"),
        ("2024-Q3 Aug VIX spike",    "2024-07-01", "2024-09-30", "Yen carry unwind, VIX > 65"),
        ("2024-Q4 election rally",   "2024-10-01", "2024-12-31", "Post-election rip"),
        ("2025-Q1 top formation",    "2025-01-01", "2025-03-31", "Market topping, rotation"),
        ("2025 Apr tariff crash",    "2025-04-01", "2025-06-30", "Tariff shock, -15% drawdown"),
        ("2025-Q3 recovery",         "2025-07-01", "2025-09-30", "Post-crash recovery"),
        ("2025-Q4 into 2026",        "2025-10-01", "2025-12-31", "Year-end positioning"),
        ("2026-Q1 new year",         "2026-01-01", "2026-04-10", "Most recent period"),
    ]

    results = []
    for label, start, end, desc in regimes:
        score, result = run_bt(start, end)
        print(fmt_result(f"{label}", score, result))
        if result:
            results.append((label, score, result))

    # Summary stats
    scores = [s for _, s, _ in results if s > -999]
    returns = [r.total_return_pct for _, _, r in results]
    dds = [r.max_drawdown_pct for _, _, r in results]
    win_rates = [r.win_rate_pct for _, _, r in results]

    print(f"\n  Regimes tested: {len(results)}")
    print(f"  Profitable regimes: {sum(1 for r in returns if r > 0)}/{len(returns)}")
    print(f"  Avg return: {np.mean(returns):.1f}%  (min: {np.min(returns):.1f}%, max: {np.max(returns):.1f}%)")
    print(f"  Avg max DD: {np.mean(dds):.2f}%  (worst: {np.max(dds):.2f}%)")
    print(f"  Avg win rate: {np.mean(win_rates):.1f}%")
    losing = [(l, r.total_return_pct) for l, _, r in results if r.total_return_pct < 0]
    if losing:
        print(f"  LOSING REGIMES: {losing}")
    else:
        print(f"  NO LOSING REGIMES")


# ============================================================================
# 2. WALK-FORWARD VALIDATION
# ============================================================================

def test_walkforward():
    """Rolling walk-forward: train 6mo, test next 3mo, roll forward."""
    print("\n" + "=" * 80)
    print("2. WALK-FORWARD VALIDATION (3-month rolling OOS windows)")
    print("=" * 80)
    print("  Note: strategy params are FIXED — no re-optimization per window.")
    print("  This tests if a single param set works across all time periods.\n")

    # 3-month windows from data start to end
    windows = []
    start_year = 2023
    start_month = 7  # first full quarter after data start (Jun 2023)

    while True:
        y = start_year + (start_month - 1) // 12
        m = ((start_month - 1) % 12) + 1
        end_month = start_month + 3
        ey = start_year + (end_month - 1) // 12
        em = ((end_month - 1) % 12) + 1

        s = f"{y:04d}-{m:02d}-01"
        e = f"{ey:04d}-{em:02d}-01"

        if ey > 2026 or (ey == 2026 and em > 4):
            break

        windows.append((s, e))
        start_month += 3

    all_returns = []
    all_dds = []
    all_sharpes = []

    for start, end in windows:
        score, result = run_bt(start, end)
        if result and result.num_trades > 0:
            print(fmt_result(f"{start} to {end}", score, result))
            all_returns.append(result.total_return_pct)
            all_dds.append(result.max_drawdown_pct)
            all_sharpes.append(result.sharpe)

    print(f"\n  Windows tested: {len(all_returns)}")
    print(f"  Profitable windows: {sum(1 for r in all_returns if r > 0)}/{len(all_returns)}")
    print(f"  Avg quarterly return: {np.mean(all_returns):.1f}%")
    print(f"  Min quarterly return: {np.min(all_returns):.1f}%")
    print(f"  Max quarterly return: {np.max(all_returns):.1f}%")
    print(f"  Std of quarterly returns: {np.std(all_returns):.1f}%")
    print(f"  Avg Sharpe: {np.mean(all_sharpes):.2f}")
    print(f"  Worst quarterly DD: {np.max(all_dds):.2f}%")
    print(f"  Consistency (% positive windows): {sum(1 for r in all_returns if r > 0)/len(all_returns)*100:.0f}%")


# ============================================================================
# 3. CROSS-ASSET CORRELATION ANALYSIS
# ============================================================================

def test_correlation():
    """Analyze return correlations across the ETF universe."""
    print("\n" + "=" * 80)
    print("3. CROSS-ASSET CORRELATION ANALYSIS")
    print("=" * 80)

    # Load raw data for correlation analysis
    data = load_data("val", symbols=SYMBOLS, interval="1h",
                     start_date="2024-01-01", end_date="2026-04-10")

    if not data:
        print("  No data available")
        return

    # Compute hourly returns per symbol
    returns_dict = {}
    for sym, df in data.items():
        closes = df["close"].values
        rets = np.diff(closes) / closes[:-1]
        returns_dict[sym] = rets[:min(len(r) for r in [rets])]  # will trim later

    # Align lengths
    min_len = min(len(v) for v in returns_dict.values())
    returns_df = pd.DataFrame({sym: rets[:min_len] for sym, rets in returns_dict.items()})

    # Correlation matrix
    corr = returns_df.corr()
    print("\n  Hourly Return Correlation Matrix:\n")
    print("       ", "  ".join(f"{s:>5s}" for s in corr.columns))
    for sym in corr.index:
        vals = "  ".join(f"{corr.loc[sym, s]:>5.2f}" for s in corr.columns)
        print(f"  {sym:<5s}  {vals}")

    # Average pairwise correlation
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    avg_corr = upper.stack().mean()
    max_corr = upper.stack().max()
    min_corr = upper.stack().min()

    print(f"\n  Avg pairwise correlation: {avg_corr:.3f}")
    print(f"  Max pairwise correlation: {max_corr:.3f}")
    print(f"  Min pairwise correlation: {min_corr:.3f}")

    # Find most and least correlated pairs
    pairs = upper.stack().sort_values()
    print(f"\n  Most uncorrelated pairs:")
    for (s1, s2), val in pairs.head(5).items():
        print(f"    {s1}/{s2}: {val:.3f}")
    print(f"  Most correlated pairs:")
    for (s1, s2), val in pairs.tail(5).items():
        print(f"    {s1}/{s2}: {val:.3f}")

    # Run per-symbol backtests and check if STRATEGY returns are correlated
    print(f"\n  Per-Symbol Strategy Return Correlation:")
    print(f"  (Do symbols win/lose at the same time?)\n")

    sym_equity = {}
    for sym in SYMBOLS:
        strategy = load_strategy()
        sym_data = load_data("val", symbols=[sym], interval="1h",
                             start_date="2024-07-01", end_date="2025-03-31")
        if sym_data:
            result = run_backtest(strategy, sym_data, interval="1h",
                                 slippage_bps=SLIPPAGE_BPS, taker_fee=TAKER_FEE)
            if result.equity_curve and len(result.equity_curve) > 10:
                sym_equity[sym] = np.array(result.equity_curve)

    # Compute equity curve returns and correlate
    min_len = min(len(v) for v in sym_equity.values())
    strat_returns = {}
    for sym, eq in sym_equity.items():
        eq = eq[:min_len]
        rets = np.diff(eq) / eq[:-1]
        strat_returns[sym] = rets

    strat_df = pd.DataFrame(strat_returns)
    strat_corr = strat_df.corr()

    upper_s = strat_corr.where(np.triu(np.ones(strat_corr.shape), k=1).astype(bool))
    avg_strat_corr = upper_s.stack().mean()

    print(f"  Avg pairwise STRATEGY return correlation: {avg_strat_corr:.3f}")
    print(f"  (lower = more diversification benefit)")

    if avg_strat_corr < 0.1:
        print(f"  EXCELLENT: Strategy returns are nearly uncorrelated across symbols")
    elif avg_strat_corr < 0.3:
        print(f"  GOOD: Moderate diversification benefit")
    else:
        print(f"  WARNING: Strategy returns are correlated — diversification benefit is limited")


# ============================================================================
# 4. DRAWDOWN STRESS TESTING
# ============================================================================

def test_drawdown():
    """Analyze worst drawdown periods in detail."""
    print("\n" + "=" * 80)
    print("4. DRAWDOWN STRESS TESTING")
    print("=" * 80)

    # Run full period backtest to get equity curve
    strategy = load_strategy()
    data = load_data("val", symbols=SYMBOLS, interval="1h",
                     start_date="2023-06-01", end_date="2026-04-10")
    result = run_backtest(strategy, data, interval="1h",
                          slippage_bps=SLIPPAGE_BPS, taker_fee=TAKER_FEE)

    eq = np.array(result.equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100

    # Find top 5 drawdown periods
    print(f"\n  Full period: Jun 2023 - Apr 2026")
    print(f"  Max drawdown: {dd.max():.2f}%")
    print(f"  Final equity: ${eq[-1]:,.0f}")

    # Find drawdown events > 2%
    in_dd = False
    dd_events = []
    dd_start = 0

    for i in range(len(dd)):
        if dd[i] > 1.0 and not in_dd:
            in_dd = True
            dd_start = i
        elif dd[i] < 0.1 and in_dd:
            in_dd = False
            dd_peak = dd[dd_start:i].max()
            dd_peak_idx = dd_start + np.argmax(dd[dd_start:i])
            dd_events.append((dd_start, i, dd_peak, dd_peak_idx))

    if in_dd:
        dd_peak = dd[dd_start:].max()
        dd_peak_idx = dd_start + np.argmax(dd[dd_start:])
        dd_events.append((dd_start, len(dd)-1, dd_peak, dd_peak_idx))

    dd_events.sort(key=lambda x: -x[2])
    print(f"\n  Drawdown events > 1%: {len(dd_events)}")

    # Analyze top 5
    print(f"\n  Top 5 Drawdowns:")
    print(f"  {'#':<4s} {'Depth':>7s} {'Duration':>10s} {'Recovery':>10s} {'Peak Eq':>12s} {'Trough Eq':>12s}")

    for i, (start, end, depth, peak_idx) in enumerate(dd_events[:5]):
        duration = peak_idx - start
        recovery = end - peak_idx
        peak_eq = eq[start]
        trough_eq = eq[peak_idx]
        print(f"  {i+1:<4d} {depth:>6.2f}%  {duration:>8d} bars  {recovery:>8d} bars  "
              f"${peak_eq:>10,.0f}  ${trough_eq:>10,.0f}")

    # Trade-level analysis during worst drawdown
    if dd_events:
        worst_start, worst_end, worst_depth, worst_peak = dd_events[0]
        trades = result.trade_log
        # Count wins/losses during drawdown period (approximate by trade index)
        total_trades = len(trades)
        bars_total = len(eq)
        approx_trade_start = int(worst_start / bars_total * total_trades)
        approx_trade_end = int(worst_end / bars_total * total_trades)
        dd_trades = trades[approx_trade_start:approx_trade_end]
        close_trades = [t for t in dd_trades if t[0] == "close"]
        if close_trades:
            dd_wins = sum(1 for t in close_trades if t[4] > 0)
            dd_losses = sum(1 for t in close_trades if t[4] < 0)
            dd_pnl = sum(t[4] for t in close_trades)
            print(f"\n  During worst drawdown ({worst_depth:.2f}%):")
            print(f"    Trades: {len(close_trades)} ({dd_wins} wins, {dd_losses} losses)")
            print(f"    Net PnL: ${dd_pnl:,.0f}")
            print(f"    Win rate during DD: {dd_wins/max(len(close_trades),1)*100:.1f}%")

    # Test with 2x slippage during high-vol periods
    print(f"\n  Stress test: 2x slippage during tariff crash (Apr-Jun 2025):")
    score, stress_result = run_bt("2025-04-01", "2025-06-30")
    if stress_result:
        print(fmt_result("  normal (0.5 bps)", score, stress_result))

    strategy2 = load_strategy()
    data2 = load_data("val", symbols=SYMBOLS, interval="1h",
                      start_date="2025-04-01", end_date="2025-06-30")
    stress_result2 = run_backtest(strategy2, data2, interval="1h",
                                  slippage_bps=2.0, taker_fee=0.0)
    score2 = compute_score_daily_return(stress_result2, max_dd_pct=15.0)
    print(fmt_result("  stressed (2.0 bps)", score2, stress_result2))


# ============================================================================
# 5. MONTE CARLO & TRADE-LEVEL ANALYSIS
# ============================================================================

def test_montecarlo():
    """Monte Carlo shuffling and per-trade statistics."""
    print("\n" + "=" * 80)
    print("5. MONTE CARLO & TRADE-LEVEL ANALYSIS")
    print("=" * 80)

    # Run full OOS backtest to get trade log
    strategy = load_strategy()
    data = load_data("val", symbols=SYMBOLS, interval="1h",
                     start_date="2025-04-01", end_date="2026-04-10")
    result = run_backtest(strategy, data, interval="1h",
                          slippage_bps=SLIPPAGE_BPS, taker_fee=TAKER_FEE)

    trades = result.trade_log
    close_trades = [t for t in trades if t[0] == "close"]
    pnls = [t[4] for t in close_trades]

    if not pnls:
        print("  No closed trades to analyze")
        return

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    print(f"\n  OOS Period: Apr 2025 - Apr 2026")
    print(f"  Total closed trades: {len(pnls)}")
    print(f"  Winners: {len(wins)}  Losers: {len(losses)}")
    print(f"  Win rate: {len(wins)/len(pnls)*100:.1f}%")

    print(f"\n  Per-Trade Statistics:")
    print(f"    Avg win:   ${np.mean(wins):>10,.2f}" if wins else "    No wins")
    print(f"    Avg loss:  ${np.mean(losses):>10,.2f}" if losses else "    No losses")
    print(f"    Max win:   ${np.max(wins):>10,.2f}" if wins else "")
    print(f"    Max loss:  ${np.min(losses):>10,.2f}" if losses else "")
    print(f"    Avg trade: ${np.mean(pnls):>10,.2f}")
    print(f"    Median:    ${np.median(pnls):>10,.2f}")
    print(f"    Std dev:   ${np.std(pnls):>10,.2f}")

    # Expectancy
    expectancy = np.mean(pnls)
    print(f"\n  Expectancy (avg PnL per trade): ${expectancy:,.2f}")
    print(f"  Expectancy ratio (avg_win/avg_loss): {abs(np.mean(wins)/np.mean(losses)):.2f}" if wins and losses else "")

    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    current_wins = 0
    current_losses = 0
    for p in pnls:
        if p > 0:
            current_wins += 1
            current_losses = 0
            max_consec_wins = max(max_consec_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_consec_losses = max(max_consec_losses, current_losses)

    print(f"\n  Max consecutive wins: {max_consec_wins}")
    print(f"  Max consecutive losses: {max_consec_losses}")

    # Top/bottom 10 trades — check if returns depend on outliers
    sorted_pnls = sorted(pnls)
    top10_pnl = sum(sorted_pnls[-10:])
    bottom10_pnl = sum(sorted_pnls[:10])
    total_pnl = sum(pnls)
    print(f"\n  Outlier Dependency:")
    print(f"    Total PnL:          ${total_pnl:>12,.0f}")
    print(f"    Top 10 trades PnL:  ${top10_pnl:>12,.0f}  ({top10_pnl/total_pnl*100:.1f}% of total)")
    print(f"    Bottom 10 trades:   ${bottom10_pnl:>12,.0f}")
    print(f"    PnL without top 10: ${total_pnl - top10_pnl:>12,.0f}")

    if top10_pnl / total_pnl > 0.5:
        print(f"    WARNING: Top 10 trades account for >{top10_pnl/total_pnl*100:.0f}% of profit")
    else:
        print(f"    GOOD: Returns are distributed across many trades")

    # Monte Carlo: shuffle trade PnLs and simulate equity curves
    print(f"\n  Monte Carlo Simulation (1000 shuffled paths):")
    n_sims = 1000
    mc_final_eq = []
    mc_max_dd = []

    for _ in range(n_sims):
        shuffled = pnls.copy()
        random.shuffle(shuffled)

        eq = [INITIAL_CAPITAL]
        for p in shuffled:
            eq.append(eq[-1] + p)
        eq = np.array(eq)
        peak = np.maximum.accumulate(eq)
        dd = ((peak - eq) / peak * 100).max()
        mc_final_eq.append(eq[-1])
        mc_max_dd.append(dd)

    mc_final = np.array(mc_final_eq)
    mc_dd = np.array(mc_max_dd)

    print(f"    Median final equity:  ${np.median(mc_final):>12,.0f}")
    print(f"    5th percentile:       ${np.percentile(mc_final, 5):>12,.0f}")
    print(f"    95th percentile:      ${np.percentile(mc_final, 95):>12,.0f}")
    print(f"    % of paths profitable: {(mc_final > INITIAL_CAPITAL).mean()*100:.1f}%")
    print(f"    Median max DD:         {np.median(mc_dd):>5.2f}%")
    print(f"    95th pctile max DD:    {np.percentile(mc_dd, 95):>5.2f}%")
    print(f"    Worst-case max DD:     {mc_dd.max():>5.2f}%")

    actual_final = result.equity_curve[-1] if result.equity_curve else 0
    actual_dd = result.max_drawdown_pct
    print(f"\n    Actual final equity:   ${actual_final:>12,.0f}")
    print(f"    Actual max DD:         {actual_dd:>5.2f}%")

    if actual_final > np.percentile(mc_final, 75):
        print(f"    NOTE: Actual result is ABOVE 75th pctile — trade sequencing helped")
    elif actual_final < np.percentile(mc_final, 25):
        print(f"    NOTE: Actual result is BELOW 25th pctile — trade sequencing hurt")
    else:
        print(f"    GOOD: Actual result is near median — not sequence-dependent")

    # Per-symbol PnL breakdown
    print(f"\n  Per-Symbol PnL Breakdown:")
    sym_pnl = defaultdict(lambda: {"pnl": 0, "wins": 0, "losses": 0, "trades": 0})
    for t in close_trades:
        sym = t[1]
        pnl = t[4]
        sym_pnl[sym]["pnl"] += pnl
        sym_pnl[sym]["trades"] += 1
        if pnl > 0:
            sym_pnl[sym]["wins"] += 1
        else:
            sym_pnl[sym]["losses"] += 1

    print(f"  {'Symbol':<8s} {'PnL':>12s} {'Trades':>8s} {'Win%':>7s} {'Avg PnL':>10s}")
    for sym in sorted(sym_pnl.keys(), key=lambda s: -sym_pnl[s]["pnl"]):
        d = sym_pnl[sym]
        wr = d["wins"] / max(d["trades"], 1) * 100
        avg = d["pnl"] / max(d["trades"], 1)
        print(f"  {sym:<8s} ${d['pnl']:>11,.0f} {d['trades']:>8d} {wr:>6.1f}% ${avg:>9,.0f}")


# ============================================================================
# Main
# ============================================================================

TESTS = {
    "regime": test_regime,
    "walkforward": test_walkforward,
    "correlation": test_correlation,
    "drawdown": test_drawdown,
    "montecarlo": test_montecarlo,
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deep validation for equity strategy")
    parser.add_argument("--test", default=None, help=f"Run specific test ({', '.join(TESTS.keys())})")
    args = parser.parse_args()

    t0 = time.time()

    if args.test:
        if args.test not in TESTS:
            print(f"Unknown test: {args.test}. Options: {', '.join(TESTS.keys())}")
            sys.exit(1)
        TESTS[args.test]()
    else:
        for name, fn in TESTS.items():
            fn()

    elapsed = time.time() - t0
    print(f"\n{'=' * 80}")
    print(f"Validation complete in {elapsed:.0f}s")
    print(f"{'=' * 80}")
