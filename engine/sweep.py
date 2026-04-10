"""
Parameter sweep runner for equity strategy experiments.

Dynamically patches strategy parameters and runs backtests, logging all results.
Uses the same backtest engine — just overrides module-level constants.

Usage:
    uv run engine/sweep.py                    # run all sweeps
    uv run engine/sweep.py --sweep momentum   # run one sweep
    uv run engine/sweep.py --list             # show available sweeps
"""

import sys
import os
import time
import argparse
import importlib.util
import copy
from datetime import datetime

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE_DIR)

from prepare import load_data, run_backtest, compute_score, compute_score_daily_return

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRATEGY_PATH = os.path.join(PROJECT_ROOT, "strategies", "1h-equities", "strategy.py")
RESULTS_PATH = os.path.join(PROJECT_ROOT, "strategies", "1h-equities", "results.tsv")

SYMBOLS = ["SPY", "QQQ", "IWM", "XLE", "XLF", "GLD", "TLT", "EEM", "XBI", "SOXX"]
VAL_START = "2024-07-01"
VAL_END = "2025-03-31"
SLIPPAGE_BPS = 0.5
TAKER_FEE = 0.0
MAX_DD = 10.0

RESULTS_HEADER = "exp\tsplit\tscore\tsharpe\treturn_pct\tmax_dd_pct\ttrades\twin_rate\tprofit_factor\tsymbols\tdate\tnotes\n"


def load_strategy_module(path):
    """Load strategy module (not instance) for patching."""
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_experiment(label, notes, param_overrides, symbols=None, start=None, end=None,
                   slippage=None, fee=None):
    """Run one backtest with patched parameters. Returns (score, result)."""
    mod = load_strategy_module(STRATEGY_PATH)

    # Patch module-level constants
    for key, value in param_overrides.items():
        if hasattr(mod, key):
            setattr(mod, key, value)

    strategy = mod.Strategy()

    syms = symbols or SYMBOLS
    s = start or VAL_START
    e = end or VAL_END
    slip = slippage if slippage is not None else SLIPPAGE_BPS
    tfee = fee if fee is not None else TAKER_FEE

    data = load_data("val", symbols=syms, interval="1h", start_date=s, end_date=e)
    total_bars = sum(len(df) for df in data.values())

    result = run_backtest(strategy, data, interval="1h", slippage_bps=slip, taker_fee=tfee)
    score = compute_score_daily_return(result, max_dd_pct=MAX_DD)

    # Log to results.tsv
    write_header = not os.path.exists(RESULTS_PATH) or os.path.getsize(RESULTS_PATH) == 0
    with open(RESULTS_PATH, "a") as f:
        if write_header:
            f.write(RESULTS_HEADER)
        sym_str = "/".join(sorted(data.keys()))
        date_str = datetime.now().strftime("%Y-%m-%d")
        row = (
            f"{label}\t{s}_{e}\t{score:.2f}\t{result.sharpe:.2f}\t"
            f"{result.total_return_pct:.1f}\t{result.max_drawdown_pct:.4f}\t"
            f"{result.num_trades}\t{result.win_rate_pct:.1f}\t{result.profit_factor:.2f}\t"
            f"{sym_str}\t{date_str}\t{notes}\n"
        )
        f.write(row)

    return score, result


def print_result(label, score, result):
    """Print compact result line."""
    eq = result.equity_curve
    end_eq = f"${eq[-1]:,.0f}" if eq else "N/A"
    print(f"  {label:<30s} score={score:>8.1f}  sharpe={result.sharpe:>6.2f}  "
          f"ret={result.total_return_pct:>8.1f}%  dd={result.max_drawdown_pct:>5.2f}%  "
          f"trades={result.num_trades:>5d}  wr={result.win_rate_pct:>5.1f}%  "
          f"pf={result.profit_factor:>5.2f}  {end_eq}")


# ============================================================================
# Sweep definitions
# ============================================================================

def sweep_momentum():
    """Sweep MOMENTUM_THRESHOLD."""
    print("\n=== MOMENTUM THRESHOLD SWEEP ===")
    for val in [0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.010]:
        label = f"mom-{val}"
        score, result = run_experiment(label, f"momentum threshold={val}",
                                       {"MOMENTUM_THRESHOLD": val})
        print_result(label, score, result)


def sweep_take_profit():
    """Sweep TAKE_PROFIT_PCT."""
    print("\n=== TAKE PROFIT SWEEP ===")
    for val in [0.004, 0.005, 0.006, 0.007, 0.008, 0.010, 0.012, 0.015, 0.020]:
        label = f"tp-{val}"
        score, result = run_experiment(label, f"take profit={val}",
                                       {"TAKE_PROFIT_PCT": val})
        print_result(label, score, result)


