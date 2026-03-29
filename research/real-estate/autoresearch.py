#!/usr/bin/env python3
"""
Real Estate Auto-Research — Paterson, NJ
Automated deal sourcing for Veloce Capital + Forte Investment Fund

Runs on a schedule (daily/weekly). Detects new opportunities, sheriff sales,
price changes, and generates actionable alerts.

Usage:
  python autoresearch.py                  # Full refresh + report
  python autoresearch.py --delta-only     # Only show changes since last run
  python autoresearch.py --alerts         # Only show high-priority alerts
  python autoresearch.py --refresh        # Force re-download all data
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# Import the pipeline
from pipeline import (
    DATA_DIR,
    INVESTABLE_CLASSES,
    fetch_paterson_parcels,
    load_hud_safmr,
    load_zillow_zori,
    load_census_acs,
    load_sheriff_sales,
    score_property,
    percentile_rank,
)

REPORT_DIR = Path(__file__).parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "autoresearch_state.json"


def load_previous_state():
    """Load the previous run's state for delta detection."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return None


def save_state(state):
    """Save current run state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_sheriff_sales_live():
    """Fetch latest sheriff sales from CivilView."""
    print("  Fetching live sheriff sales from CivilView...")
    url = "https://salesweb.civilview.com/Sales/SalesSearch"
    params = {"CountyId": 17}  # Passaic County

    try:
        resp = requests.get(url, params=params, timeout=30,
                           headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            print(f"  Warning: CivilView returned {resp.status_code}")
            return None

        # Parse the HTML for sale entries
        from html.parser import HTMLParser
        import re

        text = resp.text
        # Extract Paterson addresses from the results
        paterson_entries = []
        # Look for table rows with Paterson addresses
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL
        )
        for row in rows:
            if 'Paterson' in row or 'PATERSON' in row:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                cells = [c.strip() for c in cells if c.strip()]
                if cells:
                    paterson_entries.append(cells)

        if paterson_entries:
            print(f"  Found {len(paterson_entries)} Paterson sheriff sales")
            return paterson_entries
    except Exception as e:
        print(f"  Warning: Could not fetch live sheriff sales: {e}")

    return None


def fetch_new_listings():
    """Check for new multifamily listings on Redfin."""
    print("  Checking for new listings...")
    try:
        url = (
            "https://www.redfin.com/stingray/api/gis-csv"
            "?al=1&market=newjersey&region_id=14170&region_type=6"
            "&status=9&num_homes=50&property_type=3"  # active, multifamily
        )
        resp = requests.get(url, timeout=30,
                           headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.content) > 100:
            # Parse CSV
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            print(f"  Found {len(df)} active listings")
            return df
    except Exception as e:
        print(f"  Warning: Could not fetch listings: {e}")
    return None


def detect_deltas(current_df, prev_state):
    """Compare current results to previous run and detect changes."""
    deltas = {
        "new_sheriff_sales": [],
        "new_high_score": [],
        "price_drops": [],
        "new_distress": [],
    }

    if prev_state is None:
        return deltas

    prev_addresses = set(prev_state.get("scored_addresses", []))
    prev_sheriff = set(prev_state.get("sheriff_addresses", []))
    prev_scores = prev_state.get("top_scores", {})

    for _, row in current_df.iterrows():
        addr = row["address"]

        # New sheriff sale
        if row.get("in_sheriff_sale") and addr not in prev_sheriff:
            deltas["new_sheriff_sales"].append(row.to_dict())

        # New high-score property (wasn't in top 100 before)
        if addr not in prev_scores and row["composite_score"] > 0.85:
            deltas["new_high_score"].append(row.to_dict())

        # Score increased significantly
        if addr in prev_scores:
            old_score = prev_scores[addr]
            if row["composite_score"] > old_score + 0.1:
                deltas["new_distress"].append(row.to_dict())

    return deltas


def generate_alerts(df, deltas):
    """Generate high-priority alerts."""
    alerts = []

    # Sheriff sale properties with good returns
    sheriff_deals = df[
        (df["in_sheriff_sale"]) & (df["equity_multiple"] > 1.3)
    ].sort_values("equity_multiple", ascending=False)
    for _, row in sheriff_deals.iterrows():
        alerts.append({
            "priority": "HIGH",
            "type": "sheriff_sale",
            "address": row["address"],
            "message": (
                f"Sheriff sale: {row['address']} ({row['zip']}) — "
                f"{row['est_units']} units, {row['equity_multiple']:.2f}x equity, "
                f"{row['cap_rate_current']:.1%} cap rate"
            ),
        })

    # Distressed absentee properties (motivated sellers)
    motivated = df[
        (df["absentee_status"].isin(["out_of_state"]))
        & (df["distress_score"] >= 4)
        & (df["equity_multiple"] > 2.0)
        & (df["est_units"] >= 2)
    ].sort_values("equity_multiple", ascending=False)
    for _, row in motivated.head(10).iterrows():
        alerts.append({
            "priority": "HIGH",
            "type": "motivated_seller",
            "address": row["address"],
            "message": (
                f"Motivated seller: {row['address']} — "
                f"out-of-state owner, {row['est_units']} units, "
                f"{row['equity_multiple']:.2f}x equity, "
                f"distress score {row['distress_score']}"
            ),
        })

    # High-value apartments
    big_apts = df[
        (df["prop_class"] == "4C")
        & (df["equity_multiple"] > 2.0)
    ].sort_values("equity_multiple", ascending=False)
    for _, row in big_apts.head(5).iterrows():
        alerts.append({
            "priority": "MEDIUM",
            "type": "apartment_opportunity",
            "address": row["address"],
            "message": (
                f"Apartment deal: {row['address']} — "
                f"{row['est_units']} units, ${row['est_market_value']:,.0f} market value, "
                f"{row['equity_multiple']:.2f}x equity"
            ),
        })

    # New delta alerts
    for item in deltas.get("new_sheriff_sales", []):
        alerts.append({
            "priority": "URGENT",
            "type": "new_sheriff_sale",
            "address": item["address"],
            "message": f"NEW sheriff sale: {item['address']} since last scan",
        })

    return sorted(alerts, key=lambda a: {"URGENT": 0, "HIGH": 1, "MEDIUM": 2}.get(a["priority"], 3))


def generate_report(df, alerts, deltas, prev_state):
    """Generate the auto-research report."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")

    lines = []
    lines.append("=" * 70)
    lines.append(f"  PATERSON NJ — AUTO-RESEARCH REPORT")
    lines.append(f"  {timestamp}")
    lines.append(f"  For Veloce Capital + Forte Investment Fund")
    lines.append("=" * 70)

    # ── Alerts ──
    if alerts:
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  ALERTS ({len(alerts)})")
        lines.append(f"{'─' * 70}")
        for a in alerts:
            icon = {"URGENT": "🚨", "HIGH": "⚡", "MEDIUM": "📋"}.get(a["priority"], "📋")
            lines.append(f"  {icon} [{a['priority']}] {a['message']}")

    # ── Market Snapshot ──
    lines.append(f"\n{'─' * 70}")
    lines.append("  MARKET SNAPSHOT")
    lines.append(f"{'─' * 70}")
    lines.append(f"  Properties analyzed: {len(df):,}")
    lines.append(f"  Median market value: ${df['est_market_value'].median():,.0f}")
    lines.append(f"  Median cap rate: {df['cap_rate_current'].median():.1%}")
    lines.append(f"  Equity multiple > 1.5x: {len(df[df['equity_multiple'] > 1.5]):,}")
    lines.append(f"  Sheriff sale matches: {df['in_sheriff_sale'].sum()}")
    lines.append(f"  High distress (3+): {len(df[df['distress_score'] >= 3]):,}")

    if prev_state:
        prev_count = prev_state.get("total_scored", 0)
        diff = len(df) - prev_count
        if diff != 0:
            lines.append(f"  Change from last run: {diff:+d} properties")

    # ── Top 10 Overall ──
    lines.append(f"\n{'─' * 70}")
    lines.append("  TOP 10 DEALS (composite score)")
    lines.append(f"{'─' * 70}")
    top = df.head(10)
    for i, (_, row) in enumerate(top.iterrows(), 1):
        sheriff = " [SHERIFF]" if row['in_sheriff_sale'] else ""
        absentee = f" [{row['absentee_status']}]" if row['absentee_status'] not in ('owner_occupied', 'unknown') else ""
        lines.append(
            f"  {i:2d}. {row['address']} ({row['zip']}){sheriff}{absentee}"
        )
        lines.append(
            f"      {row['prop_class_desc']} | {row['est_units']}u | "
            f"${row['est_market_value']:,.0f} | "
            f"{row['cap_rate_current']:.1%} cap | "
            f"{row['equity_multiple']:.2f}x equity | "
            f"score: {row['composite_score']:.3f}"
        )

    # ── Sheriff Sales ──
    sheriff_df = df[df["in_sheriff_sale"]].copy()
    if len(sheriff_df) > 0:
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  SHERIFF SALES ({len(sheriff_df)} matched)")
        lines.append(f"{'─' * 70}")
        for _, row in sheriff_df.iterrows():
            lines.append(
                f"  {row['address']} | {row['est_units']}u | "
                f"${row['est_market_value']:,.0f} | "
                f"{row['cap_rate_current']:.1%} cap | "
                f"{row['equity_multiple']:.2f}x"
            )

    # ── Top Multifamily 3+ ──
    mf = df[(df["prop_class"].isin(["2", "4C"])) & (df["est_units"] >= 3)].head(15)
    lines.append(f"\n{'─' * 70}")
    lines.append("  TOP 15 MULTIFAMILY (3+ units)")
    lines.append(f"{'─' * 70}")
    for i, (_, row) in enumerate(mf.iterrows(), 1):
        absentee = f" [{row['absentee_status']}]" if row['absentee_status'] not in ('owner_occupied', 'unknown') else ""
        yr = int(row['year_built']) if pd.notna(row['year_built']) and row['year_built'] else '?'
        lines.append(
            f"  {i:2d}. {row['address']} ({row['zip']}){absentee}"
        )
        lines.append(
            f"      {row['est_units']}u | Built: {yr} | "
            f"${row['est_market_value']:,.0f} mkt | "
            f"${row['gross_annual_rent']:,.0f}/yr rent | "
            f"{row['cap_rate_current']:.1%} cap | "
            f"{row['equity_multiple']:.2f}x | "
            f"distress: {row['distress_score']}"
        )

    # ── Motivated Sellers ──
    motivated = df[
        (df["absentee_status"].isin(["out_of_state", "absentee_nj"]))
        & (df["distress_score"] >= 3)
        & (df["est_units"] >= 2)
    ].head(10)
    if len(motivated) > 0:
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  MOTIVATED SELLERS — absentee + distressed ({len(motivated)})")
        lines.append(f"{'─' * 70}")
        for i, (_, row) in enumerate(motivated.iterrows(), 1):
            lines.append(
                f"  {i:2d}. {row['address']} ({row['zip']}) [{row['absentee_status']}]"
            )
            lines.append(
                f"      {row['est_units']}u | ${row['est_market_value']:,.0f} | "
                f"{row['equity_multiple']:.2f}x | "
                f"flags: {row['distress_flags']}"
            )

    # ── Apartments ──
    apts = df[df["prop_class"] == "4C"].head(10)
    if len(apts) > 0:
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  TOP APARTMENTS (5+ units)")
        lines.append(f"{'─' * 70}")
        for i, (_, row) in enumerate(apts.iterrows(), 1):
            lines.append(
                f"  {i:2d}. {row['address']} | {row['est_units']}u | "
                f"${row['est_market_value']:,.0f} | "
                f"{row['cap_rate_current']:.1%} cap | "
                f"{row['equity_multiple']:.2f}x"
            )

    lines.append(f"\n{'=' * 70}")
    lines.append(f"  Report generated: {timestamp}")
    lines.append(f"  Data: {len(df):,} properties | HUD FMR FY2026 | ZORI | ACS 2022")
    lines.append(f"  Full CSV: data/paterson_ranked_deals.csv")
    lines.append("=" * 70)

    report_text = "\n".join(lines)

    # Save report
    report_path = REPORT_DIR / f"paterson_{date_str}.txt"
    with open(report_path, "w") as f:
        f.write(report_text)

    # Also save latest
    latest_path = REPORT_DIR / "latest.txt"
    with open(latest_path, "w") as f:
        f.write(report_text)

    return report_text, report_path


