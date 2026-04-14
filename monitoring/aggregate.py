"""Aggregate per-fix JSONL event logs into dashboard-consumable JSON files.

Reads from live/logs/{skip_events,hwm_track,halt_events,cooldown_events}_*.jsonl
and writes to monitoring/{phase1_attribution,kill_switch_status,recent_events}.json
following the schemas in PLANNED_FIXES.md Fix 7.

Run via cron or invoked at end of trader tick. Cheap (parses small JSONL files).

CLI:
    uv run python -m monitoring.aggregate
        — produces all monitoring JSONs in monitoring/ at repo root
    uv run python -m monitoring.aggregate --output-dir /path/to/dashboard/data
        — writes to a custom location (e.g., dashboard sync target)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = REPO_ROOT / "live" / "logs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "monitoring"


def _read_jsonl_glob(log_dir: Path, prefix: str) -> list[dict]:
    """Read all records from {prefix}_*.jsonl files in log_dir, sorted by ts."""
    pattern = str(log_dir / f"{prefix}_*.jsonl")
    files = sorted(glob.glob(pattern))
    records: list[dict] = []
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # skip malformed
        except OSError:
            continue
    records.sort(key=lambda r: r.get("ts", 0))
    return records


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_baseline(output_dir: Path) -> Optional[dict]:
    """Read pre-Phase-1 baseline if available."""
    p = output_dir / "baseline.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def build_phase1_attribution(
    log_dir: Path,
    baseline: Optional[dict] = None,
) -> dict:
    """Compute per-fix attribution from the JSONL logs.

    Returns dict matching schema in PLANNED_FIXES.md Fix 7
    monitoring/phase1_attribution.json.
    """
    skip_events = _read_jsonl_glob(log_dir, "skip_events")
    hwm_ticks = _read_jsonl_glob(log_dir, "hwm_track")
    halt_events = _read_jsonl_glob(log_dir, "halt_events")
    cooldown_events = _read_jsonl_glob(log_dir, "cooldown_events")

    # Fix 1: SKIP attribution
    fix_1 = {
        "skip_events_fired": len(skip_events),
        "fees_avoided_usd": round(sum(r.get("implied_fee_avoided_usd", 0) for r in skip_events), 2),
    }

    # Fix 5: HWM attribution
    phantom_triggers = [
        r for r in hwm_ticks
        if r.get("would_old_trigger") and not r.get("did_new_trigger")
    ]
    current_tick = hwm_ticks[-1] if hwm_ticks else {}
    fix_5 = {
        "ticks_logged": len(hwm_ticks),
        "hwm_new_current": current_tick.get("hwm_new_realized", 0.0),
        "hwm_old_current": current_tick.get("hwm_old_mtm", 0.0),
        "phantom_triggers_prevented": len(phantom_triggers),
    }

    # Fix 3: halt attribution (counterfactual flatten cost is hard to compute
    # in real time — for now just count events; full attribution requires
    # follow-up bar marks)
    fix_3 = {
        "cb_trigger_events_since_ship": len(halt_events),
    }

    # Fix 4: cooldown attribution
    fix_4 = {
        "auto_clear_events": len(cooldown_events),
        "avg_cooldown_duration_sec": round(
            sum(r.get("cooldown_duration_sec", 0) for r in cooldown_events) / max(1, len(cooldown_events)),
            1,
        ) if cooldown_events else 0.0,
    }

    # Days live
    if baseline and baseline.get("phase1_ship_date"):
        try:
            ship_dt = datetime.fromisoformat(baseline["phase1_ship_date"].replace("Z", "+00:00"))
            days_live = (datetime.now(timezone.utc) - ship_dt).total_seconds() / 86400
        except (ValueError, TypeError):
            days_live = 0.0
    else:
        days_live = 0.0

    # Per-day rate
    if days_live > 0:
        fix_1["per_day_rate_usd"] = round(fix_1["fees_avoided_usd"] / days_live, 2)
    else:
        fix_1["per_day_rate_usd"] = 0.0

    sum_attributed = fix_1["fees_avoided_usd"]  # fix_5/3/4 attribution is event count, not direct $

    return {
        "updated_at": _now_iso(),
        "phase1_ship_date": baseline.get("phase1_ship_date") if baseline else None,
        "days_live": round(days_live, 2),
        "fix_1_skip_bug": fix_1,
        "fix_5_hwm": fix_5,
        "fix_3_halt_behavior": fix_3,
        "fix_4_cooldown": fix_4,
        "sum_attributed_savings_usd": sum_attributed,
    }


def build_kill_switch_status(log_dir: Path) -> dict:
    """Build current kill-switch status snapshot. Conservative defaults if no data."""
    hwm_ticks = _read_jsonl_glob(log_dir, "hwm_track")
    current_tick = hwm_ticks[-1] if hwm_ticks else {}

    # HWM drift detection: the new HWM should ALWAYS be <= old HWM (it ratchets
    # only on realized, old ratchets on MTM which is a superset).
    # If new > old, something is wrong with the implementation.
    invariant_breaches = sum(
        1 for r in hwm_ticks
        if r.get("hwm_new_realized", 0) > r.get("hwm_old_mtm", 0)
    )

    switches = []

    # DD approach (using new HWM)
    dd_new = current_tick.get("dd_new_pct", 0.0)
    threshold = current_tick.get("threshold_pct", 10.0)
    switches.append({
        "name": "dd_approach",
        "status": "green" if dd_new < threshold * 0.5 else ("yellow" if dd_new < threshold * 0.8 else "red"),
        "current_value": f"{dd_new:.2f}%",
        "threshold": f"{threshold}%",
        "distance_to_trigger": f"{max(0.0, threshold - dd_new):.2f}pp",
    })

    # HWM drift detection invariant
    switches.append({
        "name": "hwm_drift_detection",
        "status": "green" if invariant_breaches == 0 else "red",
        "current_value": f"{invariant_breaches} ticks with hwm_new > hwm_old",
        "threshold": "0 (invariant)",
        "invariant_holds": invariant_breaches == 0,
    })

    return {
        "updated_at": _now_iso(),
        "switches": switches,
    }


def build_recent_events(log_dir: Path, max_events: int = 50) -> dict:
    """Roll up recent notable events into a single feed for the dashboard."""
    events: list[dict] = []

    for r in _read_jsonl_glob(log_dir, "skip_events"):
        events.append({
            "ts": r.get("ts"),
            "type": "skip_fired",
            "symbol": r.get("symbol"),
            "fee_avoided_usd": r.get("implied_fee_avoided_usd"),
        })
    for r in _read_jsonl_glob(log_dir, "hwm_track"):
        if r.get("would_old_trigger") and not r.get("did_new_trigger"):
            events.append({
                "ts": r.get("ts"),
                "type": "hwm_phantom_prevented",
                "dd_old_pct": r.get("dd_old_pct"),
                "dd_new_pct": r.get("dd_new_pct"),
            })
    for r in _read_jsonl_glob(log_dir, "halt_events"):
        events.append({
            "ts": r.get("ts"),
            "type": "halt_triggered",
            "reason": r.get("trigger_reason"),
        })
    for r in _read_jsonl_glob(log_dir, "cooldown_events"):
        events.append({
            "ts": r.get("ts_cleared"),
            "type": "cooldown_cleared",
            "duration_sec": r.get("cooldown_duration_sec"),
        })

    # Most recent first
    events.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return {
        "updated_at": _now_iso(),
        "events": events[:max_events],
    }


def build_phase2_maker_pilot(log_dir: Path) -> dict:
    """Aggregate Fix 6 BTC paired maker/taker observations.

    Computes fill rate, mean price improvement, fallback rate, and per-trade
    P&L delta from the maker_pilot_*.jsonl event log.
    """
    records = _read_jsonl_glob(log_dir, "maker_pilot")
    if not records:
        return {
            "updated_at": _now_iso(),
            "pilot_active": False,
            "paired_observations_total": 0,
        }

    # Filter to actual paired trades (excludes skipped_reason events with no maker leg)
    paired = [r for r in records if r.get("maker_fill_px") is not None]
    skipped = [r for r in records if r.get("skipped_reason")]
    fallback = [r for r in paired if r.get("maker_fallback_bool")]

    n = len(paired)
    fill_rate_pct = (n - len(fallback)) / n * 100 if n > 0 else 0.0
    fallback_rate_pct = len(fallback) / n * 100 if n > 0 else 0.0

    # Price improvement bps: (taker_fill - maker_fill) / taker_fill * 10000 for BUY,
    # negated for SELL. Without per-record side info, use absolute distance.
    improvements = []
    for r in paired:
        t = r.get("taker_fill_px")
        m = r.get("maker_fill_px")
        if t and m and t > 0:
            improvements.append(abs(t - m) / t * 10000.0)
    mean_improvement = sum(improvements) / len(improvements) if improvements else 0.0

    # Fallback penalty stats
    penalties = [r.get("maker_fallback_penalty_bps", 0.0) for r in fallback]
    p95_penalty = sorted(penalties)[int(len(penalties) * 0.95)] if penalties else 0.0
    max_penalty_usd = max(
        (abs(r.get("maker_fallback_penalty_bps", 0.0)) * r.get("signal_size", 0.0) / 10000.0)
        for r in fallback
    ) if fallback else 0.0

    # Fill time
    fill_times = []
    for r in paired:
        if r.get("maker_fill_time") and r.get("signal_ts"):
            fill_times.append((r["maker_fill_time"] - r["signal_ts"]) / 1000.0)
    mean_fill_time_sec = sum(fill_times) / len(fill_times) if fill_times else 0.0

    return {
        "updated_at": _now_iso(),
        "pilot_active": True,
        "paired_observations_total": n,
        "skipped_signals": len(skipped),
        "fill_rate_pct": round(fill_rate_pct, 2),
        "fallback_rate_pct": round(fallback_rate_pct, 2),
        "mean_fill_time_sec": round(mean_fill_time_sec, 1),
        "mean_price_improvement_bps": round(mean_improvement, 4),
        "p95_fallback_penalty_bps": round(p95_penalty, 4),
        "max_single_fallback_usd": round(max_penalty_usd, 2),
    }


def build_account_metrics(log_dir: Path) -> dict:
    """Aggregate account performance metrics from existing JSONL logs.

    Reads:
      - hwm_track_*.jsonl → per-tick mtm/realized equity → end-of-day cash + history
      - trades_*.jsonl    → per-trade fees → cumulative + daily breakdown

    Produces an account_metrics.json file the dashboard can render to show
    cash balance trend and fee accumulation over time.
    """
    from datetime import datetime, timezone

    hwm_ticks = _read_jsonl_glob(log_dir, "hwm_track")
    trades = _read_jsonl_glob(log_dir, "trades")

    # Filter to LIVE trades only (live fees are real $; paper fee_usd is 0)
    live_trades = [t for t in trades if not t.get("dry_run", True)]

    # --- Current snapshot ---
    current = {}
    if hwm_ticks:
        latest = hwm_ticks[-1]
        current["cash_balance_usd"] = latest.get("realized_equity", 0.0)
        current["mtm_equity_usd"] = latest.get("mtm_equity", 0.0)
        current["unrealized_pnl_usd"] = round(
            latest.get("mtm_equity", 0.0) - latest.get("realized_equity", 0.0), 2
        )
    else:
        current = {"cash_balance_usd": 0.0, "mtm_equity_usd": 0.0, "unrealized_pnl_usd": 0.0}

    # --- Cumulative fees ---
    cumulative_fees = sum(t.get("fee_usd", 0.0) for t in live_trades)

    # --- Per-day breakdown ---
    def date_of(ts_ms):
        if not ts_ms:
            return None
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    fees_by_date: dict[str, dict] = {}
    for t in live_trades:
        d = date_of(t.get("ts"))
        if not d:
            continue
        if d not in fees_by_date:
            fees_by_date[d] = {"fees_usd": 0.0, "trades": 0}
        fees_by_date[d]["fees_usd"] += t.get("fee_usd", 0.0)
        fees_by_date[d]["trades"] += 1

    # End-of-day cash balance from hwm ticks: for each date, last realized_equity seen
    cash_by_date: dict[str, float] = {}
    for tick in hwm_ticks:
        d = date_of(tick.get("ts"))
        if d:
            cash_by_date[d] = tick.get("realized_equity", 0.0)

    sorted_dates = sorted(set(list(fees_by_date.keys()) + list(cash_by_date.keys())))
    daily_fees_breakdown = []
    cash_balance_history = []
    for d in sorted_dates:
        if d in fees_by_date:
            daily_fees_breakdown.append({
                "date": d,
                "fees_usd": round(fees_by_date[d]["fees_usd"], 2),
                "trades": fees_by_date[d]["trades"],
            })
        if d in cash_by_date:
            cash_balance_history.append({
                "date": d,
                "end_of_day_cash_usd": round(cash_by_date[d], 2),
            })

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_fees = fees_by_date.get(today, {}).get("fees_usd", 0.0)

    last_7_days = sum(
        e["fees_usd"] for e in daily_fees_breakdown[-7:]
    )

    return {
        "updated_at": _now_iso(),
        "current": current,
        "fees": {
            "cumulative_paid_usd": round(cumulative_fees, 2),
            "today_paid_usd": round(today_fees, 2),
            "last_7_days_total_usd": round(last_7_days, 2),
            "daily_breakdown": daily_fees_breakdown[-30:],  # cap at 30 days
        },
        "cash_balance_history": cash_balance_history[-30:],
    }


def write_all(log_dir: Path, output_dir: Path) -> dict[str, Path]:
    """Build all monitoring JSON files. Returns map of name → path written.

    Skips writes that fail (fail-soft); returns only successful paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = _load_baseline(output_dir)

    builders = {
        "phase1_attribution.json": lambda: build_phase1_attribution(log_dir, baseline),
        "phase2_maker_pilot.json": lambda: build_phase2_maker_pilot(log_dir),
        "kill_switch_status.json": lambda: build_kill_switch_status(log_dir),
        "recent_events.json": lambda: build_recent_events(log_dir),
        "account_metrics.json": lambda: build_account_metrics(log_dir),
    }

    written: dict[str, Path] = {}
    for filename, builder in builders.items():
        try:
            data = builder()
            path = output_dir / filename
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            written[filename] = path
        except Exception as e:
            print(f"Warning: failed to write {filename}: {e}", file=sys.stderr)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate fix attribution JSONL logs into dashboard JSON files"
    )
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR),
                        help="Directory containing skip_events_*.jsonl etc. "
                             f"(default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help="Where to write the aggregated monitoring JSONs "
                             f"(default: {DEFAULT_OUTPUT_DIR})")
    args = parser.parse_args(argv)

    written = write_all(Path(args.log_dir), Path(args.output_dir))
    if not written:
        print("No monitoring files written.", file=sys.stderr)
        return 1
    for name, path in written.items():
        print(f"wrote {name} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
