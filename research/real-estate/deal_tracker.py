#!/usr/bin/env python3
"""
Deal Flow Tracker — Paterson, NJ
Tracks properties over time to validate model predictions.

Stores weekly snapshots so we can answer:
- Did a high-scored property eventually sell? At what price?
- How accurate were our cap rate / equity multiple predictions?
- Which distress signals actually predicted motivated sellers?

Usage:
  python deal_tracker.py snapshot              # Take weekly snapshot
  python deal_tracker.py compare               # Compare to last snapshot
  python deal_tracker.py report                # Generate tracking report
  python deal_tracker.py validate              # Validate predictions vs outcomes
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
TRACKER_DIR = Path(__file__).parent / "tracking"
TRACKER_DIR.mkdir(exist_ok=True)


def take_snapshot():
    """Take a snapshot of current state for future comparison."""
    print("Taking snapshot...")
    df = pd.read_csv(DATA_DIR / "paterson_ranked_deals.csv")
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # Store key metrics for top 500 properties (manageable file size)
    top = df.head(500)
    snapshot = {
        "timestamp": now.isoformat(),
        "date": date_str,
        "total_scored": len(df),
        "market_stats": {
            "median_market_value": float(df["est_market_value"].median()),
            "median_cap_rate": float(df["cap_rate_current"].median()),
            "median_equity_multiple": float(df["equity_multiple"].median()),
            "sheriff_sales": int(df["in_sheriff_sale"].sum()),
            "high_distress": int(len(df[df["distress_score"] >= 3])),
        },
        "properties": [],
    }

    for _, row in top.iterrows():
        snapshot["properties"].append({
            "address": row["address"],
            "zip": str(row["zip"]),
            "prop_class": row["prop_class"],
            "units": int(row["est_units"]),
            "market_value": float(row["est_market_value"]),
            "cap_rate": float(row["cap_rate_current"]),
            "equity_multiple": float(row["equity_multiple"]),
            "distress_score": int(row["distress_score"]),
            "composite_score": float(row["composite_score"]),
            "sheriff_sale": bool(row.get("in_sheriff_sale", False)),
            "absentee": row.get("absentee_status", "unknown"),
        })

    path = TRACKER_DIR / f"snapshot_{date_str}.json"
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)

    # Also save as "latest"
    latest_path = TRACKER_DIR / "snapshot_latest.json"
    with open(latest_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"  Saved snapshot: {path}")
    print(f"  Properties tracked: {len(snapshot['properties'])}")
    print(f"  Market stats: cap rate {snapshot['market_stats']['median_cap_rate']:.1%}, "
          f"equity {snapshot['market_stats']['median_equity_multiple']:.2f}x")
    return snapshot


def compare_snapshots():
    """Compare current state to previous snapshot."""
    print("Comparing to previous snapshot...\n")

    # Load current
    df_current = pd.read_csv(DATA_DIR / "paterson_ranked_deals.csv")

    # Find previous snapshots
    snapshots = sorted(TRACKER_DIR.glob("snapshot_20*.json"))
    if len(snapshots) < 2:
        print("  Need at least 2 snapshots to compare. Run 'snapshot' first.")
        # Take one now for next time
        take_snapshot()
        return

    # Load most recent previous snapshot
    with open(snapshots[-2]) as f:
        prev = json.load(f)

    prev_date = prev["date"]
    prev_addrs = {p["address"]: p for p in prev["properties"]}
    current_addrs = set(df_current["address"].values)

    print(f"  Comparing: {prev_date} → today")
    print(f"  Previous top 500: {len(prev['properties'])} properties")
    print(f"  Current scored: {len(df_current)}")

    # Market stats comparison
    print(f"\n  Market Changes:")
    curr_stats = {
        "median_market_value": float(df_current["est_market_value"].median()),
        "median_cap_rate": float(df_current["cap_rate_current"].median()),
        "median_equity_multiple": float(df_current["equity_multiple"].median()),
    }
    for key in curr_stats:
        prev_val = prev["market_stats"].get(key, 0)
        curr_val = curr_stats[key]
        if "value" in key:
            change = f"${prev_val:,.0f} → ${curr_val:,.0f}"
        elif "rate" in key:
            change = f"{prev_val:.1%} → {curr_val:.1%}"
        else:
            change = f"{prev_val:.2f} → {curr_val:.2f}"
        print(f"    {key}: {change}")

    # Property movements
    no_longer_top = []
    score_changes = []

    for addr, prev_data in prev_addrs.items():
        curr_row = df_current[df_current["address"] == addr]
        if curr_row.empty:
            no_longer_top.append(prev_data)
        else:
            curr = curr_row.iloc[0]
            score_delta = curr["composite_score"] - prev_data["composite_score"]
            if abs(score_delta) > 0.05:
                score_changes.append({
                    "address": addr,
                    "old_score": prev_data["composite_score"],
                    "new_score": float(curr["composite_score"]),
                    "delta": score_delta,
                })

    if no_longer_top:
        print(f"\n  Properties dropped from top 500: {len(no_longer_top)}")
        for p in no_longer_top[:5]:
            print(f"    - {p['address']} (was score {p['composite_score']:.3f})")

    if score_changes:
        score_changes.sort(key=lambda x: -abs(x["delta"]))
        print(f"\n  Biggest score changes:")
        for sc in score_changes[:10]:
            direction = "↑" if sc["delta"] > 0 else "↓"
            print(f"    {direction} {sc['address']}: {sc['old_score']:.3f} → {sc['new_score']:.3f} ({sc['delta']:+.3f})")


def detect_sales():
    """Detect properties that may have sold since last snapshot."""
    print("Checking for potential sales...\n")

    with open(DATA_DIR / "paterson_parcels.json") as f:
        parcels = json.load(f)

    # Build current sale price index
    current_sales = {}
    for p in parcels:
        addr = (p.get("PROP_LOC") or "").strip().upper()
        sp = p.get("SALE_PRICE") or 0
        dd = p.get("DEED_DATE") or ""
        if addr and sp > 50000:
            current_sales[addr] = {"price": sp, "deed_date": dd}

    # Load tracked properties from latest snapshot
    latest_path = TRACKER_DIR / "snapshot_latest.json"
    if not latest_path.exists():
        print("  No snapshot to compare. Run 'snapshot' first.")
        return

    with open(latest_path) as f:
        snapshot = json.load(f)

    # Check if any tracked properties have new sales
    validated = []
    for prop in snapshot["properties"]:
        addr = prop["address"]
        sale_info = current_sales.get(addr)
        if sale_info and sale_info["price"] > 0:
            predicted_value = prop["market_value"]
            actual_price = sale_info["price"]
            accuracy = actual_price / predicted_value if predicted_value > 0 else 0

            validated.append({
                "address": addr,
                "predicted": predicted_value,
                "actual": actual_price,
                "accuracy": accuracy,
                "equity_multiple": prop["equity_multiple"],
                "distress_score": prop["distress_score"],
            })

    if validated:
        print(f"  Properties with recorded sales: {len(validated)}")
        # Accuracy stats
        accuracies = [v["accuracy"] for v in validated]
        print(f"  Prediction accuracy (actual/predicted):")
        print(f"    Median: {pd.Series(accuracies).median():.2f}x")
        print(f"    Mean: {pd.Series(accuracies).mean():.2f}x")

        # Best predictions
        validated.sort(key=lambda x: abs(1 - x["accuracy"]))
        print(f"\n  Most accurate predictions:")
        for v in validated[:5]:
            print(f"    {v['address']}: predicted ${v['predicted']:,.0f}, "
                  f"actual ${v['actual']:,.0f} ({v['accuracy']:.2f}x)")
    else:
        print("  No sales detected yet for tracked properties.")
        print("  This will populate as the model runs over weeks/months.")


def generate_report():
    """Generate a tracking report across all snapshots."""
    snapshots = sorted(TRACKER_DIR.glob("snapshot_20*.json"))
    if not snapshots:
        print("No snapshots found. Run 'snapshot' first.")
        return

    print(f"{'=' * 60}")
    print(f"  DEAL FLOW TRACKING REPORT")
    print(f"  {len(snapshots)} snapshots from {snapshots[0].stem.replace('snapshot_', '')} "
          f"to {snapshots[-1].stem.replace('snapshot_', '')}")
    print(f"{'=' * 60}")

    for snap_path in snapshots:
        with open(snap_path) as f:
            snap = json.load(f)
        stats = snap["market_stats"]
        print(f"\n  {snap['date']}: "
              f"{snap['total_scored']} props | "
              f"cap {stats['median_cap_rate']:.1%} | "
              f"eq {stats['median_equity_multiple']:.2f}x | "
              f"sheriff {stats['sheriff_sales']} | "
              f"distress {stats['high_distress']}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python deal_tracker.py [snapshot|compare|report|validate]")
        return

    cmd = sys.argv[1]
    if cmd == "snapshot":
        take_snapshot()
    elif cmd == "compare":
        compare_snapshots()
    elif cmd == "report":
        generate_report()
    elif cmd == "validate":
        detect_sales()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
