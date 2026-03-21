"""
Data integrity validator for cached parquet files.
Run before backtesting to ensure data hasn't been tampered with or corrupted.

Usage:
    uv run validate_data.py              # validate all cached data
    uv run validate_data.py --checksums  # generate/verify SHA-256 checksums
"""

import os
import sys
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
DATA_DIR = os.environ.get("AUTOTRADER_DATA_DIR", os.path.join(CACHE_DIR, "data"))
CHECKSUM_FILE = os.path.join(DATA_DIR, "data_checksums.json")

EXPECTED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume", "funding_rate"}
EXPECTED_SYMBOLS = {"BTC", "ETH", "SOL"}

# Sanity bounds for price data
PRICE_BOUNDS = {
    "BTC": (10_000, 500_000),
    "ETH": (500, 50_000),
    "SOL": (1, 5_000),
}


def validate_schema(filepath: Path, symbol: str) -> list[str]:
    """Validate parquet file schema and basic integrity."""
    errors = []

    try:
        # Read parquet metadata without loading full data
        pf = pq.ParquetFile(filepath)
        schema = pf.schema_arrow

        # Check columns exist
        file_columns = set(schema.names)
        missing = EXPECTED_COLUMNS - file_columns
        if missing:
            errors.append(f"Missing columns: {missing}")

        # Check column types are numeric (not object/string)
        for col_name in EXPECTED_COLUMNS & file_columns:
            idx = schema.get_field_index(col_name)
            if idx >= 0:
                col_type = str(schema.field(idx).type)
                if "string" in col_type or "object" in col_type:
                    errors.append(f"Column '{col_name}' has non-numeric type: {col_type}")

    except Exception as e:
        errors.append(f"Failed to read parquet metadata: {e}")
        return errors

    # Load and validate data
    try:
        df = pd.read_parquet(filepath)
    except Exception as e:
        errors.append(f"Failed to read parquet data: {e}")
        return errors

    # Check not empty
    if len(df) == 0:
        errors.append("File is empty (0 rows)")
        return errors

    # Check timestamps are monotonically increasing
    if not df["timestamp"].is_monotonic_increasing:
        errors.append("Timestamps are not monotonically increasing")

    # Check no duplicate timestamps
    dupes = df["timestamp"].duplicated().sum()
    if dupes > 0:
        errors.append(f"{dupes} duplicate timestamps found")

    # Check for NaN/inf in price columns
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            nan_count = df[col].isna().sum()
            inf_count = (~df[col].apply(lambda x: abs(x) < float('inf'))).sum() if nan_count == 0 else 0
            if nan_count > 0:
                errors.append(f"Column '{col}' has {nan_count} NaN values")
            if inf_count > 0:
                errors.append(f"Column '{col}' has {inf_count} inf values")

    # Check price sanity bounds
    if symbol in PRICE_BOUNDS:
        low_bound, high_bound = PRICE_BOUNDS[symbol]
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                col_min = df[col].min()
                col_max = df[col].max()
                if col_min < low_bound * 0.1:  # 10x tolerance
                    errors.append(f"{col} min ({col_min:.2f}) below sanity bound ({low_bound * 0.1:.2f})")
                if col_max > high_bound * 10:  # 10x tolerance
                    errors.append(f"{col} max ({col_max:.2f}) above sanity bound ({high_bound * 10:.2f})")

    # Check high >= low
    if "high" in df.columns and "low" in df.columns:
        violations = (df["high"] < df["low"]).sum()
        if violations > 0:
            errors.append(f"{violations} bars where high < low")

    # Check volume is non-negative
    if "volume" in df.columns:
        neg_vol = (df["volume"] < 0).sum()
        if neg_vol > 0:
            errors.append(f"{neg_vol} bars with negative volume")

    return errors


def compute_checksum(filepath: Path) -> str:
    """SHA-256 checksum of file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate cached market data")
    parser.add_argument("--checksums", action="store_true",
                        help="Generate or verify SHA-256 checksums")
    args = parser.parse_args()

    if not os.path.exists(DATA_DIR):
        print(f"Data directory not found: {DATA_DIR}")
        print("Run 'uv run prepare.py' to download data first.")
        sys.exit(1)

    # Find all parquet files
    parquet_files = sorted(Path(DATA_DIR).glob("*.parquet"))
    if not parquet_files:
        print(f"No parquet files found in {DATA_DIR}")
        sys.exit(1)

    print(f"Validating {len(parquet_files)} data files in {DATA_DIR}\n")

    all_ok = True
    checksums = {}

    for filepath in parquet_files:
        symbol = filepath.stem.split("_")[0]
        print(f"  {filepath.name}:", end=" ")

        errors = validate_schema(filepath, symbol)
        if errors:
            print("FAILED")
            for err in errors:
                print(f"    - {err}")
            all_ok = False
        else:
            df = pd.read_parquet(filepath)
            print(f"OK ({len(df)} bars, {symbol})")

        if args.checksums:
            checksums[filepath.name] = compute_checksum(filepath)

    print()

    # Checksum handling
    if args.checksums:
        if os.path.exists(CHECKSUM_FILE):
            # Verify against stored checksums
            with open(CHECKSUM_FILE) as f:
                stored = json.load(f)

            print("Checksum verification:")
            for name, new_hash in checksums.items():
                if name in stored:
                    if stored[name] == new_hash:
                        print(f"  {name}: MATCH")
                    else:
                        print(f"  {name}: MISMATCH (file has been modified!)")
                        all_ok = False
                else:
                    print(f"  {name}: NEW (no stored checksum)")
            print()
        else:
            # No stored checksums — try to save (may fail in read-only containers)
            try:
                with open(CHECKSUM_FILE, "w") as f:
                    json.dump(checksums, f, indent=2)
                print(f"Checksums saved to {CHECKSUM_FILE}")
                print("Run again with --checksums to verify integrity.\n")
            except OSError:
                print(f"WARNING: Cannot write checksums (read-only filesystem).")
                print(f"Checksums should be generated during data download.\n")

    # Check for expected symbols
    found_symbols = {f.stem.split("_")[0] for f in parquet_files}
    missing_symbols = EXPECTED_SYMBOLS - found_symbols
    if missing_symbols:
        print(f"WARNING: Missing data for symbols: {missing_symbols}")

    if all_ok:
        print("All validations passed.")
    else:
        print("VALIDATION ERRORS FOUND. Fix issues before backtesting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
