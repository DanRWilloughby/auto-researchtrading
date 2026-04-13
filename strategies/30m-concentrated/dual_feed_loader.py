"""
Loads and time-aligns HL and CB candle data for dual-feed strategies.
Returns merged DataFrames with columns suffixed _hl and _cb.
"""
import os
import pandas as pd

# Use the same DATA_DIR as the backtest engine
_cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
DATA_DIR = os.environ.get("AUTOTRADER_DATA_DIR", os.path.join(_cache_dir, "data"))


def load_dual_candles(symbol: str, interval: str = "30m") -> pd.DataFrame:
    """Load HL and CB candles, merge on timestamp, return only overlapping bars."""
    hl_path = os.path.join(DATA_DIR, f"{symbol}_{interval}.parquet")
    cb_path = os.path.join(DATA_DIR, f"{symbol}_{interval}_coinbase.parquet")

    hl = pd.read_parquet(hl_path).rename(columns={
        "open": "open_hl", "high": "high_hl", "low": "low_hl",
        "close": "close_hl", "volume": "volume_hl",
    })
    cb = pd.read_parquet(cb_path).rename(columns={
        "open": "open_cb", "high": "high_cb", "low": "low_cb",
        "close": "close_cb", "volume": "volume_cb",
    })

    hl = hl[["timestamp", "open_hl", "high_hl", "low_hl", "close_hl", "volume_hl"]]
    cb = cb[["timestamp", "open_cb", "high_cb", "low_cb", "close_cb", "volume_cb"]]

    merged = pd.merge(hl, cb, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    # Add funding rate from HL if available
    hl_full = pd.read_parquet(hl_path)
    if "funding_rate" in hl_full.columns:
        funding = hl_full[["timestamp", "funding_rate"]]
        merged = pd.merge(merged, funding, on="timestamp", how="left")
        merged["funding_rate"] = merged["funding_rate"].fillna(0.0)
    else:
        merged["funding_rate"] = 0.0

    return merged
