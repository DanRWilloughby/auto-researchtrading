#!/usr/bin/env python3
"""
Property Underwriting Sheet Generator — Paterson, NJ
Generates investor-ready underwriting summaries for Forte Investment Fund.

Usage:
  python underwriting.py                     # Generate for top 20 deals
  python underwriting.py --address "123 MAIN ST"  # Specific property
  python underwriting.py --sheriff           # Sheriff sale properties only
  python underwriting.py --top 50            # Top N deals
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
SHEETS_DIR = Path(__file__).parent / "underwriting_sheets"
SHEETS_DIR.mkdir(exist_ok=True)


def load_ranked_deals():
    """Load the scored property database."""
    path = DATA_DIR / "paterson_ranked_deals.csv"
    if not path.exists():
        print("Error: Run pipeline.py first to generate ranked deals")
        sys.exit(1)
    return pd.read_csv(path)


def load_sheriff_details():
    """Load detailed sheriff sale info."""
    path = DATA_DIR / "passaic_sheriff_sales_paterson.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    props = data.get("paterson_properties", []) if isinstance(data, dict) else data
    # Index by normalized address
    details = {}
    for p in props:
        addr = (p.get("address") or "").upper().strip()
        addr = addr.replace(",", "").replace(".", "")
        if "PATERSON" in addr:
            addr = addr[:addr.index("PATERSON")].strip()
        details[addr] = p
    return details


def generate_sheet(row, sheriff_details=None):
    """Generate a text-based underwriting sheet for a single property."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    addr = row["address"]

    # Check for sheriff sale details
    sheriff_info = None
    if sheriff_details:
        addr_norm = addr.replace(",", "").replace(".", "").strip()
        for key, val in sheriff_details.items():
            if addr_norm in key or key in addr_norm:
                sheriff_info = val
                break

    lines = []
    lines.append("╔" + "═" * 68 + "╗")
    lines.append("║  PROPERTY UNDERWRITING SHEET" + " " * 39 + "║")
    lines.append("║  Veloce Capital + Forte Investment Fund" + " " * 27 + "║")
    lines.append("╚" + "═" * 68 + "╝")

    lines.append(f"\n  Date: {now}")
    lines.append(f"  Property: {addr}")
    lines.append(f"  ZIP: {row['zip']} | Block/Lot: {row['block_lot']}")

    # Property Overview
    lines.append(f"\n{'─' * 70}")
    lines.append("  PROPERTY OVERVIEW")
    lines.append(f"{'─' * 70}")
    yr = int(row['year_built']) if pd.notna(row.get('year_built')) and row.get('year_built') else 'Unknown'
    lines.append(f"  Type: {row['prop_class_desc']}")
    lines.append(f"  Units: {row['est_units']}")
    lines.append(f"  Est. Sqft: {row['sqft']:,.0f}")
    lines.append(f"  Year Built: {yr}")

    # Status flags
    flags = []
    if row.get('in_sheriff_sale'):
        flags.append("SHERIFF SALE")
    if row.get('in_opportunity_zone'):
        flags.append("OPPORTUNITY ZONE")
    if row.get('in_flood_zone'):
        flags.append("FLOOD RISK AREA")
    if row.get('absentee_status') not in ('owner_occupied', 'unknown', None):
        flags.append(f"ABSENTEE ({row['absentee_status'].upper()})")
    if flags:
        lines.append(f"  Status: {' | '.join(flags)}")

    # Valuation
    lines.append(f"\n{'─' * 70}")
    lines.append("  VALUATION & PRICING")
    lines.append(f"{'─' * 70}")
    lines.append(f"  Net Assessed Value:     ${row['net_assessed']:>12,.0f}")
    lines.append(f"  Est. Market Value:      ${row['est_market_value']:>12,.0f}")
    if pd.notna(row.get('last_sale_price')) and row.get('last_sale_price'):
        lines.append(f"  Last Sale Price:        ${row['last_sale_price']:>12,.0f}")
    lines.append(f"  Annual Property Tax:    ${row['annual_tax']:>12,.0f}")

    # Sheriff sale details
    if sheriff_info:
        lines.append(f"\n  SHERIFF SALE DETAILS:")
        if sheriff_info.get('sale_date'):
            lines.append(f"    Sale Date: {sheriff_info['sale_date']}")
        if sheriff_info.get('approx_upset'):
            lines.append(f"    Upset Price: ${sheriff_info['approx_upset']:,.2f}")
        if sheriff_info.get('plaintiff'):
            lines.append(f"    Plaintiff: {sheriff_info['plaintiff']}")
        if sheriff_info.get('lot_dimensions'):
            lines.append(f"    Lot: {sheriff_info['lot_dimensions']}")
        if sheriff_info.get('notes'):
            lines.append(f"    Notes: {sheriff_info['notes'][:120]}")

    # Income Analysis
    lines.append(f"\n{'─' * 70}")
    lines.append("  INCOME ANALYSIS")
    lines.append(f"{'─' * 70}")
    lines.append(f"  {'':30s} {'As-Is':>15s}   {'Stabilized':>15s}")
    lines.append(f"  {'─' * 30} {'─' * 15}   {'─' * 15}")
    lines.append(f"  {'Rent per Unit (monthly)':30s} ${row['est_rent_per_unit']:>14,.0f}   ${row['est_rent_per_unit'] * 1.2:>14,.0f}")
    lines.append(f"  {'Gross Annual Rent':30s} ${row['gross_annual_rent']:>14,.0f}   ${row['gross_annual_rent'] * 1.2:>14,.0f}")
    lines.append(f"  {'Net Operating Income':30s} ${row['noi_current']:>14,.0f}   ${row['noi_stabilized']:>14,.0f}")
    lines.append(f"  {'Cap Rate':30s} {row['cap_rate_current']:>14.1%}   {(row['cap_rate_spread'] + row['cap_rate_current']):>14.1%}")

    # Investment Returns
    lines.append(f"\n{'─' * 70}")
    lines.append("  INVESTMENT RETURNS")
    lines.append(f"{'─' * 70}")
    lines.append(f"  Acquisition Cost:       ${row['est_market_value']:>12,.0f}")
    lines.append(f"  Renovation Cost:        ${row['est_reno_cost']:>12,.0f}  ({row['sqft']:,.0f} sqft × $55/sqft)")
    lines.append(f"  Total Investment:       ${row['total_cost']:>12,.0f}")
    lines.append(f"  Est. Exit Value:        ${row['exit_value']:>12,.0f}")
    lines.append(f"  Equity Multiple:        {row['equity_multiple']:>12.2f}x")
    lines.append(f"  Rent-to-Price Ratio:    {row['rent_to_price']:>12.4f}  ({row['rent_to_price']*100:.2f}%)")
    lines.append(f"  Cash-on-Cash Yield:     {row['noi_stabilized']/row['total_cost']:>12.1%}")

    # Opportunity Zone benefit
    if row.get('in_opportunity_zone'):
        lines.append(f"\n  OPPORTUNITY ZONE BENEFITS:")
        lines.append(f"    - Capital gains tax deferral on invested gains")
        lines.append(f"    - 10% step-up in basis after 5 years")
        lines.append(f"    - Tax-free appreciation after 10-year hold")

    # Risk Assessment
    lines.append(f"\n{'─' * 70}")
    lines.append("  RISK ASSESSMENT")
    lines.append(f"{'─' * 70}")
    lines.append(f"  Distress Score:         {row['distress_score']}/10")
    if row.get('distress_flags'):
        for flag in str(row['distress_flags']).split(','):
            if flag:
                desc = {
                    'sheriff_sale': 'Property in foreclosure — potential below-market acquisition',
                    'low_improvement_ratio': 'Low improvement-to-land ratio — may need significant renovation',
                    'pre_1960': 'Pre-1960 construction — check for lead paint, asbestos',
                    'sold_below_assessed': 'Prior sale below assessed — distressed transaction history',
                    'zero_tax': 'Zero property tax — verify tax status',
                    'out_of_state_owner': 'Out-of-state owner — potentially motivated seller',
                    'absentee': 'Absentee owner — may be open to off-market offer',
                    'flood_risk_area': 'Near Passaic River flood zone — verify FEMA designation, budget for flood insurance',
                }.get(flag.strip(), flag.strip())
                lines.append(f"  • {desc}")

    # Composite Score
    lines.append(f"\n{'─' * 70}")
    lines.append("  COMPOSITE SCORE")
    lines.append(f"{'─' * 70}")
    score = row['composite_score']
    bar_len = int(score * 40)
    bar = "█" * bar_len + "░" * (40 - bar_len)
    lines.append(f"  [{bar}] {score:.3f}")
    if score > 0.9:
        lines.append(f"  Rating: STRONG BUY — Top decile opportunity")
    elif score > 0.75:
        lines.append(f"  Rating: BUY — Above-average opportunity")
    elif score > 0.5:
        lines.append(f"  Rating: HOLD — Average opportunity, review details")
    else:
        lines.append(f"  Rating: PASS — Below-average risk-adjusted return")

    lines.append(f"\n{'─' * 70}")
    lines.append(f"  Generated: {now} | Source: Paterson Deal Intelligence Pipeline v2")
    lines.append(f"  Data: NJ MOD-IV | HUD FMR FY2026 | Zillow ZORI | Census ACS 2022")
    lines.append(f"  This analysis is for informational purposes only. Verify all data")
    lines.append(f"  independently before making investment decisions.")
    lines.append("─" * 70)

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    df = load_ranked_deals()
    sheriff_details = load_sheriff_details()

    if "--address" in args:
        idx = args.index("--address")
        addr = args[idx + 1].upper()
        matches = df[df["address"].str.contains(addr, na=False)]
        if matches.empty:
            print(f"No property found matching '{addr}'")
            return
        for _, row in matches.iterrows():
            sheet = generate_sheet(row, sheriff_details)
            print(sheet)
            filename = row["address"].replace(" ", "_").replace("/", "-")[:40]
            path = SHEETS_DIR / f"{filename}.txt"
            with open(path, "w") as f:
                f.write(sheet)
            print(f"\n  Saved: {path}")
        return

    # Filter mode
    if "--sheriff" in args:
        subset = df[df["in_sheriff_sale"] == True]
        label = "sheriff sale"
    elif "--oz" in args:
        subset = df[df.get("in_opportunity_zone", False) == True]
        label = "Opportunity Zone"
    else:
        n = 20
        if "--top" in args:
            idx = args.index("--top")
            n = int(args[idx + 1])
        subset = df.head(n)
        label = f"top {n}"

    print(f"Generating underwriting sheets for {len(subset)} {label} properties...\n")

    for i, (_, row) in enumerate(subset.iterrows(), 1):
        sheet = generate_sheet(row, sheriff_details)
        filename = row["address"].replace(" ", "_").replace("/", "-")[:40]
        path = SHEETS_DIR / f"{filename}.txt"
        with open(path, "w") as f:
            f.write(sheet)
        print(f"  [{i}/{len(subset)}] {row['address']} → {path.name}")

    # Also generate a combined file
    combined_path = SHEETS_DIR / f"combined_{label.replace(' ', '_')}.txt"
    with open(combined_path, "w") as f:
        for _, row in subset.iterrows():
            f.write(generate_sheet(row, sheriff_details))
            f.write("\n\n\n")
    print(f"\n  Combined file: {combined_path}")
    print(f"  Total: {len(subset)} underwriting sheets generated")


if __name__ == "__main__":
    main()
