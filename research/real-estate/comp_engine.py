#!/usr/bin/env python3
"""
Comparable Sales Analysis Engine — Paterson, NJ
For Veloce Capital + Forte Investment Fund

Finds the most comparable recent sales for any target property using a
weighted distance scoring model. Supports single-property lookup, batch
analysis of top-ranked deals, and sheriff sale comps.

Usage:
  python comp_engine.py --address "307 VAN HOUTEN ST"   # Single property
  python comp_engine.py --top 20                         # Top 20 ranked deals
  python comp_engine.py --sheriff                        # Sheriff sale properties
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

# ─── Date Parsing ───────────────────────────────────────────────────────────

def parse_deed_date(raw: str) -> datetime | None:
    """Parse DEED_DATE from YYMMDD format (e.g. '211014' -> 2021-10-14)."""
    if not raw or not isinstance(raw, str) or len(raw) != 6:
        return None
    try:
        yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
        year = 2000 + yy if yy < 80 else 1900 + yy
        return datetime(year, mm, dd)
    except (ValueError, TypeError):
        return None


# ─── Data Loading ───────────────────────────────────────────────────────────

def load_parcels() -> list[dict]:
    """Load all Paterson parcels from JSON cache."""
    path = DATA_DIR / "paterson_parcels.json"
    if not path.exists():
        print(f"ERROR: Parcel data not found at {path}")
        print("Run pipeline.py first to fetch parcel data.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def load_ranked_deals() -> pd.DataFrame:
    """Load scored/ranked deals from CSV."""
    path = DATA_DIR / "paterson_ranked_deals.csv"
    if not path.exists():
        print(f"ERROR: Ranked deals not found at {path}")
        print("Run pipeline.py first to generate ranked deals.")
        sys.exit(1)
    return pd.read_csv(path)


# ─── Comp Pool Preparation ─────────────────────────────────────────────────

def build_comp_pool(parcels: list[dict], cutoff_years: int = 3) -> list[dict]:
    """
    Filter parcels to valid recent sales for use as comps.

    Criteria:
    - SALE_PRICE > $50,000
    - DEED_DATE within the last `cutoff_years` years
    - Valid property class
    """
    now = datetime.now()
    cutoff_date = now - timedelta(days=cutoff_years * 365)
    pool = []

    for p in parcels:
        price = p.get("SALE_PRICE")
        if not price or price <= 50000:
            continue

        deed_dt = parse_deed_date(p.get("DEED_DATE", ""))
        if not deed_dt or deed_dt < cutoff_date or deed_dt > now:
            continue

        # Must have a property class
        if not p.get("PROP_CLASS"):
            continue

        pool.append({
            "address": p.get("PROP_LOC", "UNKNOWN"),
            "block": p.get("PCLBLOCK", ""),
            "lot": p.get("PCLLOT", ""),
            "prop_class": p.get("PROP_CLASS", ""),
            "sale_price": price,
            "deed_date": deed_dt,
            "deed_date_raw": p.get("DEED_DATE", ""),
            "units": p.get("DWELL") or 1,
            "net_value": p.get("NET_VALUE", 0),
            "yr_constr": p.get("YR_CONSTR"),
            "area": p.get("Shape__Area", 0),
            "imprvt_val": p.get("IMPRVT_VAL", 0),
            "land_val": p.get("LAND_VAL", 0),
        })

    return pool


# ─── Comp Scoring ───────────────────────────────────────────────────────────

WEIGHTS = {
    "units": 0.30,
    "value": 0.30,
    "year_built": 0.15,
    "lot_size": 0.15,
    "recency": 0.10,
}


def score_comp(target: dict, comp: dict) -> float | None:
    """
    Score a comp against the target property. Lower = more comparable.
    Returns None if the comp should be excluded (different prop class).
    """
    # Hard filter: same property class required
    if target["prop_class"] != comp["prop_class"]:
        return None

    # Unit count difference
    t_units = max(target.get("units") or 1, 1)
    c_units = max(comp.get("units") or 1, 1)
    unit_diff = abs(t_units - c_units) / max(t_units, 1)

    # Assessed value difference
    t_val = max(target.get("net_value", 0), 1)
    c_val = max(comp.get("net_value", 0), 1)
    val_diff = abs(t_val - c_val) / max(t_val, 1)

    # Year built difference (if available)
    t_yr = target.get("yr_constr")
    c_yr = comp.get("yr_constr")
    if t_yr and c_yr:
        try:
            yr_diff = abs(float(t_yr) - float(c_yr)) / 100
        except (ValueError, TypeError):
            yr_diff = 0.5  # neutral penalty when unknown
    else:
        yr_diff = 0.0  # no penalty when data missing

    # Lot size difference
    t_area = max(target.get("area", 0), 1)
    c_area = max(comp.get("area", 0), 1)
    area_diff = abs(t_area - c_area) / max(t_area, 1)

    # Recency bonus: days since sale / max days (3 years = 1095)
    days_ago = (datetime.now() - comp["deed_date"]).days
    recency = min(days_ago / 1095, 1.0)  # 0 = today, 1 = 3 years ago

    # Weighted distance score
    score = (
        WEIGHTS["units"] * unit_diff
        + WEIGHTS["value"] * val_diff
        + WEIGHTS["year_built"] * yr_diff
        + WEIGHTS["lot_size"] * area_diff
        + WEIGHTS["recency"] * recency
    )

    # Block proximity bonus: same block gets a small bonus
    if target.get("block") and comp.get("block"):
        if target["block"] == comp["block"]:
            score *= 0.85  # 15% bonus for same block

    return score


# ─── Public API ─────────────────────────────────────────────────────────────

def get_comps(address: str, parcels: list[dict], n: int = 10) -> dict:
    """
    Find the N most comparable recent sales for a property at the given address.

    Args:
        address: Property address to match (e.g. "307 VAN HOUTEN ST")
        parcels: Full parcel list from load_parcels()
        n: Number of comps to return (default 10)

    Returns:
        dict with keys:
            target: target property summary dict
            comps: list of comp dicts sorted by distance score
            valuation: dict with min, median, max implied values
            confidence: str ("high", "medium", "low")
    """
    address_upper = address.strip().upper()

    # Find target property
    target_parcel = None
    for p in parcels:
        if p.get("PROP_LOC", "").strip().upper() == address_upper:
            target_parcel = p
            break

    if not target_parcel:
        # Try partial match
        for p in parcels:
            loc = p.get("PROP_LOC", "").strip().upper()
            if address_upper in loc or loc in address_upper:
                target_parcel = p
                break

    if not target_parcel:
        return {"error": f"Property not found: {address}"}

    target = {
        "address": target_parcel.get("PROP_LOC", "UNKNOWN"),
        "block": target_parcel.get("PCLBLOCK", ""),
        "lot": target_parcel.get("PCLLOT", ""),
        "prop_class": target_parcel.get("PROP_CLASS", ""),
        "sale_price": target_parcel.get("SALE_PRICE", 0),
        "deed_date": parse_deed_date(target_parcel.get("DEED_DATE", "")),
        "units": target_parcel.get("DWELL") or 1,
        "net_value": target_parcel.get("NET_VALUE", 0),
        "yr_constr": target_parcel.get("YR_CONSTR"),
        "area": target_parcel.get("Shape__Area", 0),
        "imprvt_val": target_parcel.get("IMPRVT_VAL", 0),
        "land_val": target_parcel.get("LAND_VAL", 0),
    }

    # Build comp pool and score
    comp_pool = build_comp_pool(parcels)
    scored = []
    for comp in comp_pool:
        # Skip if same property
        if comp["address"] == target["address"] and comp["block"] == target["block"]:
            continue
        dist = score_comp(target, comp)
        if dist is not None:
            comp["distance_score"] = dist
            scored.append(comp)

    # Sort by distance score (lower = better)
    scored.sort(key=lambda x: x["distance_score"])
    top_comps = scored[:n]

    # Compute comp-implied valuation
    valuation = compute_valuation(target, top_comps)
    confidence = assess_confidence(top_comps)

    return {
        "target": target,
        "comps": top_comps,
        "valuation": valuation,
        "confidence": confidence,
    }


def compute_valuation(target: dict, comps: list[dict]) -> dict:
    """
    Derive an implied value range from comps, adjusting for unit/size differences.
    """
    if not comps:
        return {"min": 0, "median": 0, "max": 0, "avg_per_unit": 0}

    t_units = max(target.get("units") or 1, 1)
    t_area = max(target.get("area", 0), 1)

    # Per-unit and per-sqft values from comps
    per_unit_prices = []
    per_area_prices = []
    raw_prices = []

    for c in comps:
        c_units = max(c.get("units") or 1, 1)
        c_area = max(c.get("area", 0), 1)
        per_unit_prices.append(c["sale_price"] / c_units)
        per_area_prices.append(c["sale_price"] / c_area)
        raw_prices.append(c["sale_price"])

    # Primary method: per-unit adjusted values
    adj_prices = [ppu * t_units for ppu in per_unit_prices]
    adj_prices.sort()

    return {
        "min": int(adj_prices[0]),
        "median": int(adj_prices[len(adj_prices) // 2]),
        "max": int(adj_prices[-1]),
        "avg_per_unit": int(sum(per_unit_prices) / len(per_unit_prices)),
        "raw_min": int(min(raw_prices)),
        "raw_median": int(sorted(raw_prices)[len(raw_prices) // 2]),
        "raw_max": int(max(raw_prices)),
    }


def assess_confidence(comps: list[dict]) -> str:
    """Rate confidence based on comp quality and quantity."""
    if len(comps) < 3:
        return "low"

    avg_score = sum(c["distance_score"] for c in comps[:5]) / min(len(comps), 5)

    if avg_score < 0.15 and len(comps) >= 5:
        return "high"
    elif avg_score < 0.30 and len(comps) >= 3:
        return "medium"
    else:
        return "low"


# ─── Display ────────────────────────────────────────────────────────────────

PROP_CLASS_NAMES = {
    "1": "Vacant Land",
    "2": "Residential (1-4 family)",
    "4A": "Commercial",
    "4B": "Industrial",
    "4C": "Apartment (5+ units)",
    "5A": "Railroad",
    "15A": "Public School",
    "15B": "Other School",
    "15C": "Public Property",
    "15D": "Church/Charity",
    "15E": "Cemetery",
    "15F": "Other Exempt",
}


def format_currency(val: int | float) -> str:
    """Format as currency string."""
    if val >= 1_000_000:
        return f"${val:,.0f}"
    return f"${val:,.0f}"


def display_results(result: dict) -> None:
    """Pretty-print comp analysis results to stdout."""
    if "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        return

    target = result["target"]
    comps = result["comps"]
    valuation = result["valuation"]
    confidence = result["confidence"]

    # Header
    print("\n" + "=" * 80)
    print("  COMPARABLE SALES ANALYSIS")
    print("=" * 80)

    # Target summary
    prop_class_name = PROP_CLASS_NAMES.get(target["prop_class"], target["prop_class"])
    print(f"\n  Target: {target['address']}")
    print(f"  Block/Lot: {target['block']}/{target['lot']}")
    print(f"  Class: {target['prop_class']} — {prop_class_name}")
    print(f"  Units: {target['units']}   Assessed: {format_currency(target['net_value'])}")
    print(f"  Year Built: {target.get('yr_constr') or 'N/A'}   Lot: {target['area']:.0f} sqft")
    if target.get("sale_price") and target["sale_price"] > 0:
        sale_dt = target["deed_date"].strftime("%Y-%m-%d") if target["deed_date"] else "N/A"
        print(f"  Last Sale: {format_currency(target['sale_price'])} ({sale_dt})")

    # Comp table
    print(f"\n  {'─' * 76}")
    print(f"  {'#':>2}  {'Address':<28} {'Sale Price':>11} {'Date':>10} {'Units':>5}"
          f" {'Assessed':>10} {'$/Unit':>9} {'Score':>6}")
    print(f"  {'─' * 76}")

    for i, c in enumerate(comps, 1):
        sale_dt = c["deed_date"].strftime("%Y-%m-%d") if c.get("deed_date") else "N/A"
        c_units = max(c.get("units") or 1, 1)
        per_unit = c["sale_price"] / c_units
        addr = c["address"][:28]
        print(f"  {i:>2}  {addr:<28} {format_currency(c['sale_price']):>11} {sale_dt:>10}"
              f" {c_units:>5} {format_currency(c['net_value']):>10}"
              f" {format_currency(per_unit):>9} {c['distance_score']:>6.3f}")

    print(f"  {'─' * 76}")

    # Valuation summary
    conf_label = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}[confidence]
    print(f"\n  COMP-IMPLIED VALUATION (unit-adjusted)")
    print(f"  Min: {format_currency(valuation['min']):>12}   "
          f"Median: {format_currency(valuation['median']):>12}   "
          f"Max: {format_currency(valuation['max']):>12}")
    print(f"  Avg $/Unit: {format_currency(valuation['avg_per_unit'])}")
    print(f"\n  Raw Comp Prices (unadjusted)")
    print(f"  Min: {format_currency(valuation['raw_min']):>12}   "
          f"Median: {format_currency(valuation['raw_median']):>12}   "
          f"Max: {format_currency(valuation['raw_max']):>12}")
    print(f"\n  Confidence: {conf_label} ({len(comps)} comps found)")
    print("=" * 80 + "\n")


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Comparable Sales Analysis Engine — Paterson, NJ"
    )
    parser.add_argument(
        "--address", type=str,
        help="Single property address to analyze (e.g. '307 VAN HOUTEN ST')"
    )
    parser.add_argument(
        "--top", type=int, default=0,
        help="Analyze top N ranked deals from paterson_ranked_deals.csv"
    )
    parser.add_argument(
        "--sheriff", action="store_true",
        help="Analyze all sheriff sale properties"
    )
    parser.add_argument(
        "--comps", type=int, default=10,
        help="Number of comps to return per property (default: 10)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON instead of formatted tables"
    )
    args = parser.parse_args()

    if not args.address and not args.top and not args.sheriff:
        parser.print_help()
        sys.exit(1)

    # Load data
    print("Loading parcel data...")
    parcels = load_parcels()
    print(f"  {len(parcels):,} parcels loaded")

    # Single address mode
    if args.address:
        result = get_comps(args.address, parcels, n=args.comps)
        if args.json:
            print(json.dumps(serialize_result(result), indent=2))
        else:
            display_results(result)
        return

    # Batch mode: load ranked deals
    ranked = load_ranked_deals()

    if args.sheriff:
        targets = ranked[ranked["in_sheriff_sale"] == True]
        print(f"  {len(targets)} sheriff sale properties found")
    elif args.top > 0:
        targets = ranked.nlargest(args.top, "composite_score")
        print(f"  Analyzing top {len(targets)} ranked deals")
    else:
        parser.print_help()
        sys.exit(1)

    # Run comps for each target
    all_results = []
    for _, row in targets.iterrows():
        addr = row["address"]
        result = get_comps(addr, parcels, n=args.comps)
        all_results.append(result)
        if not args.json:
            display_results(result)

    if args.json:
        serialized = [serialize_result(r) for r in all_results]
        print(json.dumps(serialized, indent=2))

    # Summary stats
    if not args.json and len(all_results) > 1:
        print_batch_summary(all_results)


def serialize_result(result: dict) -> dict:
    """Make a result dict JSON-serializable."""
    if "error" in result:
        return result

    def serialize_item(d: dict) -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, datetime):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out

    return {
        "target": serialize_item(result["target"]),
        "comps": [serialize_item(c) for c in result["comps"]],
        "valuation": result["valuation"],
        "confidence": result["confidence"],
    }


def print_batch_summary(results: list[dict]) -> None:
    """Print summary statistics for a batch of comp analyses."""
    valid = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    print("\n" + "=" * 80)
    print("  BATCH SUMMARY")
    print("=" * 80)
    print(f"  Properties analyzed: {len(results)}")
    print(f"  Successful: {len(valid)}   Errors: {len(errors)}")

    if valid:
        confidences = [r["confidence"] for r in valid]
        print(f"\n  Confidence Distribution:")
        for level in ["high", "medium", "low"]:
            count = confidences.count(level)
            print(f"    {level.upper()}: {count}")

        # Valuation summary
        medians = [r["valuation"]["median"] for r in valid if r["valuation"]["median"] > 0]
        if medians:
            print(f"\n  Median Implied Values:")
            print(f"    Min: {format_currency(min(medians))}")
            print(f"    Avg: {format_currency(int(sum(medians) / len(medians)))}")
            print(f"    Max: {format_currency(max(medians))}")

    if errors:
        print(f"\n  Properties not found:")
        for r in errors:
            print(f"    - {r['error']}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
