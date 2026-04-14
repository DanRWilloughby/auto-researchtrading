"""JSONL emitters for per-fix attribution logging.

Each fix calls a single function here when its event fires. The function:
    1. Builds a dict matching the schema from PLANNED_FIXES.md
    2. Appends one JSON line to the appropriate dated log file
    3. Catches any I/O error and warns (NEVER raises — trading must continue)

Log files live under live/logs/ on the deploy host, dated by UTC day:
    skip_events_2026-04-15.jsonl
    hwm_track_2026-04-15.jsonl
    halt_events_2026-04-15.jsonl
    cooldown_events_2026-04-15.jsonl

These are append-only. Aggregators in monitoring/aggregate.py read them
to produce the dashboard JSON files.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default log directory. Trader sets this at startup via set_log_dir().
_LOG_DIR: Optional[Path] = None


def set_log_dir(log_dir: str | Path) -> None:
    """Configure where event JSONL files are written. Called once at trader startup.

    Fail-soft: if the directory can't be created, logs a warning. Subsequent
    writes will fail individually (also fail-soft).
    """
    global _LOG_DIR
    _LOG_DIR = Path(log_dir)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Failed to create monitoring log dir %s: %s", _LOG_DIR, e)


def _resolve_log_dir() -> Path:
    """Return the effective log directory, defaulting to live/logs/ in the repo."""
    if _LOG_DIR is not None:
        return _LOG_DIR
    repo_root = Path(__file__).resolve().parents[1]
    default = repo_root / "live" / "logs"
    default.mkdir(parents=True, exist_ok=True)
    return default


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_jsonl(filename: str, record: dict) -> None:
    """Fail-soft JSONL append. Logs warnings but never raises."""
    try:
        path = _resolve_log_dir() / filename
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.warning("Failed to write monitoring event to %s: %s", filename, e)


# ---------------------------------------------------------------------------
# Fix 1: SKIP events — fees avoided
# ---------------------------------------------------------------------------

def log_skip_event(
    symbol: str,
    target_notional_usd: float,
    current_notional_usd: float,
    delta_notional_usd: float,
    implied_fee_avoided_usd: float,
    tolerance_used_usd: float,
    skip_reason: str = "",
) -> None:
    """Emit a SKIP event when place_market_order skips below the tolerance.

    Schema matches PLANNED_FIXES.md Fix 1 logging spec.
    """
    record = {
        "ts": int(time.time() * 1000),
        "symbol": symbol,
        "target_notional_usd": round(target_notional_usd, 4),
        "current_notional_usd": round(current_notional_usd, 4),
        "delta_notional_usd": round(delta_notional_usd, 4),
        "implied_fee_avoided_usd": round(implied_fee_avoided_usd, 4),
        "tolerance_used_usd": round(tolerance_used_usd, 4),
        "skip_reason": skip_reason,
    }
    _append_jsonl(f"skip_events_{_today_utc()}.jsonl", record)


# ---------------------------------------------------------------------------
# Fix 5: HWM dual-track — both new (realized) and old (MTM) for comparison
# ---------------------------------------------------------------------------

def log_hwm_tick(
    mtm_equity: float,
    realized_equity: float,
    hwm_new_realized: float,
    hwm_old_mtm: float,
    dd_new_pct: float,
    dd_old_pct: float,
    threshold_pct: float,
) -> None:
    """Emit a per-tick HWM observation.

    Tracks both the new realized-only HWM and what the old MTM-based HWM
    would have been, so we can count phantom triggers prevented.

    Schema matches PLANNED_FIXES.md Fix 5 logging spec.
    """
    record = {
        "ts": int(time.time() * 1000),
        "mtm_equity": round(mtm_equity, 4),
        "realized_equity": round(realized_equity, 4),
        "hwm_new_realized": round(hwm_new_realized, 4),
        "hwm_old_mtm": round(hwm_old_mtm, 4),
        "dd_new_pct": round(dd_new_pct, 4),
        "dd_old_pct": round(dd_old_pct, 4),
        "would_old_trigger": dd_old_pct >= threshold_pct,
        "did_new_trigger": dd_new_pct >= threshold_pct,
        "threshold_pct": threshold_pct,
    }
    _append_jsonl(f"hwm_track_{_today_utc()}.jsonl", record)


# ---------------------------------------------------------------------------
# Fix 3: halt events — record what would have been force-flattened
# ---------------------------------------------------------------------------

def log_halt_event(
    trigger_reason: str,
    hwm_at_trigger: float,
    equity_at_trigger: float,
    open_positions: dict,
    marks_at_trigger: dict,
) -> None:
    """Emit a halt-trigger event. Must be called when CB fires.

    `open_positions` and `marks_at_trigger` capture the counterfactual:
    under old halt logic these would have been force-flattened. Now they
    ride. Comparing entry mark to eventual exit mark = avoided cost.
    """
    record = {
        "ts": int(time.time() * 1000),
        "trigger_reason": trigger_reason,
        "hwm_at_trigger": round(hwm_at_trigger, 4),
        "equity_at_trigger": round(equity_at_trigger, 4),
        "open_positions": {k: round(v, 4) for k, v in open_positions.items()},
        "marks_at_trigger": {k: round(v, 6) for k, v in marks_at_trigger.items()},
    }
    _append_jsonl(f"halt_events_{_today_utc()}.jsonl", record)


# ---------------------------------------------------------------------------
# Fix 4: auto-reset cooldown clear events
# ---------------------------------------------------------------------------

def log_cooldown_event(
    ts_trigger_ms: int,
    ts_cleared_ms: int,
    cooldown_duration_sec: float,
    dd_at_clear_pct: float,
) -> None:
    """Emit when the auto-reset cooldown clears the kill flag."""
    record = {
        "ts_trigger": ts_trigger_ms,
        "ts_cleared": ts_cleared_ms,
        "cooldown_duration_sec": round(cooldown_duration_sec, 1),
        "dd_at_clear_pct": round(dd_at_clear_pct, 4),
    }
    _append_jsonl(f"cooldown_events_{_today_utc()}.jsonl", record)


# ---------------------------------------------------------------------------
# Fix 6: BTC paired maker/taker observation (per PLANNED_FIXES.md)
# ---------------------------------------------------------------------------

def log_btc_paired_trade(
    signal_ts_ms: int,
    signal_size_usd: float,
    taker_fill_px: Optional[float],
    taker_fill_time_ms: Optional[int],
    taker_fee_usd: float,
    maker_limit_px: Optional[float],
    maker_fill_px: Optional[float],
    maker_fill_time_ms: Optional[int],
    maker_fallback: bool,
    maker_fallback_penalty_bps: float,
    maker_fee_usd: float,
    realized_vol_15m_at_signal_bps: float,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    spread_bps: Optional[float] = None,
    skipped_reason: Optional[str] = None,
) -> None:
    """Emit a paired (maker, taker) BTC trade observation.

    One record per BTC signal during pilot. Lets us measure the per-trade
    delta between maker and taker execution under matched conditions.

    Schema matches PLANNED_FIXES.md Fix 6 logging spec, with extra market
    context fields (bid/ask/spread) for regime-conditional analysis later.

    `skipped_reason` is set when maker leg was bypassed entirely (vol-aware
    skip, DD-approach disable, or invalid book).
    """
    record = {
        "signal_ts": signal_ts_ms,
        "signal_size": round(signal_size_usd, 4),
        "taker_fill_px": round(taker_fill_px, 4) if taker_fill_px else None,
        "taker_fill_time": taker_fill_time_ms,
        "taker_fee_usd": round(taker_fee_usd, 4),
        "maker_limit_px": round(maker_limit_px, 4) if maker_limit_px else None,
        "maker_fill_px": round(maker_fill_px, 4) if maker_fill_px else None,
        "maker_fill_time": maker_fill_time_ms,
        "maker_fallback_bool": maker_fallback,
        "maker_fallback_penalty_bps": round(maker_fallback_penalty_bps, 4),
        "maker_fee_usd": round(maker_fee_usd, 4),
        "realized_vol_15m_at_signal": round(realized_vol_15m_at_signal_bps, 4),
        "bid": round(bid, 4) if bid else None,
        "ask": round(ask, 4) if ask else None,
        "spread_bps": round(spread_bps, 4) if spread_bps else None,
        "skipped_reason": skipped_reason,
    }
    _append_jsonl(f"maker_pilot_{_today_utc()}.jsonl", record)
