"""
Compare 30-minute candles between Hyperliquid and Coinbase for BTC/ETH/SOL.

Purpose: before any live trading, validate that Coinbase candle data aligns
with Hyperliquid (which the backtest + paper trading were calibrated on).

If average % difference in close prices is:
  <5 bps  : venues are well-aligned, safe to switch
  5-20 bps: minor microstructure difference, probably fine
  >20 bps : likely timezone/bar-close misalignment — investigate before live

Pulls 7 days of 30-min bars, joins on timestamp, computes per-bar % diff.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests

from exchanges import CoinbaseClient

HL_URL = "https://api.hyperliquid.xyz/info"
DAYS = 7
INTERVAL = "30m"
INTERVAL_MS = 30 * 60 * 1000


def fetch_hl_candles(coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Hyperliquid candleSnapshot — chunks in 5000-bar windows."""
    rows = []
    cursor = start_ms
    chunk_ms = 4900 * INTERVAL_MS  # stay safely under 5000 bar cap
    while cursor < end_ms:
        window_end = min(cursor + chunk_ms, end_ms)
        body = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": INTERVAL, "startTime": cursor, "endTime": window_end},
        }
        try:
            r = requests.post(HL_URL, json=body, timeout=15)
            r.raise_for_status()
            data = r.json() or []
            for c in data:
                rows.append({
                    "timestamp": int(c["t"]),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c.get("v", 0)),
                })
        except Exception as e:
            print(f"  HL fetch error for {coin}: {e}")
        cursor = window_end
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def compare_one(symbol: str, hl_df: pd.DataFrame, cb_df: pd.DataFrame):
    print(f"\n{'-' * 70}")
    print(f"  {symbol}")
    print("-" * 70)
    print(f"  HL bars      : {len(hl_df)}")
    print(f"  Coinbase bars: {len(cb_df)}")

    if len(hl_df) == 0 or len(cb_df) == 0:
        print("  ⚠️  One side returned no data — cannot compare")
        return None

    # Inner join on timestamp
    merged = hl_df.merge(cb_df, on="timestamp", suffixes=("_hl", "_cb"))
    print(f"  Joined bars  : {len(merged)}")

    if len(merged) == 0:
        # Timestamps don't overlap at all — show both ranges for debugging
        print(f"  ⚠️  NO TIMESTAMP OVERLAP")
        print(f"    HL range      : {hl_df['timestamp'].min()} → {hl_df['timestamp'].max()}")
        print(f"    Coinbase range: {cb_df['timestamp'].min()} → {cb_df['timestamp'].max()}")
        hl_sample = hl_df['timestamp'].iloc[0]
        cb_sample = cb_df['timestamp'].iloc[0]
        diff_sec = (cb_sample - hl_sample) / 1000
        print(f"    Offset        : {diff_sec:+,.0f} sec  ({diff_sec/60:+.1f} min)")
        return None

    # Per-bar % difference in close
    merged["diff_pct"] = (merged["close_cb"] - merged["close_hl"]) / merged["close_hl"] * 100
    merged["diff_bps"] = merged["diff_pct"] * 100  # bps

    mean_bps = merged["diff_bps"].mean()
    median_bps = merged["diff_bps"].median()
    abs_mean_bps = merged["diff_bps"].abs().mean()
    p95_bps = merged["diff_bps"].abs().quantile(0.95)
    max_bps = merged["diff_bps"].abs().max()

    print(f"  Close price alignment (Coinbase - HL):")
    print(f"    Mean diff           : {mean_bps:+.2f} bps")
    print(f"    Median diff         : {median_bps:+.2f} bps")
    print(f"    Mean |diff|         : {abs_mean_bps:.2f} bps")
    print(f"    P95 |diff|          : {p95_bps:.2f} bps")
    print(f"    Max |diff|          : {max_bps:.2f} bps")

    # Rating
    if abs_mean_bps < 5:
        rating = "✅ EXCELLENT (<5 bps) — venues aligned, safe to switch"
    elif abs_mean_bps < 20:
        rating = "✅ ACCEPTABLE (5-20 bps) — minor microstructure diff"
    else:
        rating = "⚠️  INVESTIGATE (>20 bps) — likely timezone/alignment issue"
    print(f"  Rating: {rating}")

    # Show 3 worst bars
    worst = merged.iloc[merged["diff_bps"].abs().argsort()[::-1][:3]]
    if len(worst) > 0:
        print(f"  Worst 3 bars:")
        for _, row in worst.iterrows():
            ts = pd.to_datetime(row["timestamp"], unit="ms")
            print(f"    {ts}  HL=${row['close_hl']:,.2f}  CB=${row['close_cb']:,.2f}  diff={row['diff_bps']:+.1f} bps")

    return {
        "symbol": symbol,
        "bars": len(merged),
        "mean_bps": mean_bps,
        "abs_mean_bps": abs_mean_bps,
        "p95_bps": p95_bps,
        "max_bps": max_bps,
    }


def main():
    print("=" * 70)
    print("  HYPERLIQUID vs COINBASE CANDLE ALIGNMENT CHECK")
    print(f"  {DAYS} days of {INTERVAL} bars")
    print("=" * 70)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 24 * 3600 * 1000

    cb_client = CoinbaseClient(dry_run_default=True)

    results = []
    for symbol in ("BTC", "ETH", "SOL"):
        print(f"\nFetching {symbol}...")
        hl_df = fetch_hl_candles(symbol, start_ms, now_ms)
        cb_df = cb_client.fetch_candles(symbol, INTERVAL, start_ms, now_ms)
        result = compare_one(symbol, hl_df, cb_df)
        if result:
            results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    if results:
        print(f"  {'Symbol':<8} {'Bars':>6} {'Mean':>10} {'|Mean|':>10} {'P95':>10} {'Max':>10}")
        for r in results:
            print(f"  {r['symbol']:<8} {r['bars']:>6} "
                  f"{r['mean_bps']:>+8.2f}bp "
                  f"{r['abs_mean_bps']:>8.2f}bp "
                  f"{r['p95_bps']:>8.2f}bp "
                  f"{r['max_bps']:>8.2f}bp")

        all_safe = all(r["abs_mean_bps"] < 20 for r in results)
        if all_safe:
            print("\n  ✅ All coins pass alignment check. Safe to proceed with live trading on Coinbase.")
        else:
            print("\n  ⚠️  One or more coins exceeded 20 bps threshold. Investigate before going live.")
    else:
        print("  ⚠️  No valid comparisons completed.")


if __name__ == "__main__":
    main()
