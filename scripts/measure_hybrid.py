#!/usr/bin/env python3
"""
measure_hybrid.py — Hybrid maker-taker shadow performance diagnostic.

Reads the 4 maker shadow state files + the live (paper-cb-early) taker state
and reports per-level:
  - Fill rate (maker vs taker fallback)
  - Avg maker price improvement (vs actual live taker price)
  - Avg taker fallback penalty (slippage/drift over the 30-min wait)
  - Total realized P&L
  - Total fees paid (maker + taker components)
  - Net equity vs live taker baseline

Designed to answer: "which maker aggressiveness (passive/quarter/mid/aggressive)
wins the best fill rate × price improvement × fee-savings trade-off in
production-like hybrid execution?"

Usage (from VM or local after scp'ing state files):
  python3 measure_hybrid.py

Pulls directly from the VM via ssh if --remote flag passed; otherwise reads
local files at /tmp/.
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PRICE_LEVELS = ["passive", "quarter", "mid", "aggressive"]


def load_state(level, remote=False):
    path = f"/home/openclaw/auto-researchtrading/live/state/maker_shadow_{level}_paper_state.json"
    if remote:
        out = subprocess.run(
            ["ssh", "root@100.109.85.37", f"sudo -u openclaw cat {path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)
    else:
        return json.load(open(f"/tmp/maker_shadow_{level}_paper_state.json"))


def load_shadow_raw(level, remote=False):
    """Load the internal state file (has stats + pending orders)."""
    path = f"/home/openclaw/auto-researchtrading/live/state/maker_shadow_{level}_state.json"
    if remote:
        out = subprocess.run(
            ["ssh", "root@100.109.85.37", f"sudo -u openclaw cat {path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)
    return json.load(open(f"/tmp/maker_shadow_{level}_state.json"))


def load_live_taker(remote=False):
    path = "/home/openclaw/auto-researchtrading/live/state/30m-concentrated_paper-cb-early_state.json"
    if remote:
        out = subprocess.run(
            ["ssh", "root@100.109.85.37", f"sudo -u openclaw cat {path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)
    return json.load(open("/tmp/30m-concentrated_paper-cb-early_state.json"))


def analyze_level(level, remote):
    paper = load_state(level, remote)
    raw = load_shadow_raw(level, remote)

    trades = paper.get("trade_log", [])
    ec = paper.get("equity_curve", [])
    stats = raw.get("stats", {})

    maker_trades = [t for t in trades if t.get("fill_mode") == "maker"]
    taker_trades = [t for t in trades if t.get("fill_mode") == "taker_fallback"]
    unknown = [t for t in trades if t.get("fill_mode") not in ("maker", "taker_fallback")]

    n_maker = len(maker_trades)
    n_taker = len(taker_trades)
    n_total = n_maker + n_taker

    # Fill rate from stats (includes pending not yet in trade_log)
    filled = stats.get("maker_would_have_filled", 0)
    missed = stats.get("maker_would_have_missed", 0)
    pending = stats.get("maker_pending", 0)
    resolved = filled + missed
    fill_rate = (filled / resolved * 100) if resolved > 0 else None

    # P&L / fee breakdown by mode
    maker_pnl = sum(t.get("pnl", 0) for t in maker_trades)
    taker_pnl = sum(t.get("pnl", 0) for t in taker_trades)
    maker_fees = sum(t.get("fee", 0) for t in maker_trades)
    taker_fees = sum(t.get("fee", 0) for t in taker_trades)
    maker_notional = sum(abs(t.get("notional_usd", 0)) for t in maker_trades)
    taker_notional = sum(abs(t.get("notional_usd", 0)) for t in taker_trades)

    maker_fee_bps = (maker_fees / maker_notional * 10000) if maker_notional > 0 else None
    taker_fee_bps = (taker_fees / taker_notional * 10000) if taker_notional > 0 else None

    # Equity
    init = ec[0]["equity"] if ec else 10000
    final = ec[-1]["equity"] if ec else init
    ret_pct = (final - init) / init * 100 if init else 0

    return {
        "level": level,
        "n_maker": n_maker,
        "n_taker": n_taker,
        "n_total": n_total,
        "n_pending": pending,
        "n_resolved": resolved,
        "fill_rate_pct": fill_rate,
        "maker_pnl": maker_pnl,
        "taker_pnl": taker_pnl,
        "maker_fees": maker_fees,
        "taker_fees": taker_fees,
        "maker_notional": maker_notional,
        "taker_notional": taker_notional,
        "maker_fee_bps": maker_fee_bps,
        "taker_fee_bps": taker_fee_bps,
        "init_equity": init,
        "final_equity": final,
        "return_pct": ret_pct,
        "unknown_trades": len(unknown),
    }


def analyze_taker_baseline(remote):
    t = load_live_taker(remote)
    trades = t.get("trade_log", [])
    ec = t.get("equity_curve", [])
    init = ec[0]["equity"] if ec else 10000
    final = ec[-1]["equity"] if ec else init
    return {
        "trades": len(trades),
        "init": init,
        "final": final,
        "return_pct": (final - init) / init * 100 if init else 0,
    }


def fmt(v, spec=".2f", default="—"):
    if v is None:
        return default
    try:
        return format(v, spec)
    except Exception:
        return str(v)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--remote", action="store_true",
                   help="Pull state files from VM via ssh (default: local /tmp)")
    args = p.parse_args()

    print("=" * 96)
    print("HYBRID MAKER SHADOW — PERFORMANCE DIAGNOSTIC")
    print("=" * 96)

    # Live taker baseline
    try:
        baseline = analyze_taker_baseline(args.remote)
        print(f"\nLive taker baseline (paper-cb-early):")
        print(f"  trades: {baseline['trades']}  |  "
              f"equity: ${baseline['init']:,.0f} → ${baseline['final']:,.2f}  |  "
              f"return: {baseline['return_pct']:+.2f}%")
    except Exception as e:
        print(f"  (baseline not available: {e})")
        baseline = None

    print()
    print("Per-level hybrid results:")
    print("-" * 96)
    hdr = f"{'level':11s} {'total':>5s} {'maker':>5s} {'taker':>5s} {'pend':>5s}  "\
          f"{'fill%':>6s}  {'mkr_bps':>7s} {'tkr_bps':>7s}  "\
          f"{'equity':>10s} {'return':>8s} {'vs taker':>9s}"
    print(hdr)
    print("-" * 96)

    results = []
    for level in PRICE_LEVELS:
        try:
            r = analyze_level(level, args.remote)
            results.append(r)
            vs_taker = (r["return_pct"] - baseline["return_pct"]) if baseline else None
            print(f"{r['level']:11s} {r['n_total']:>5d} {r['n_maker']:>5d} {r['n_taker']:>5d} "
                  f"{r['n_pending']:>5d}  "
                  f"{fmt(r['fill_rate_pct'], '.1f'):>5s}%  "
                  f"{fmt(r['maker_fee_bps'], '.2f'):>7s} {fmt(r['taker_fee_bps'], '.2f'):>7s}  "
                  f"${r['final_equity']:>9,.2f} {r['return_pct']:>+7.2f}% "
                  f"{fmt(vs_taker, '+.2f', '—'):>8s}pp")
        except Exception as e:
            print(f"{level:11s} ERROR: {e}")

    # Interpretation guide
    print()
    print("=" * 96)
    print("READ THE NUMBERS")
    print("=" * 96)
    print("- fill%:    share of resolved orders that filled as MAKER (higher = more price improvement)")
    print("- mkr_bps:  realized maker fee rate (should be ~2.5 bps + per-contract regulatory)")
    print("- tkr_bps:  realized taker fallback fee rate (should match live taker fees ~5-10 bps)")
    print("- vs taker: hybrid excess return over live taker baseline (positive = hybrid wins)")
    print()
    print("Expectation: more-aggressive levels should show HIGHER fill% (lower taker fallback),")
    print("but may fill at WORSE prices (closer to taker). The sweet spot maximizes")
    print("(fill_rate × price_improvement) + (1-fill_rate) × (-slippage_penalty).")
    print()
    print("If hybrid ≤ taker baseline across all levels after 50+ trades, maker pilot isn't worth it.")
    print("If one or more levels beat taker by >2pp with >70% fill rate, that's the production config.")


if __name__ == "__main__":
    sys.exit(main())
