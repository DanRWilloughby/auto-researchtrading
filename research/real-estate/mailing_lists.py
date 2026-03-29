#!/usr/bin/env python3
"""
Direct Mail Targeting — Paterson, NJ
Generates mailing lists of motivated sellers for Veloce Capital's acquisition team.

Targets absentee and out-of-state owners with high distress scores —
the most likely to accept off-market offers.

Usage:
  python mailing_lists.py                        # Default: out-of-state + high distress
  python mailing_lists.py --all-absentee         # All absentee owners
  python mailing_lists.py --sheriff-neighbors    # Owners near sheriff sale properties
  python mailing_lists.py --oz-only              # Only Opportunity Zone properties
  python mailing_lists.py --min-units 3          # Minimum unit count
  python mailing_lists.py --min-distress 4       # Minimum distress score
  python mailing_lists.py --budget 2000000       # Max acquisition price
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
MAIL_DIR = Path(__file__).parent / "mailing_lists"
MAIL_DIR.mkdir(exist_ok=True)


def load_parcels():
    """Load raw parcel data with owner mailing addresses."""
    with open(DATA_DIR / "paterson_parcels.json") as f:
        return json.load(f)


def load_ranked_deals():
    """Load scored properties."""
    return pd.read_csv(DATA_DIR / "paterson_ranked_deals.csv")


def build_address_index(parcels):
    """Index parcels by property address for quick lookup."""
    idx = {}
    for p in parcels:
        addr = (p.get("PROP_LOC") or "").strip().upper()
        if addr:
            idx[addr] = p
    return idx


def classify_owner(parcel):
    """Classify owner type and extract mailing info."""
    prop_loc = (parcel.get("PROP_LOC") or "").strip()
    owner_name = (parcel.get("OWNER_NAME") or "").strip()
    st_addr = (parcel.get("ST_ADDRESS") or "").strip()
    city_state = (parcel.get("CITY_STATE") or "").strip()
    zip_code = (parcel.get("ZIP_CODE") or "").strip()

    # Build mailing address
    mailing_addr = f"{st_addr}, {city_state} {zip_code}".strip()
    if mailing_addr == ", ":
        mailing_addr = ""

    # Determine owner type
    cs_upper = city_state.upper()
    if "PATERSON" not in cs_upper and cs_upper:
        if "NJ" not in cs_upper and "NEW JERSEY" not in cs_upper:
            return "out_of_state", owner_name, mailing_addr
        return "absentee_nj", owner_name, mailing_addr
    if prop_loc.upper() != st_addr.upper() and st_addr:
        return "absentee_local", owner_name, mailing_addr
    return "owner_occupied", owner_name, mailing_addr


def generate_mailing_list(
    min_distress=3,
    owner_types=("out_of_state", "absentee_nj"),
    min_units=2,
    max_price=None,
    oz_only=False,
    prop_classes=("2", "4C", "4A"),
    limit=500,
    label="targeted",
):
    """Generate a mailing list of motivated sellers."""
    print(f"\nGenerating mailing list: {label}")
    print(f"  Filters: distress>={min_distress}, owners={owner_types}, units>={min_units}")

    parcels = load_parcels()
    parcel_idx = build_address_index(parcels)
    df = load_ranked_deals()

    # Apply filters
    mask = (
        (df["distress_score"] >= min_distress)
        & (df["est_units"] >= min_units)
        & (df["prop_class"].isin(prop_classes))
        & (df["equity_multiple"] > 1.0)
    )

    if owner_types:
        mask &= df["absentee_status"].isin(owner_types)

    if max_price:
        mask &= df["est_market_value"] <= max_price

    if oz_only:
        mask &= df.get("in_opportunity_zone", False) == True

    filtered = df[mask].sort_values("composite_score", ascending=False).head(limit)
    print(f"  Matched: {len(filtered)} properties")

    if filtered.empty:
        print("  No matches found")
        return None

    # Enrich with owner mailing addresses from parcel data
    records = []
    for _, row in filtered.iterrows():
        addr = row["address"]
        parcel = parcel_idx.get(addr, {})
        owner_type, owner_name, mailing_addr = classify_owner(parcel)

        records.append({
            # Property info
            "property_address": addr,
            "property_zip": row["zip"],
            "block_lot": row["block_lot"],
            "prop_class": row["prop_class_desc"],
            "units": row["est_units"],
            "year_built": row.get("year_built", ""),
            "est_market_value": row["est_market_value"],
            # Owner mailing info
            "owner_name": owner_name,
            "owner_mailing_address": mailing_addr,
            "owner_type": owner_type,
            # Investment metrics
            "cap_rate": round(row["cap_rate_current"], 3),
            "equity_multiple": row["equity_multiple"],
            "noi_current": row["noi_current"],
            "noi_stabilized": row["noi_stabilized"],
            "distress_score": row["distress_score"],
            "distress_flags": row.get("distress_flags", ""),
            "composite_score": round(row["composite_score"], 4),
            # Flags
            "sheriff_sale": row.get("in_sheriff_sale", False),
            "opportunity_zone": row.get("in_opportunity_zone", False),
            "flood_zone": row.get("in_flood_zone", False),
        })

    result_df = pd.DataFrame(records)

    # Save CSV (for mail merge)
    csv_path = MAIL_DIR / f"mailing_list_{label}.csv"
    result_df.to_csv(csv_path, index=False)

    # Save summary
    summary_path = MAIL_DIR / f"mailing_list_{label}_summary.txt"
    with open(summary_path, "w") as f:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        f.write(f"MAILING LIST: {label}\n")
        f.write(f"Generated: {now}\n")
        f.write(f"{'=' * 60}\n\n")

        f.write(f"Total addresses: {len(result_df)}\n")
        f.write(f"Owner breakdown:\n")
        for ot, count in result_df["owner_type"].value_counts().items():
            f.write(f"  {ot}: {count}\n")

        f.write(f"\nProperty class breakdown:\n")
        for pc, count in result_df["prop_class"].value_counts().items():
            f.write(f"  {pc}: {count}\n")

        f.write(f"\nZIP code breakdown:\n")
        for z, count in result_df["property_zip"].value_counts().head(10).items():
            f.write(f"  {z}: {count}\n")

        f.write(f"\nMetrics:\n")
        f.write(f"  Median market value: ${result_df['est_market_value'].median():,.0f}\n")
        f.write(f"  Median cap rate: {result_df['cap_rate'].median():.1%}\n")
        f.write(f"  Median equity multiple: {result_df['equity_multiple'].median():.2f}x\n")
        f.write(f"  Median distress score: {result_df['distress_score'].median():.0f}\n")

        f.write(f"\nWith mailing addresses: {len(result_df[result_df['owner_mailing_address'] != ''])}\n")
        f.write(f"Sheriff sale properties: {result_df['sheriff_sale'].sum()}\n")
        f.write(f"Opportunity Zone: {result_df['opportunity_zone'].sum()}\n")

        # Top 20 preview
        f.write(f"\n{'─' * 60}\n")
        f.write(f"TOP 20 TARGETS\n")
        f.write(f"{'─' * 60}\n")
        for i, (_, r) in enumerate(result_df.head(20).iterrows(), 1):
            sheriff = " [SHERIFF]" if r["sheriff_sale"] else ""
            oz = " [OZ]" if r["opportunity_zone"] else ""
            f.write(f"\n  {i:2d}. {r['property_address']} ({r['property_zip']}){sheriff}{oz}\n")
            f.write(f"      {r['prop_class']} | {r['units']} units | ${r['est_market_value']:,.0f}\n")
            f.write(f"      Owner: {r['owner_name'] or 'N/A'}\n")
            f.write(f"      Mail to: {r['owner_mailing_address'] or 'N/A'}\n")
            f.write(f"      Cap: {r['cap_rate']:.1%} | Equity: {r['equity_multiple']:.2f}x | Distress: {r['distress_score']}\n")

    print(f"  Saved: {csv_path}")
    print(f"  Summary: {summary_path}")
    return result_df


def generate_letter_template():
    """Generate a direct mail letter template for Veloce Capital."""
    template = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  DIRECT MAIL LETTER TEMPLATE — Veloce Capital
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [OWNER NAME]
  [OWNER MAILING ADDRESS]

  RE: Your property at [PROPERTY ADDRESS], Paterson, NJ

  Dear [OWNER NAME],

  My name is [AGENT NAME] and I work with Veloce Capital,
  a real estate investment firm that specializes in acquiring
  and improving properties in Paterson, New Jersey.

  I'm writing because we are actively looking to purchase
  [UNIT COUNT]-unit properties like yours at [PROPERTY ADDRESS].
  We can offer:

    • A fair cash offer — no financing contingencies
    • Quick closing — as fast as 2 weeks
    • As-is purchase — no repairs needed on your end
    • We cover all closing costs

  We understand that managing property from [OWNER CITY/STATE]
  can be challenging, and we'd like to make the process as
  simple as possible for you.

  If you have any interest in discussing a sale, please
  contact me at:

    Phone: [PHONE]
    Email: [EMAIL]
    Web: velocecapital.com

  There is no obligation and the conversation is completely
  confidential.

  Sincerely,
  [AGENT NAME]
  Veloce Capital

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MERGE FIELDS (from mailing_list CSV):
    [OWNER NAME]           → owner_name
    [OWNER MAILING ADDRESS]→ owner_mailing_address
    [PROPERTY ADDRESS]     → property_address
    [UNIT COUNT]           → units
    [OWNER CITY/STATE]     → owner_type + owner_mailing_address
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    path = MAIL_DIR / "letter_template.txt"
    with open(path, "w") as f:
        f.write(template)
    print(f"  Letter template: {path}")


def main():
    args = sys.argv[1:]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Parse options
    min_distress = 3
    min_units = 2
    max_price = None
    oz_only = False

    if "--min-distress" in args:
        min_distress = int(args[args.index("--min-distress") + 1])
    if "--min-units" in args:
        min_units = int(args[args.index("--min-units") + 1])
    if "--budget" in args:
        max_price = int(args[args.index("--budget") + 1])
    if "--oz-only" in args:
        oz_only = True

    print("=" * 60)
    print("  DIRECT MAIL TARGETING — Paterson, NJ")
    print(f"  Veloce Capital Acquisition Pipeline")
    print(f"  {now}")
    print("=" * 60)

    # List 1: Out-of-state owners with high distress (highest priority)
    generate_mailing_list(
        min_distress=max(min_distress, 3),
        owner_types=("out_of_state",),
        min_units=min_units,
        max_price=max_price,
        oz_only=oz_only,
        label="tier1_out_of_state_distressed",
        limit=200,
    )

    # List 2: Absentee NJ owners with high distress
    generate_mailing_list(
        min_distress=max(min_distress, 3),
        owner_types=("absentee_nj",),
        min_units=min_units,
        max_price=max_price,
        oz_only=oz_only,
        label="tier2_absentee_nj_distressed",
        limit=300,
    )

    if "--all-absentee" in args:
        # List 3: All absentee owners (broader campaign)
        generate_mailing_list(
            min_distress=1,
            owner_types=("out_of_state", "absentee_nj", "absentee_local"),
            min_units=min_units,
            max_price=max_price,
            oz_only=oz_only,
            label="tier3_all_absentee",
            limit=500,
        )

    if "--sheriff-neighbors" in args:
        # List 4: Owners of properties near sheriff sales
        # These owners see foreclosure activity and may want to sell before it affects them
        generate_mailing_list(
            min_distress=2,
            owner_types=("out_of_state", "absentee_nj", "absentee_local"),
            min_units=1,
            max_price=max_price,
            prop_classes=("2", "4C", "4A", "4B"),
            label="tier4_sheriff_area_owners",
            limit=200,
        )

    # Generate letter template
    generate_letter_template()

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  All mailing lists saved to: {MAIL_DIR}")
    print(f"  CSV files are ready for mail merge services")
    print(f"  (e.g., Lob, PostGrid, or print shop)")
    print("=" * 60)


if __name__ == "__main__":
    main()
