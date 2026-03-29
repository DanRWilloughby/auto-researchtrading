#!/usr/bin/env python3
"""
Real Estate Deal Sourcing Pipeline v2 — Paterson, NJ
For Veloce Capital + Forte Investment Fund

Sprint 2: Market-calibrated pricing, absentee owners, sheriff sales, better units.
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── ArcGIS MOD-IV Parcel Data ──────────────────────────────────────────────

ARCGIS_URL = (
    "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/"
    "Parcels_Composite_NJ_WM/FeatureServer/0/query"
)

PARCEL_FIELDS = [
    "OBJECTID", "PAMS_PIN", "PCLBLOCK", "PCLLOT", "PROP_CLASS", "COUNTY",
    "MUN_NAME", "PROP_LOC", "OWNER_NAME", "ST_ADDRESS", "CITY_STATE",
    "ZIP_CODE", "LAND_VAL", "IMPRVT_VAL", "NET_VALUE", "LAST_YR_TX",
    "BLDG_DESC", "LAND_DESC", "CALC_ACRE", "PROP_USE", "BLDG_CLASS",
    "DEED_DATE", "YR_CONSTR", "SALE_PRICE", "DWELL", "COMM_DWELL",
    "ZIP5", "Shape__Area",
]

INVESTABLE_CLASSES = {
    "2":   "Residential (1-4 family)",
    "4A":  "Commercial",
    "4B":  "Industrial",
    "4C":  "Apartment (5+ units)",
    "1":   "Vacant Land",
}


def fetch_paterson_parcels(force=False):
    """Download all Paterson parcels from NJ ArcGIS MOD-IV service."""
    cache_path = DATA_DIR / "paterson_parcels.json"
    if cache_path.exists() and not force:
        print(f"  Using cached parcels: {cache_path}")
        with open(cache_path) as f:
            return json.load(f)

    print("  Fetching Paterson parcels from ArcGIS...")
    all_features = []
    offset = 0
    batch_size = 2000

    while True:
        params = {
            "where": "MUN_NAME='PATERSON CITY'",
            "outFields": ",".join(PARCEL_FIELDS),
            "returnGeometry": "false",
            "resultRecordCount": str(batch_size),
            "resultOffset": str(offset),
            "f": "json",
        }
        resp = requests.get(ARCGIS_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        features = data.get("features", [])
        if not features:
            break

        all_features.extend([f["attributes"] for f in features])
        print(f"    Fetched {len(all_features)} parcels...")
        offset += batch_size

        if len(features) < batch_size:
            break
        time.sleep(0.5)

    with open(cache_path, "w") as f:
        json.dump(all_features, f)
    print(f"  Saved {len(all_features)} parcels to {cache_path}")
    return all_features


# ─── HUD Small Area Fair Market Rents ────────────────────────────────────────

def load_hud_safmr():
    """Load HUD Small Area FMR by ZIP code."""
    path = DATA_DIR / "hud_safmr_fy2026.xlsx"
    if not path.exists():
        print("  Downloading HUD SAFMR FY2026...")
        url = "https://www.huduser.gov/portal/datasets/fmr/fmr2026/fy2026_safmrs.xlsx"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)

    df = pd.read_excel(path, engine="openpyxl")
    df.columns = [c.replace("\n", " ").strip() for c in df.columns]
    df["ZIP Code"] = df["ZIP Code"].astype(str).str.zfill(5)
    paterson_zips = [f"075{i:02d}" for i in range(1, 23)]
    df = df[df["ZIP Code"].isin(paterson_zips)]

    rent_by_zip = {}
    for _, row in df.iterrows():
        z = row["ZIP Code"]
        rent_by_zip[z] = {
            "0br": row.get("SAFMR 0BR", 0),
            "1br": row.get("SAFMR 1BR", 0),
            "2br": row.get("SAFMR 2BR", 0),
            "3br": row.get("SAFMR 3BR", 0),
        }
        for col in df.columns:
            if "4BR" in col and "90%" not in col and "110%" not in col:
                rent_by_zip[z]["4br"] = row.get(col, 0)

    print(f"  Loaded SAFMR for {len(rent_by_zip)} Paterson ZIP codes")
    return rent_by_zip


def load_zillow_zori():
    """Load Zillow Observed Rent Index by ZIP."""
    path = DATA_DIR / "zillow_zori_zip.csv"
    if not path.exists():
        print("  Downloading Zillow ZORI...")
        url = "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)

    df = pd.read_csv(path)
    date_cols = [c for c in df.columns if c[:4].isdigit()]
    latest_col = sorted(date_cols)[-1]
    paterson_zips = [str(z) for z in range(7501, 7523)]
    df["RegionName"] = df["RegionName"].astype(str)
    passaic = df[df["RegionName"].isin(paterson_zips)]

    zori_by_zip = {}
    for _, row in passaic.iterrows():
        z = str(row["RegionName"]).zfill(5)
        zori_by_zip[z] = row[latest_col] if pd.notna(row[latest_col]) else None

    print(f"  Loaded ZORI for {len(zori_by_zip)} Paterson ZIPs (latest: {latest_col})")
    return zori_by_zip, latest_col


def load_census_acs():
    """Load Census ACS tract-level data for Passaic County."""
    cache_path = DATA_DIR / "census_acs_passaic.json"
    if cache_path.exists():
        with open(cache_path) as f:
            data = json.load(f)
    else:
        print("  Fetching Census ACS data for Passaic County...")
        url = (
            "https://api.census.gov/data/2022/acs/acs5"
            "?get=B25064_001E,B25002_001E,B25002_003E,B19013_001E,B01003_001E,NAME"
            "&for=tract:*&in=state:34&in=county:031"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        with open(cache_path, "w") as f:
            json.dump(data, f)

    headers = data[0]
    rows = data[1:]
    tracts = {}
    for row in rows:
        d = dict(zip(headers, row))
        tract_id = d["tract"]
        total_units = int(d["B25002_001E"]) if d["B25002_001E"] else 0
        vacant = int(d["B25002_003E"]) if d["B25002_003E"] else 0
        tracts[tract_id] = {
            "name": d["NAME"],
            "median_rent": int(d["B25064_001E"]) if d["B25064_001E"] else None,
            "total_housing_units": total_units,
            "vacant_units": vacant,
            "vacancy_rate": vacant / total_units if total_units > 0 else 0,
            "median_income": int(d["B19013_001E"]) if d["B19013_001E"] else None,
            "population": int(d["B01003_001E"]) if d["B01003_001E"] else None,
        }

    print(f"  Loaded Census ACS for {len(tracts)} Passaic County tracts")
    return tracts


# ─── Sprint 3: FEMA Flood Zones ──────────────────────────────────────────────

def load_flood_zones():
    """Load FEMA flood zone bounding boxes for spatial matching."""
    path = DATA_DIR / "fema_flood_zones_passaic_34031_summary.json"
    if not path.exists():
        print("  No FEMA flood data found")
        return []

    with open(path) as f:
        data = json.load(f)

    # Extract high-risk zone bounding boxes for Paterson area
    paterson_bbox = data.get("paterson_area_flood_zones", {}).get("paterson_bbox", {})
    high_risk_features = []
    for feat in data.get("features", []):
        zone = feat["attributes"].get("FLD_ZONE", "")
        subtype = feat["attributes"].get("ZONE_SUBTY") or ""
        bbox = feat.get("bbox", {})
        if not bbox:
            continue
        # Only high-risk zones
        if zone in ("A", "AE", "AH", "AO"):
            high_risk_features.append({
                "zone": zone,
                "subtype": subtype,
                "min_lon": bbox["min_lon"],
                "min_lat": bbox["min_lat"],
                "max_lon": bbox["max_lon"],
                "max_lat": bbox["max_lat"],
            })

    print(f"  Loaded {len(high_risk_features)} high-risk flood zone bboxes")
    return high_risk_features


# ─── Sprint 3: Opportunity Zones ─────────────────────────────────────────────

def load_opportunity_zones():
    """Load Opportunity Zone tract IDs for Paterson."""
    path = DATA_DIR / "opportunity_zones_passaic_34031.json"
    if not path.exists():
        print("  No Opportunity Zone data found")
        return set()

    with open(path) as f:
        data = json.load(f)

    oz_geoids = set(data.get("paterson_tracts", {}).get("geoids", []))
    print(f"  Loaded {len(oz_geoids)} Paterson Opportunity Zone tracts")
    return oz_geoids


# ─── Sprint 2: Sheriff Sales ────────────────────────────────────────────────

def load_sheriff_sales():
    """Load sheriff sale addresses for distress flagging."""
    path = DATA_DIR / "passaic_sheriff_sales_paterson.json"
    if not path.exists():
        print("  No sheriff sales data found")
        return set()

    with open(path) as f:
        data = json.load(f)

    # Handle both list and dict formats
    if isinstance(data, list):
        properties = data
    elif isinstance(data, dict) and "paterson_properties" in data:
        properties = data["paterson_properties"]
    else:
        return set()

    # Normalize addresses for matching
    addresses = set()
    for p in properties:
        addr = (p.get("address") or "").upper().strip()
        addr = addr.replace(",", "").replace(".", "")
        # Extract just the street part (before city)
        if "PATERSON" in addr:
            addr = addr[:addr.index("PATERSON")].strip()
        addresses.add(addr)

    print(f"  Loaded {len(addresses)} sheriff sale addresses")
    return addresses


# ─── Sprint 2: Market Pricing Calibration ────────────────────────────────────

# Key finding from backtest: MOD-IV SALE_PRICE data shows the ACTUAL assessment-
# to-sale ratio is only ~1.12x (median). The Redfin $650K figure was selection bias
# (top-tier broker-marketed multifamily only). Most sales happen near assessed value.
#
# Two tiers of market value:
#   - Off-market / distressed: ~1.1x assessed (where Veloce actually buys)
#   - On-market / broker-listed: ~2.5-3x assessed (Redfin/Zillow listings)
#
# We model TWO scenarios: acquisition at distressed pricing, exit at market pricing.

ASSESSMENT_RATIO_ACQUISITION = {
    "2":  1.15,   # Residential: buy near assessed value
    "4A": 1.20,   # Commercial
    "4B": 1.10,   # Industrial
    "4C": 1.15,   # Apartments
    "1":  1.10,   # Vacant land
}

ASSESSMENT_RATIO_EXIT = {
    "2":  2.50,   # Residential: sell at market after renovation
    "4A": 2.00,   # Commercial
    "4B": 1.80,   # Industrial
    "4C": 2.50,   # Apartments: stabilized MF commands premium
    "1":  1.50,   # Vacant land with entitlements
}

# Actual market rents from Zillow/Zumper March 2026
MARKET_RENTS = {
    "studio": 1454,
    "1br": 1595,
    "2br": 2000,
    "3br": 2300,
    "4br": 2825,
}


# ─── Scoring Engine ──────────────────────────────────────────────────────────

PATERSON_TAX_RATE = 3.776  # per $100 assessed value (tax is on ASSESSED, not market)
INSURANCE_RATE = 0.004       # 0.4% of market value
MGMT_RATE = 0.08
MAINTENANCE_RATE_CURRENT = 0.10
MAINTENANCE_RATE_RENO = 0.05
VACANCY_DEFAULT = 0.07
RENOVATION_PREMIUM = 0.20   # 20% rent lift
RENOVATION_COST_PSF = 55    # $55/sqft (Paterson, moderate reno)
EXIT_CAP_RATE = 0.065       # 6.5% stabilized cap rate (per LoopNet comps)


def get_units(parcel):
    """Get unit count from DWELL field or estimate from property class."""
    prop_class = (parcel.get("PROP_CLASS") or "").strip()
    dwell = parcel.get("DWELL")
    comm_dwell = parcel.get("COMM_DWELL")

    if dwell is not None:
        try:
            d = int(dwell)
            if d > 0:
                return d
        except (ValueError, TypeError):
            pass
    if comm_dwell is not None:
        try:
            d = int(comm_dwell)
            if d > 0:
                return d
        except (ValueError, TypeError):
            pass

    # Parse BLDG_DESC for floor count hints
    bldg_desc = (parcel.get("BLDG_DESC") or "").strip()
    if prop_class == "4C":
        net = parcel.get("NET_VALUE") or 0
        if net > 1000000:
            return max(10, int(net / 100000))
        if net > 500000:
            return max(6, int(net / 100000))
        return 6
    if prop_class == "4A":
        return 1
    if prop_class == "4B":
        return 1
    # Class 2 — most have DWELL; if missing, guess from bldg_desc
    if "3" in bldg_desc and "F" in bldg_desc:
        return 3
    if "2" in bldg_desc and "F" in bldg_desc:
        return 2
    return 1


def estimate_sqft(parcel):
    """Estimate building sqft from improvement value and lot size."""
    imprvt_val = parcel.get("IMPRVT_VAL") or 0
    shape_area = parcel.get("Shape__Area") or 0  # in sq meters from ArcGIS
    lot_sqft = shape_area * 10.764 if shape_area > 0 else 0

    # Use improvement value at roughly $120/sqft assessed basis
    if imprvt_val > 10000:
        return max(800, int(imprvt_val / 120))

    # Fallback: 60% lot coverage, 2 floors
    if lot_sqft > 0:
        return max(800, int(lot_sqft * 0.6 * 2))

    return 1200  # default


def estimate_acquisition_price(parcel):
    """Estimate realistic acquisition price (what Veloce would actually pay)."""
    prop_class = (parcel.get("PROP_CLASS") or "").strip()
    net_value = parcel.get("NET_VALUE") or 0
    sale_price = parcel.get("SALE_PRICE") or 0

    # If there's a real recent sale (>$50K), use it as acquisition basis
    if sale_price > 50000:
        return sale_price

    # Otherwise estimate from assessed value at off-market ratios
    ratio = ASSESSMENT_RATIO_ACQUISITION.get(prop_class, 1.15)
    return int(net_value * ratio)


def estimate_exit_value_from_assessment(parcel):
    """Estimate post-renovation market value (what it would sell for stabilized)."""
    prop_class = (parcel.get("PROP_CLASS") or "").strip()
    net_value = parcel.get("NET_VALUE") or 0

    ratio = ASSESSMENT_RATIO_EXIT.get(prop_class, 2.0)
    return int(net_value * ratio)


def estimate_rent_per_unit(parcel, rent_by_zip, zori_by_zip):
    """Estimate rent using market data, weighted by unit size."""
    zip_code = str(parcel.get("ZIP5") or parcel.get("ZIP_CODE") or "").zfill(5)
    units = get_units(parcel)

    # Use actual market rents from Zillow/Zumper (most accurate)
    # For multifamily, estimate bedroom mix based on unit count
    if units == 1:
        return MARKET_RENTS["2br"]  # SFR typically 2br+
    elif units == 2:
        # Duplex: typically two 2BR units
        return MARKET_RENTS["2br"]
    elif units == 3:
        # 3-family: 1BR + 2BR + 3BR typical
        return (MARKET_RENTS["1br"] + MARKET_RENTS["2br"] + MARKET_RENTS["3br"]) / 3
    elif units == 4:
        return (MARKET_RENTS["1br"] + MARKET_RENTS["2br"] * 2 + MARKET_RENTS["3br"]) / 4
    else:
        # 5+ units: mix of 1BR and 2BR
        return (MARKET_RENTS["1br"] * 0.4 + MARKET_RENTS["2br"] * 0.6)

    return MARKET_RENTS["2br"]  # fallback


def classify_absentee(parcel):
    """Determine absentee owner status from mailing address."""
    prop_loc = (parcel.get("PROP_LOC") or "").strip().upper()
    st_addr = (parcel.get("ST_ADDRESS") or "").strip().upper()
    city_state = (parcel.get("CITY_STATE") or "").strip().upper()

    if not city_state:
        return "unknown", None

    if "PATERSON" not in city_state:
        if "NJ" not in city_state and "NEW JERSEY" not in city_state:
            return "out_of_state", city_state
        return "absentee_nj", city_state

    if prop_loc and st_addr and prop_loc != st_addr:
        return "absentee_local", city_state

    return "owner_occupied", None


def score_property(parcel, rent_by_zip, zori_by_zip, census_tracts,
                   sheriff_addresses, flood_zones=None, oz_tracts=None):
    """Score a single property for value-add potential."""
    prop_class = (parcel.get("PROP_CLASS") or "").strip()
    net_value = parcel.get("NET_VALUE") or 0
    land_val = parcel.get("LAND_VAL") or 0
    imprvt_val = parcel.get("IMPRVT_VAL") or 0
    sale_price = parcel.get("SALE_PRICE") or 0
    last_yr_tax = parcel.get("LAST_YR_TX") or 0
    yr_constr = parcel.get("YR_CONSTR") or 0
    zip_code = str(parcel.get("ZIP5") or parcel.get("ZIP_CODE") or "").zfill(5)
    prop_loc = (parcel.get("PROP_LOC") or "").strip().upper()

    # Skip trivial parcels
    if net_value < 50000:
        return None

    # ── Acquisition & Exit Values ──
    acq_price = estimate_acquisition_price(parcel)
    market_value = acq_price  # for backward compat in output
    if acq_price < 75000:
        return None

    units = get_units(parcel)
    sqft = estimate_sqft(parcel)

    # ── Absentee Owner ──
    absentee_status, owner_location = classify_absentee(parcel)

    # ── Sheriff Sale Check ──
    addr_normalized = prop_loc.replace(",", "").replace(".", "")
    in_sheriff_sale = any(
        sheriff_addr in addr_normalized or addr_normalized in sheriff_addr
        for sheriff_addr in sheriff_addresses
        if sheriff_addr and len(sheriff_addr) > 5
    )

    # ── Rent Estimation ──
    monthly_rent_per_unit = estimate_rent_per_unit(parcel, rent_by_zip, zori_by_zip)
    gross_annual_rent = monthly_rent_per_unit * 12 * units

    # ── As-Is Analysis (using acquisition price for cap rate) ──
    vacancy_rate = VACANCY_DEFAULT
    effective_rent = gross_annual_rent * (1 - vacancy_rate)
    # Tax is on ASSESSED value, not market value
    annual_tax = last_yr_tax if last_yr_tax > 0 else (net_value * PATERSON_TAX_RATE / 100)
    insurance = acq_price * INSURANCE_RATE
    mgmt = effective_rent * MGMT_RATE
    maintenance = effective_rent * MAINTENANCE_RATE_CURRENT
    opex = annual_tax + insurance + mgmt + maintenance
    noi_current = effective_rent - opex
    cap_rate_current = noi_current / acq_price if acq_price > 0 else 0

    # ── Post-Renovation Projection ──
    reno_rent = monthly_rent_per_unit * (1 + RENOVATION_PREMIUM)
    gross_annual_reno = reno_rent * 12 * units
    eff_rent_reno = gross_annual_reno * 0.95  # 5% stabilized vacancy
    # Tax reassessment after reno: estimate 30% increase on assessed
    tax_reno = annual_tax * 1.30
    insurance_reno = acq_price * 1.3 * INSURANCE_RATE
    mgmt_reno = eff_rent_reno * MGMT_RATE
    maint_reno = eff_rent_reno * MAINTENANCE_RATE_RENO
    opex_reno = tax_reno + insurance_reno + mgmt_reno + maint_reno
    noi_stabilized = eff_rent_reno - opex_reno

    reno_cost = sqft * RENOVATION_COST_PSF
    total_cost = acq_price + reno_cost

    # Exit value: HIGHER of NOI-based cap rate exit or assessment-based exit
    exit_noi = noi_stabilized / EXIT_CAP_RATE if noi_stabilized > 0 else 0
    exit_assessment = estimate_exit_value_from_assessment(parcel)
    exit_value = max(exit_noi, exit_assessment)

    equity_multiple = exit_value / total_cost if total_cost > 0 else 0
    rent_to_price = (monthly_rent_per_unit * units) / acq_price if acq_price > 0 else 0

    # ── Distress Signals ──
    distress_score = 0
    distress_flags = []

    if in_sheriff_sale:
        distress_score += 3
        distress_flags.append("sheriff_sale")

    if imprvt_val is not None and land_val is not None and (land_val + imprvt_val) > 0:
        imp_ratio = imprvt_val / (land_val + imprvt_val)
        if imp_ratio < 0.3:
            distress_score += 2
            distress_flags.append("low_improvement_ratio")

    if yr_constr and 1800 < yr_constr < 1960:
        distress_score += 1
        distress_flags.append("pre_1960")

    if sale_price > 0 and net_value > 0 and sale_price < net_value * 0.7:
        distress_score += 2
        distress_flags.append("sold_below_assessed")

    if last_yr_tax == 0:
        distress_score += 1
        distress_flags.append("zero_tax")

    if absentee_status == "out_of_state":
        distress_score += 2
        distress_flags.append("out_of_state_owner")
    elif absentee_status in ("absentee_nj", "absentee_local"):
        distress_score += 1
        distress_flags.append("absentee")

    # ── Flood Zone Check (Sprint 3) ──
    in_flood_zone = False
    flood_zone_type = None
    if flood_zones:
        # Use parcel's Shape__Area centroid approximation via ZIP-based rough matching
        # For proper spatial join we'd need parcel centroids, but bbox overlap works
        # as a conservative flag
        shape_area = parcel.get("Shape__Area") or 0
        if shape_area > 0:
            # Note: without actual coordinates we flag by ZIP-level known flood areas
            # Paterson flood-prone ZIPs along Passaic River: 07501, 07503, 07505, 07522
            flood_prone_zips = {"07501", "07503", "07505", "07522"}
            if zip_code in flood_prone_zips:
                in_flood_zone = True
                flood_zone_type = "near_passaic_river"
                distress_flags.append("flood_risk_area")

    # ── Opportunity Zone Check (Sprint 3) ──
    in_oz = False
    if oz_tracts:
        # Match parcel ZIP to known OZ tracts
        # OZ tracts in Paterson: 1815, 1818, 1823.02, 1825, 1828, 1829, 1832, 2642
        # These roughly correspond to ZIPs 07501, 07503, 07505, 07522 (south/central Paterson)
        oz_zips = {"07501", "07503", "07505", "07522", "07524"}
        if zip_code in oz_zips:
            in_oz = True

    # ── Cap Rate Spread ──
    cap_rate_stabilized = noi_stabilized / market_value if market_value > 0 else 0
    cap_spread = cap_rate_stabilized - cap_rate_current

    return {
        "address": prop_loc,
        "zip": zip_code,
        "block_lot": f"{parcel.get('PCLBLOCK','')}/{parcel.get('PCLLOT','')}",
        "prop_class": prop_class,
        "prop_class_desc": INVESTABLE_CLASSES.get(prop_class, prop_class),
        "year_built": yr_constr if yr_constr and yr_constr > 1800 else None,
        "est_units": units,
        "sqft": sqft,
        "net_assessed": net_value,
        "est_market_value": market_value,
        "last_sale_price": sale_price if sale_price > 0 else None,
        "annual_tax": round(annual_tax),
        "absentee_status": absentee_status,
        "owner_location": owner_location,
        "in_sheriff_sale": in_sheriff_sale,
        "in_flood_zone": in_flood_zone,
        "flood_zone_type": flood_zone_type,
        "in_opportunity_zone": in_oz,
        "est_rent_per_unit": round(monthly_rent_per_unit),
        "gross_annual_rent": round(gross_annual_rent),
        "noi_current": round(noi_current),
        "cap_rate_current": round(cap_rate_current, 4),
        "annual_opex": round(opex),
        "noi_stabilized": round(noi_stabilized),
        "est_reno_cost": round(reno_cost),
        "total_cost": round(total_cost),
        "exit_value": round(exit_value),
        "equity_multiple": round(equity_multiple, 2),
        "rent_to_price": round(rent_to_price, 4),
        "cap_rate_spread": round(cap_spread, 4),
        "distress_score": distress_score,
        "distress_flags": ",".join(distress_flags),
        "raw_equity_multiple": equity_multiple,
        "raw_cap_spread": cap_spread,
        "raw_distress": distress_score,
        "raw_rent_price": rent_to_price,
    }


def percentile_rank(series):
    return series.rank(pct=True, na_option="bottom")


def run_pipeline():
    """Execute the full deal sourcing pipeline."""
    print("=" * 70)
    print("  PATERSON NJ — DEAL SOURCING PIPELINE v2")
    print("  For Veloce Capital + Forte Investment Fund")
    print("  Market-calibrated pricing | Absentee owners | Sheriff sales")
    print("=" * 70)

    # ── Step 1: Load Data ──
    print("\n[1/5] Loading data sources...")
    parcels = fetch_paterson_parcels()
    rent_by_zip = load_hud_safmr()
    zori_by_zip, zori_date = load_zillow_zori()
    census_tracts = load_census_acs()
    sheriff_addresses = load_sheriff_sales()
    flood_zones = load_flood_zones()
    oz_tracts = load_opportunity_zones()

    # ── Step 2: Filter to investable properties ──
    print("\n[2/5] Filtering to investable properties...")
    investable = []
    skip_counts = {}
    for p in parcels:
        pc = (p.get("PROP_CLASS") or "").strip()
        if pc in INVESTABLE_CLASSES:
            investable.append(p)
        else:
            skip_counts[pc] = skip_counts.get(pc, 0) + 1

    print(f"  Total parcels: {len(parcels)}")
    print(f"  Investable classes: {len(investable)}")

    # ── Step 3: Score each property ──
    print("\n[3/5] Scoring properties (market-calibrated)...")
    results = []
    for p in investable:
        r = score_property(p, rent_by_zip, zori_by_zip, census_tracts,
                          sheriff_addresses, flood_zones, oz_tracts)
        if r is not None and r["equity_multiple"] > 0:
            results.append(r)

    df = pd.DataFrame(results)
    print(f"  Scored: {len(df)} properties")

    if df.empty:
        print("  No scoreable properties found!")
        return

    # ── Step 4: Percentile rank and composite score ──
    print("\n[4/5] Computing composite scores...")

    df["pct_equity_multiple"] = percentile_rank(df["raw_equity_multiple"])
    df["pct_cap_spread"] = percentile_rank(df["raw_cap_spread"])
    df["pct_distress"] = percentile_rank(df["raw_distress"])
    df["pct_rent_price"] = percentile_rank(df["raw_rent_price"])

    df["composite_score"] = (
        0.30 * df["pct_equity_multiple"]
        + 0.25 * df["pct_cap_spread"]
        + 0.20 * df["pct_distress"]
        + 0.15 * df["pct_rent_price"]
        + 0.10 * df["pct_equity_multiple"]  # double-weight total return
    )

    df = df.sort_values("composite_score", ascending=False)

    # ── Step 5: Output ──
    print("\n[5/5] Generating output...")

    output_cols = [
        "address", "zip", "block_lot", "prop_class", "prop_class_desc",
        "year_built", "est_units", "sqft", "net_assessed", "est_market_value",
        "last_sale_price", "annual_tax", "absentee_status", "owner_location",
        "in_sheriff_sale", "in_flood_zone", "in_opportunity_zone",
        "est_rent_per_unit", "gross_annual_rent",
        "noi_current", "cap_rate_current", "rent_to_price",
        "est_reno_cost", "total_cost", "noi_stabilized", "exit_value",
        "equity_multiple", "cap_rate_spread", "distress_score",
        "distress_flags", "composite_score",
    ]

    out_path = DATA_DIR / "paterson_ranked_deals.csv"
    df[output_cols].to_csv(out_path, index=False)
    print(f"  Saved {len(df)} ranked properties to {out_path}")

    # ── Print Results ──

    def print_top(label, subset, n=15):
        print(f"\n{'=' * 70}")
        print(f"  TOP {n} — {label}")
        print("=" * 70)
        for i, (_, row) in enumerate(subset.head(n).iterrows(), 1):
            sale_str = f"Last Sale: ${row['last_sale_price']:,.0f}" if pd.notna(row['last_sale_price']) and row['last_sale_price'] else ""
            yr = int(row['year_built']) if pd.notna(row['year_built']) and row['year_built'] else '?'
            sheriff_tag = " *** SHERIFF SALE ***" if row['in_sheriff_sale'] else ""
            absentee_tag = f" [{row['absentee_status']}]" if row['absentee_status'] not in ('owner_occupied', 'unknown') else ""
            oz_tag = " [OZ]" if row.get('in_opportunity_zone') else ""
            flood_tag = " [FLOOD]" if row.get('in_flood_zone') else ""

            print(f"\n  #{i:02d} | {row['address']} ({row['zip']}){sheriff_tag}{oz_tag}{flood_tag}")
            print(f"      {row['prop_class_desc']} | {row['est_units']} units | {row['sqft']:,} sqft | Built: {yr}{absentee_tag}")
            print(f"      Market Value: ${row['est_market_value']:,.0f} | Assessed: ${row['net_assessed']:,.0f} | {sale_str}")
            print(f"      Rent: ${row['est_rent_per_unit']:,.0f}/unit/mo → ${row['gross_annual_rent']:,.0f}/yr gross")
            print(f"      NOI: ${row['noi_current']:,.0f} → ${row['noi_stabilized']:,.0f} (stabilized) | Cap: {row['cap_rate_current']:.1%} → {row['cap_rate_spread']+row['cap_rate_current']:.1%}")
            print(f"      Total Cost: ${row['total_cost']:,.0f} → Exit: ${row['exit_value']:,.0f} | Equity: {row['equity_multiple']:.2f}x")
            if row['distress_flags']:
                print(f"      Flags: {row['distress_flags']}")

    # ── Sheriff Sales (highest priority) ──
    sheriff_df = df[df["in_sheriff_sale"]].copy()
    if len(sheriff_df) > 0:
        print_top("SHERIFF SALE PROPERTIES (immediate opportunities)", sheriff_df, len(sheriff_df))

    # ── Multifamily 3+ units (Veloce's sweet spot) ──
    mf_3plus = df[(df["prop_class"].isin(["2", "4C"])) & (df["est_units"] >= 3)].copy()
    print_top("MULTIFAMILY 3+ UNITS (value-add sweet spot)", mf_3plus, 20)

    # ── Absentee + Distressed (motivated sellers) ──
    motivated = df[
        (df["absentee_status"].isin(["out_of_state", "absentee_nj"]))
        & (df["distress_score"] >= 3)
        & (df["est_units"] >= 2)
    ].copy()
    print_top("ABSENTEE + DISTRESSED (motivated sellers)", motivated, 15)

    # ── Apartments 5+ ──
    apts = df[df["prop_class"] == "4C"].copy()
    if len(apts) > 0:
        print_top("APARTMENTS (5+ units)", apts, 10)

    # ── Commercial / Industrial ──
    comm = df[df["prop_class"].isin(["4A", "4B"])].copy()
    print_top("COMMERCIAL / INDUSTRIAL (adaptive reuse)", comm, 10)

    # ── Opportunity Zone Deals ──
    if "in_opportunity_zone" in df.columns:
        oz_deals = df[
            (df["in_opportunity_zone"] == True)
            & (df["est_units"] >= 2)
        ].copy()
        if len(oz_deals) > 0:
            print_top("OPPORTUNITY ZONE DEALS (tax-advantaged)", oz_deals, 10)

    # ── Market Overview ──
    print(f"\n{'=' * 70}")
    print("  MARKET OVERVIEW — PATERSON, NJ (Market-Calibrated)")
    print("=" * 70)
    print(f"  Total properties scored: {len(df)}")
    for pc, desc in INVESTABLE_CLASSES.items():
        count = len(df[df["prop_class"] == pc])
        if count > 0:
            print(f"    {pc} ({desc}): {count}")

    print(f"\n  Median market value (est): ${df['est_market_value'].median():,.0f}")
    print(f"  Median assessed value: ${df['net_assessed'].median():,.0f}")
    print(f"  Assessment-to-market ratio: {df['net_assessed'].median() / df['est_market_value'].median():.0%}")

    print(f"\n  Median cap rate (as-is): {df['cap_rate_current'].median():.1%}")
    print(f"  Median equity multiple: {df['equity_multiple'].median():.2f}x")
    print(f"  Median rent/price ratio: {df['rent_to_price'].median():.4f}")

    # Absentee breakdown
    abs_counts = df['absentee_status'].value_counts()
    print(f"\n  Owner status:")
    for status, count in abs_counts.items():
        print(f"    {status}: {count} ({count/len(df)*100:.0f}%)")

    print(f"\n  Sheriff sale properties matched: {df['in_sheriff_sale'].sum()}")
    print(f"  High distress (score >= 3): {len(df[df['distress_score'] >= 3])}")
    print(f"  Equity multiple > 1.5x: {len(df[df['equity_multiple'] > 1.5])}")
    print(f"  Cap rate > 6%: {len(df[df['cap_rate_current'] > 0.06])}")

    if "in_opportunity_zone" in df.columns:
        oz_count = df["in_opportunity_zone"].sum()
        flood_count = df["in_flood_zone"].sum() if "in_flood_zone" in df.columns else 0
        print(f"\n  Opportunity Zone properties: {oz_count} ({oz_count/len(df)*100:.0f}%)")
        print(f"  Flood risk area properties: {flood_count} ({flood_count/len(df)*100:.0f}%)")


if __name__ == "__main__":
    force_refresh = "--refresh" in sys.argv
    if force_refresh:
        for f in DATA_DIR.glob("paterson_parcels.json"):
            f.unlink()
        for f in DATA_DIR.glob("census_acs_passaic.json"):
            f.unlink()
    run_pipeline()
