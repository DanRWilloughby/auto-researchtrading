"""Run engine.prepare.run_backtest() directly on Coinbase 1-min data aggregated
to 30-min. This should reproduce the 'cb-baseline' methodology exactly —
same engine code path that produced Sharpe 26.11 / 73.2% win rate.

If this reproduces ~73% win rate, the data is fine and my custom backtest has
a bug. If it shows ~50% win rate, the data has changed since the prior test.
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from engine.prepare import run_backtest  # noqa: E402

SYMBOLS = ["BTC", "ETH", "SOL"]
DATA_DIR = Path("/tmp/trading-check-refresh")
STRATEGY_PATH = PROJECT_ROOT / "strategies" / "30m-concentrated" / "strategy.py"


def load_1m(symbol):
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m_9mo.parquet")
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def aggregate_to_30m(df1m):
    g = df1m.set_index("dt").resample("30min", label="left", closed="left")
    agg = pd.DataFrame({
        "timestamp": g["timestamp"].first().astype("Int64"),
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna(subset=["close"]).reset_index(drop=True)
    agg["timestamp"] = agg["timestamp"].astype(int)
    agg["funding_rate"] = 0.0
    return agg[["timestamp", "open", "high", "low", "close", "volume", "funding_rate"]]


def load_strategy():
    spec = importlib.util.spec_from_file_location("strategy_module", STRATEGY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy()


def main():
    print("Loading 1-min Coinbase parquets + aggregating to 30-min...")
    data = {}
    for sym in SYMBOLS:
        df1 = load_1m(sym)
        data[sym] = aggregate_to_30m(df1)
        print(f"  {sym}: {len(data[sym]):,} 30-min bars  "
              f"{pd.to_datetime(data[sym]['timestamp'].iloc[0], unit='ms')} → "
              f"{pd.to_datetime(data[sym]['timestamp'].iloc[-1], unit='ms')}")

    strategy = load_strategy()

    print("\nRunning engine.run_backtest() — same code path as 'cb-baseline' test")
    print("Params: taker_fee=3bps, slippage=1bps, execute_delay=0 (matches cb-baseline notes)")
    result = run_backtest(
        strategy,
        data,
        interval="30m",
        taker_fee=0.0003,
        slippage_bps=1.0,
        execute_delay=0,
    )

    print()
    print("=" * 60)
    print(f"Sharpe:            {result.sharpe:.2f}")
    print(f"Total return:      {result.total_return_pct:,.2f}%")
    print(f"Max drawdown:      {result.max_drawdown_pct:.2f}%")
    print(f"Num trades:        {result.num_trades:,}")
    print(f"Win rate:          {result.win_rate_pct:.1f}%")
    print(f"Profit factor:     {result.profit_factor:.2f}")
    print(f"Annual turnover:   {result.annual_turnover:,.0f}")
    print()
    print("Prior cb-baseline (2026-04-12) had:")
    print("  Sharpe 26.11, Win rate 73.2%, Return 69,886%, Max DD 4.15%, 11,016 trades")
    print()
    print("If this run matches prior results, the engine+data work. If it doesn't,")
    print("something changed (strategy code, data source, or parameters).")


if __name__ == "__main__":
    main()
