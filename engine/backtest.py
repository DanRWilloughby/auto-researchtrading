"""
Run backtest. Configurable for any interval and symbol set.

Usage:
    uv run engine/backtest.py                                    # 1h BTC/ETH/SOL on val
    uv run engine/backtest.py --interval 15m                     # 15m on val
    uv run engine/backtest.py --split test                       # 1h on test (forward sim)
    uv run engine/backtest.py --strategy strategies/1h-btc-eth-sol/strategy.py
    uv run engine/backtest.py --interval 30m --all-symbols
    uv run engine/backtest.py --compare                          # val vs test side-by-side
"""

import sys
import os
import time
import argparse
import importlib.util
import signal as sig

# Add project root to path so engine imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prepare import (
    load_data, run_backtest, compute_score, TIME_BUDGET,
    DEFAULT_SYMBOLS, VALID_INTERVALS, ALL_SYMBOLS,
)

# Timeout guard
def timeout_handler(signum, frame):
    print("TIMEOUT: backtest exceeded time budget")
    exit(1)

sig.signal(sig.SIGALRM, timeout_handler)
sig.alarm(TIME_BUDGET + 30)


def load_strategy(path):
    """Dynamically load a Strategy class from a file path."""
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--strategy", default=None,
                        help="Path to strategy.py (default: strategies/1h-btc-eth-sol/strategy.py)")
    parser.add_argument("--interval", default="1h",
                        help=f"Bar interval (default: 1h, options: {VALID_INTERVALS})")
    parser.add_argument("--split", default="val", choices=["train", "val", "test"],
                        help="Data split to backtest on (default: val)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=f"Symbols to trade (default: {DEFAULT_SYMBOLS})")
    parser.add_argument("--all-symbols", action="store_true",
                        help=f"Use all supported symbols: {ALL_SYMBOLS}")
    parser.add_argument("--compare", action="store_true",
                        help="Run both val and test splits for comparison")
    parser.add_argument("--start", default=None,
                        help="Custom start date (YYYY-MM-DD), overrides --split")
    parser.add_argument("--end", default=None,
                        help="Custom end date (YYYY-MM-DD), overrides --split")
    args = parser.parse_args()

    # Resolve strategy path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.strategy:
        strategy_path = os.path.join(project_root, args.strategy) if not os.path.isabs(args.strategy) else args.strategy
    else:
        strategy_path = os.path.join(project_root, "strategies", "1h-btc-eth-sol", "strategy.py")

    symbols = ALL_SYMBOLS if args.all_symbols else args.symbols

    use_custom_range = args.start and args.end
    splits = ["val", "test"] if args.compare else [args.split]

    for split_name in splits:
        t_start = time.time()

        strategy = load_strategy(strategy_path)
        if use_custom_range:
            data = load_data(split_name, symbols=symbols, interval=args.interval,
                             start_date=args.start, end_date=args.end)
        else:
            data = load_data(split_name, symbols=symbols, interval=args.interval)

        total_bars = sum(len(df) for df in data.values())
        if use_custom_range:
            print(f"=== CUSTOM RANGE: {args.start} to {args.end} ({args.interval}) ===")
        else:
            print(f"=== {split_name.upper()} SPLIT ({args.interval}) ===")
        print(f"Loaded {total_bars} bars across {list(data.keys())}")

        result = run_backtest(strategy, data, interval=args.interval)
        score = compute_score(result)

        t_end = time.time()

        print("---")
        print(f"score:              {score:.6f}")
        print(f"sharpe:             {result.sharpe:.6f}")
        print(f"total_return_pct:   {result.total_return_pct:.6f}")
        print(f"max_drawdown_pct:   {result.max_drawdown_pct:.6f}")
        print(f"num_trades:         {result.num_trades}")
        print(f"win_rate_pct:       {result.win_rate_pct:.6f}")
        print(f"profit_factor:      {result.profit_factor:.6f}")
        print(f"annual_turnover:    {result.annual_turnover:.2f}")
        print(f"backtest_seconds:   {result.backtest_seconds:.1f}")
        print(f"total_seconds:      {t_end - t_start:.1f}")

        eq = result.equity_curve
        if eq:
            print(f"start_equity:       ${eq[0]:,.0f}")
            print(f"end_equity:         ${eq[-1]:,.0f}")
        print()
