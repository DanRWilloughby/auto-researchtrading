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


def write_all(log_dir: Path, output_dir: Path) -> dict[str, Path]:
    """Build all monitoring JSON files. Returns map of name → path written.

    Skips writes that fail (fail-soft); returns only successful paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = _load_baseline(output_dir)

    builders = {
        "phase1_attribution.json": lambda: build_phase1_attribution(log_dir, baseline),
        "kill_switch_status.json": lambda: build_kill_switch_status(log_dir),
        "recent_events.json": lambda: build_recent_events(log_dir),
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