def sweep_atr_stop():
    """Sweep ATR_STOP_MULT."""
    print("\n=== ATR STOP MULTIPLIER SWEEP ===")
    for val in [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]:
        label = f"atr-{val}"
        score, result = run_experiment(label, f"ATR stop mult={val}",
                                       {"ATR_STOP_MULT": val})
        print_result(label, score, result)


def sweep_rsi():
    """Sweep RSI parameters."""
    print("\n=== RSI PERIOD SWEEP ===")
    for val in [3, 5, 7, 10, 14]:
        label = f"rsi-period-{val}"
        score, result = run_experiment(label, f"RSI period={val}",
                                       {"RSI_PERIOD": val})
        print_result(label, score, result)

    print("\n=== RSI BULL/BEAR THRESHOLD SWEEP ===")
    for bull, bear in [(50, 50), (51, 49), (52, 48), (53, 47), (55, 45)]:
        label = f"rsi-thresh-{bull}-{bear}"
        score, result = run_experiment(label, f"RSI bull={bull} bear={bear}",
                                       {"RSI_BULL": bull, "RSI_BEAR": bear})
        print_result(label, score, result)

    print("\n=== RSI EXHAUSTION THRESHOLD SWEEP ===")
    for ob, os_ in [(65, 35), (68, 32), (70, 30), (72, 28), (75, 25), (80, 20)]:
        label = f"rsi-exhaust-{ob}-{os_}"
        score, result = run_experiment(label, f"RSI overbought={ob} oversold={os_}",
                                       {"RSI_OVERBOUGHT": ob, "RSI_OVERSOLD": os_})
        print_result(label, score, result)


def sweep_ema():
    """Sweep EMA fast/slow combos."""
    print("\n=== EMA FAST/SLOW SWEEP ===")
    for fast, slow in [(2, 8), (3, 10), (3, 12), (5, 15), (5, 20), (8, 21)]:
        label = f"ema-{fast}-{slow}"
        score, result = run_experiment(label, f"EMA fast={fast} slow={slow}",
                                       {"EMA_FAST": fast, "EMA_SLOW": slow})
        print_result(label, score, result)


def sweep_min_votes():
    """Sweep MIN_VOTES."""
    print("\n=== MIN VOTES SWEEP ===")
    for val in [2, 3, 4, 5]:
        label = f"votes-{val}"
        score, result = run_experiment(label, f"MIN_VOTES={val}",
                                       {"MIN_VOTES": val})
        print_result(label, score, result)


def sweep_cooldown():
    """Sweep COOLDOWN_BARS."""
    print("\n=== COOLDOWN SWEEP ===")
    for val in [0, 1, 2, 3, 5]:
        label = f"cooldown-{val}"
        score, result = run_experiment(label, f"cooldown={val} bars",
                                       {"COOLDOWN_BARS": val})
        print_result(label, score, result)


def sweep_htf():
    """Sweep higher-timeframe trend filter params."""
    print("\n=== HTF MOMENTUM WINDOW SWEEP ===")
    for val in [15, 20, 25, 33, 40, 50]:
        label = f"htf-mom-{val}"
        score, result = run_experiment(label, f"HTF momentum window={val}",
                                       {"HTF_MOM_WINDOW": val})
        print_result(label, score, result)

    print("\n=== HTF EMA SWEEP ===")
    for fast, slow in [(7, 30), (10, 40), (14, 52), (20, 60)]:
        label = f"htf-ema-{fast}-{slow}"
        score, result = run_experiment(label, f"HTF EMA fast={fast} slow={slow}",
                                       {"HTF_EMA_FAST": fast, "HTF_EMA_SLOW": slow})
        print_result(label, score, result)

    print("\n=== HTF MIN VOTES SWEEP ===")
    for val in [1, 2, 3]:
        label = f"htf-votes-{val}"
        score, result = run_experiment(label, f"HTF min votes={val}",
                                       {"HTF_MIN_VOTES": val})
        print_result(label, score, result)


def sweep_leverage():
    """Sweep BASE_POSITION_PCT (leverage)."""
    print("\n=== LEVERAGE SWEEP ===")
    for val in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
        label = f"lev-{val}x"
        score, result = run_experiment(label, f"leverage={val}x",
                                       {"BASE_POSITION_PCT": val})
        print_result(label, score, result)