def run_autoresearch(delta_only=False, alerts_only=False, force_refresh=False):
    """Main auto-research loop."""
    print("=" * 70)
    print("  PATERSON NJ — AUTO-RESEARCH ENGINE")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    # Load previous state
    prev_state = load_previous_state()
    if prev_state:
        print(f"\n  Previous run: {prev_state.get('timestamp', 'unknown')}")
        print(f"  Previous properties: {prev_state.get('total_scored', 0)}")

    # ── Step 1: Load all data ──
    print("\n[1/5] Loading data sources...")
    parcels = fetch_paterson_parcels(force=force_refresh)
    rent_by_zip = load_hud_safmr()
    zori_by_zip, zori_date = load_zillow_zori()
    census_tracts = load_census_acs()
    sheriff_addresses = load_sheriff_sales()

    # ── Step 2: Score ──
    print("\n[2/5] Scoring properties...")
    results = []
    for p in parcels:
        pc = (p.get("PROP_CLASS") or "").strip()
        if pc not in INVESTABLE_CLASSES:
            continue
        r = score_property(p, rent_by_zip, zori_by_zip, census_tracts,
                          sheriff_addresses)
        if r is not None and r["equity_multiple"] > 0:
            results.append(r)

    df = pd.DataFrame(results)
    print(f"  Scored: {len(df)} properties")

    # ── Step 3: Rank ──
    print("\n[3/5] Ranking...")
    df["pct_equity_multiple"] = percentile_rank(df["raw_equity_multiple"])
    df["pct_cap_spread"] = percentile_rank(df["raw_cap_spread"])
    df["pct_distress"] = percentile_rank(df["raw_distress"])
    df["pct_rent_price"] = percentile_rank(df["raw_rent_price"])
    df["composite_score"] = (
        0.30 * df["pct_equity_multiple"]
        + 0.25 * df["pct_cap_spread"]
        + 0.20 * df["pct_distress"]
        + 0.15 * df["pct_rent_price"]
        + 0.10 * df["pct_equity_multiple"]
    )
    df = df.sort_values("composite_score", ascending=False)

    # Save full CSV
    output_cols = [
        "address", "zip", "block_lot", "prop_class", "prop_class_desc",
        "year_built", "est_units", "sqft", "net_assessed", "est_market_value",
        "last_sale_price", "annual_tax", "absentee_status", "owner_location",
        "in_sheriff_sale", "est_rent_per_unit", "gross_annual_rent",
        "noi_current", "cap_rate_current", "rent_to_price",
        "est_reno_cost", "total_cost", "noi_stabilized", "exit_value",
        "equity_multiple", "cap_rate_spread", "distress_score",
        "distress_flags", "composite_score",
    ]
    df[output_cols].to_csv(DATA_DIR / "paterson_ranked_deals.csv", index=False)

    # ── Step 4: Detect changes ──
    print("\n[4/5] Detecting changes...")
    deltas = detect_deltas(df, prev_state)
    for k, v in deltas.items():
        if v:
            print(f"  {k}: {len(v)}")

    # ── Step 5: Generate alerts & report ──
    print("\n[5/5] Generating report...")
    alerts = generate_alerts(df, deltas)
    report_text, report_path = generate_report(df, alerts, deltas, prev_state)

    # Save state for next run
    now = datetime.now(timezone.utc)
    state = {
        "timestamp": now.isoformat(),
        "total_scored": len(df),
        "scored_addresses": df["address"].tolist()[:500],  # top 500 for delta
        "sheriff_addresses": df[df["in_sheriff_sale"]]["address"].tolist(),
        "top_scores": {
            row["address"]: row["composite_score"]
            for _, row in df.head(200).iterrows()
        },
        "metrics": {
            "median_market_value": float(df["est_market_value"].median()),
            "median_cap_rate": float(df["cap_rate_current"].median()),
            "sheriff_sales": int(df["in_sheriff_sale"].sum()),
            "high_distress": int(len(df[df["distress_score"] >= 3])),
        },
    }
    save_state(state)

    # Print report
    print("\n" + report_text)
    print(f"\n  Report saved: {report_path}")

    return df, alerts


if __name__ == "__main__":
    args = sys.argv[1:]
    run_autoresearch(
        delta_only="--delta-only" in args,
        alerts_only="--alerts" in args,
        force_refresh="--refresh" in args,
    )