def sweep_symbols():
    """Test per-symbol contribution and subsets."""
    print("\n=== PER-SYMBOL SOLO PERFORMANCE ===")
    for sym in SYMBOLS:
        label = f"solo-{sym}"
        score, result = run_experiment(label, f"Solo {sym} only",
                                       {}, symbols=[sym])
        print_result(label, score, result)

    print("\n=== SUBSET TESTS ===")
    subsets = {
        "equity-only":    ["SPY", "QQQ", "IWM", "XLF", "SOXX"],
        "diversified-5":  ["SPY", "GLD", "TLT", "XLE", "XBI"],
        "uncorrelated-4": ["SPY", "GLD", "TLT", "XLE"],
        "big-3":          ["SPY", "QQQ", "IWM"],
        "hedge-pair":     ["SPY", "TLT"],
        "no-gld-tlt":     ["SPY", "QQQ", "IWM", "XLE", "XLF", "EEM", "XBI", "SOXX"],
        "top-vol":        ["XBI", "SOXX", "XLE", "IWM"],
    }
    for name, syms in subsets.items():
        label = f"subset-{name}"
        score, result = run_experiment(label, f"Subset: {name} ({'/'.join(syms)})",
                                       {}, symbols=syms)
        print_result(label, score, result)


def sweep_slippage():
    """Test slippage sensitivity."""
    print("\n=== SLIPPAGE SENSITIVITY ===")
    for val in [0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0]:
        label = f"slip-{val}bps"
        score, result = run_experiment(label, f"slippage={val} bps",
                                       {}, slippage=val)
        print_result(label, score, result)


def sweep_combined():
    """Combine best params from all sweeps and validate across all splits."""
    best_params = {
        "MOMENTUM_THRESHOLD": 0.002,
        "TAKE_PROFIT_PCT": 0.004,
        "ATR_STOP_MULT": 10.0,
        "RSI_PERIOD": 10,
        "RSI_BULL": 51,
        "RSI_BEAR": 49,
        "RSI_OVERBOUGHT": 80,
        "RSI_OVERSOLD": 20,
        "EMA_FAST": 8,
        "EMA_SLOW": 21,
        "MIN_VOTES": 2,
        "COOLDOWN_BARS": 0,
        "HTF_MOM_WINDOW": 15,
        "HTF_EMA_FAST": 7,
        "HTF_EMA_SLOW": 30,
        "HTF_MIN_VOTES": 1,
    }

    print("\n=== COMBINED BEST PARAMS — ALL SPLITS ===")
    print("Params:", {k: v for k, v in best_params.items()})

    # Val split
    score, result = run_experiment("combined-val", "Combined best params — val split",
                                   best_params, start=VAL_START, end=VAL_END)
    print_result("combined-val", score, result)

    # OOS test
    score, result = run_experiment("combined-oos", "Combined best params — OOS Apr'25-Apr'26",
                                   best_params, start="2025-04-01", end="2026-04-10")
    print_result("combined-oos", score, result)

    # Train
    score, result = run_experiment("combined-train", "Combined best params — train Jun'23-Jun'24",
                                   best_params, start="2023-06-01", end="2024-06-30")
    print_result("combined-train", score, result)

    # Pessimistic slippage OOS
    score, result = run_experiment("combined-oos-2bps", "Combined best — OOS with 2bps slippage",
                                   best_params, start="2025-04-01", end="2026-04-10",
                                   slippage=2.0)
    print_result("combined-oos-2bps", score, result)

    # Also test partially combined — just the big wins (HTF + ATR + RSI exhaustion)
    # to check if we're overfitting by stacking everything
    partial_params = {
        "HTF_MOM_WINDOW": 15,
        "HTF_EMA_FAST": 7,
        "HTF_EMA_SLOW": 30,
        "HTF_MIN_VOTES": 1,
        "ATR_STOP_MULT": 10.0,
        "RSI_OVERBOUGHT": 80,
        "RSI_OVERSOLD": 20,
    }
    print("\n=== PARTIAL COMBINED (big wins only) ===")
    score, result = run_experiment("partial-val", "Partial combined (HTF+ATR+RSI exhaust) — val",
                                   partial_params, start=VAL_START, end=VAL_END)
    print_result("partial-val", score, result)

    score, result = run_experiment("partial-oos", "Partial combined (HTF+ATR+RSI exhaust) — OOS",
                                   partial_params, start="2025-04-01", end="2026-04-10")
    print_result("partial-oos", score, result)

    # Leverage variations on combined
    print("\n=== COMBINED + LEVERAGE SWEEP ===")
    for lev in [2.0, 2.5, 3.0, 3.5, 4.0]:
        params = dict(best_params)
        params["BASE_POSITION_PCT"] = lev
        label = f"combined-{lev}x-val"
        score, result = run_experiment(label, f"Combined best @ {lev}x leverage — val",
                                       params, start=VAL_START, end=VAL_END)
        print_result(label, score, result)


SWEEPS = {
    "momentum": sweep_momentum,
    "take-profit": sweep_take_profit,
    "atr-stop": sweep_atr_stop,
    "rsi": sweep_rsi,
    "ema": sweep_ema,
    "min-votes": sweep_min_votes,
    "cooldown": sweep_cooldown,
    "htf": sweep_htf,
    "leverage": sweep_leverage,
    "symbols": sweep_symbols,
    "slippage": sweep_slippage,
    "combined": sweep_combined,
}


def sweep_targeted():
    """Targeted tests around the partial combined winner."""
    partial = {
        "HTF_MOM_WINDOW": 15,
        "HTF_EMA_FAST": 7,
        "HTF_EMA_SLOW": 30,
        "HTF_MIN_VOTES": 1,
        "ATR_STOP_MULT": 10.0,
        "RSI_OVERBOUGHT": 80,
        "RSI_OVERSOLD": 20,
    }

    # Test adding RSI_PERIOD=10 to partial
    print("\n=== TARGETED: partial + RSI_PERIOD=10 ===")
    p = dict(partial); p["RSI_PERIOD"] = 10
    score, result = run_experiment("targeted-rsi10-val", "Partial + RSI period=10 — val",
                                   p, start=VAL_START, end=VAL_END)
    print_result("targeted-rsi10-val", score, result)
    score, result = run_experiment("targeted-rsi10-oos", "Partial + RSI period=10 — OOS",
                                   p, start="2025-04-01", end="2026-04-10")
    print_result("targeted-rsi10-oos", score, result)

    # Test adding MIN_VOTES=2 to partial
    print("\n=== TARGETED: partial + MIN_VOTES=2 ===")
    p = dict(partial); p["MIN_VOTES"] = 2
    score, result = run_experiment("targeted-votes2-val", "Partial + MIN_VOTES=2 — val",
                                   p, start=VAL_START, end=VAL_END)
    print_result("targeted-votes2-val", score, result)
    score, result = run_experiment("targeted-votes2-oos", "Partial + MIN_VOTES=2 — OOS",
                                   p, start="2025-04-01", end="2026-04-10")
    print_result("targeted-votes2-oos", score, result)

    # Test partial + RSI_PERIOD=10 + MIN_VOTES=2 (moderate combined)
    print("\n=== TARGETED: partial + RSI10 + VOTES2 (moderate) ===")
    p = dict(partial); p["RSI_PERIOD"] = 10; p["MIN_VOTES"] = 2
    score, result = run_experiment("moderate-val", "Moderate combined — val",
                                   p, start=VAL_START, end=VAL_END)
    print_result("moderate-val", score, result)
    score, result = run_experiment("moderate-oos", "Moderate combined — OOS",
                                   p, start="2025-04-01", end="2026-04-10")
    print_result("moderate-oos", score, result)
    score, result = run_experiment("moderate-train", "Moderate combined — train",
                                   p, start="2023-06-01", end="2024-06-30")
    print_result("moderate-train", score, result)

    # Partial at different leverage on OOS
    print("\n=== PARTIAL + LEVERAGE ON OOS ===")
    for lev in [2.0, 2.5, 3.0, 3.5, 4.0]:
        p = dict(partial); p["BASE_POSITION_PCT"] = lev
        label = f"partial-{lev}x-oos"
        score, result = run_experiment(label, f"Partial @ {lev}x — OOS",
                                       p, start="2025-04-01", end="2026-04-10")
        print_result(label, score, result)

    # Pessimistic slippage on partial OOS
    print("\n=== PARTIAL OOS — SLIPPAGE SENSITIVITY ===")
    for slip in [0.5, 1.0, 2.0, 3.0]:
        label = f"partial-oos-{slip}bps"
        score, result = run_experiment(label, f"Partial OOS @ {slip}bps slippage",
                                       partial, start="2025-04-01", end="2026-04-10",
                                       slippage=slip)
        print_result(label, score, result)


SWEEPS["targeted"] = sweep_targeted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parameter sweep for equity strategy")
    parser.add_argument("--sweep", default=None, help=f"Run specific sweep (use --list to see options)")
    parser.add_argument("--list", action="store_true", help="List available sweeps")
    args = parser.parse_args()

    if args.list:
        for name, fn in SWEEPS.items():
            print(f"  {name:<15s} {fn.__doc__}")
        sys.exit(0)

    if args.sweep:
        if args.sweep not in SWEEPS:
            print(f"Unknown sweep: {args.sweep}. Options: {', '.join(SWEEPS.keys())}")
            sys.exit(1)
        SWEEPS[args.sweep]()
    else:
        # Run all sweeps
        t0 = time.time()
        for name, fn in SWEEPS.items():
            fn()
        elapsed = time.time() - t0
        print(f"\n=== ALL SWEEPS COMPLETE ({elapsed:.0f}s) ===")
        # Count total experiments
        with open(RESULTS_PATH) as f:
            total = sum(1 for line in f) - 1  # minus header
        print(f"Total experiments logged: {total}")
