"""
Watchdog — monitors live trading instances for health.

Runs as a separate cron job every 5 minutes. For each configured instance,
checks that the state file has been updated recently. If a trader is silent
longer than expected, dispatches Telegram alerts.

The cron-based architecture means the trader itself is not a long-running
daemon — each cron fire is a fresh Python process. So "is the trader alive"
really means "did the cron run fire and update state on schedule".

Checks:
  1. State file exists
  2. State file modification time is within expected window
  3. Latest equity_curve timestamp is within expected window

Alert escalation:
  - Level 1 (warning): stale 35-65 minutes (missed 1 cron fire)
  - Level 2 (error):   stale > 65 minutes (missed 2+ cron fires)
  - Level 3 (critical): stale > 120 minutes (missed 4+ cron fires)

Does NOT attempt automatic recovery. If cron is broken, human intervention is
needed. The watchdog just tells you fast.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Allow running as script from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from risk import RiskConfig
from risk.alerts import Alert, AlertDispatch, AlertType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s watchdog: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("watchdog")


@dataclass
class InstanceConfig:
    """An instance the watchdog should monitor."""
    name: str
    state_file: Path
    expected_interval_sec: int = 1800  # 30 minutes
    warn_threshold_sec: int = 35 * 60   # 35 min — missed 1 cron fire
    error_threshold_sec: int = 65 * 60  # 65 min — missed 2+
    critical_threshold_sec: int = 120 * 60  # 120 min — missed 4+


# Instances to monitor. Match the cron wrappers.
INSTANCES = [
    InstanceConfig(
        name="30m-concentrated/live",
        state_file=PROJECT_ROOT / "live" / "state" / "30m-concentrated_live_state.json",
    ),
    InstanceConfig(
        name="30m-concentrated/paper-cb-early",
        state_file=PROJECT_ROOT / "live" / "state" / "30m-concentrated_paper-cb-early_state.json",
    ),
]


@dataclass
class HealthReport:
    instance: str
    state_exists: bool
    state_age_sec: float = 0.0
    last_equity_age_sec: float = 0.0
    latest_equity: float = 0.0
    severity: str = "ok"  # ok | warn | error | critical
    message: str = ""


def check_instance(inst: InstanceConfig) -> HealthReport:
    """Check a single instance's health."""
    report = HealthReport(instance=inst.name, state_exists=False)

    if not inst.state_file.exists():
        report.severity = "error"
        report.message = f"state file missing: {inst.state_file}"
        return report

    report.state_exists = True
    now = time.time()

    # File mtime (when trader last wrote it)
    try:
        mtime = inst.state_file.stat().st_mtime
        report.state_age_sec = now - mtime
    except Exception as e:
        report.severity = "error"
        report.message = f"failed to stat state file: {e}"
        return report

    # Equity curve latest timestamp (authoritative "when did the trader last tick")
    try:
        with open(inst.state_file) as f:
            state = json.load(f)
        ec = state.get("equity_curve", [])
        if ec:
            latest_ts_ms = ec[-1].get("ts", 0)
            report.last_equity_age_sec = now - (latest_ts_ms / 1000)
            report.latest_equity = ec[-1].get("equity", 0)
    except Exception as e:
        logger.warning("Failed to parse state file: %s", e)
        # Fall back to mtime check only
        report.last_equity_age_sec = report.state_age_sec

    # Use the older of the two age signals for severity (conservative)
    age = max(report.state_age_sec, report.last_equity_age_sec)

    if age >= inst.critical_threshold_sec:
        report.severity = "critical"
        report.message = (
            f"no activity for {age/60:.0f} minutes — missed ~{int(age / inst.expected_interval_sec)} cron fires"
        )
    elif age >= inst.error_threshold_sec:
        report.severity = "error"
        report.message = (
            f"stale {age/60:.0f} min — missed 2+ cron fires, investigate immediately"
        )
    elif age >= inst.warn_threshold_sec:
        report.severity = "warn"
        report.message = (
            f"stale {age/60:.0f} min — missed last cron fire"
        )
    else:
        report.severity = "ok"
        report.message = f"healthy ({age/60:.1f} min since last tick)"

    return report


def dispatch_alert(alerts: AlertDispatch, reports: list[HealthReport]) -> None:
    """Dispatch consolidated alert for any unhealthy instances."""
    unhealthy = [r for r in reports if r.severity != "ok"]
    if not unhealthy:
        return

    # Find worst severity
    severity_rank = {"ok": 0, "warn": 1, "error": 2, "critical": 3}
    worst = max(unhealthy, key=lambda r: severity_rank.get(r.severity, 0))

    # Build alert
    alert_type = {
        "warn": AlertType.WARNING,
        "error": AlertType.WARNING,
        "critical": AlertType.CIRCUIT_BREAKER,  # escalate to critical
    }.get(worst.severity, AlertType.WARNING)

    summary = f"WATCHDOG {worst.severity.upper()}: {len(unhealthy)}/{len(reports)} instance(s) unhealthy"

    data = {}
    for r in unhealthy:
        data[r.instance] = f"[{r.severity}] {r.message}"

    alerts.dispatch(Alert(type=alert_type, message=summary, data=data))


def main():
    # Use --dry-alert to skip Telegram (for local testing)
    dry = "--dry-alert" in sys.argv

    config = RiskConfig.from_yaml()
    alerts = AlertDispatch(config.alerts, log_dir=PROJECT_ROOT)

    if dry:
        alerts.config.telegram_enabled = False
        logger.info("--dry-alert: Telegram dispatch disabled")

    logger.info("Checking %d instance(s)...", len(INSTANCES))

    reports = []
    for inst in INSTANCES:
        report = check_instance(inst)
        reports.append(report)
        icon = {"ok": "✅", "warn": "⚠️", "error": "❌", "critical": "🚨"}.get(report.severity, "?")
        logger.info(
            "%s %s: %s (equity=$%s)",
            icon,
            report.instance,
            report.message,
            f"{report.latest_equity:,.2f}" if report.latest_equity else "?",
        )

    dispatch_alert(alerts, reports)

    # Exit code: 0 if all healthy, 1 if any unhealthy
    any_unhealthy = any(r.severity != "ok" for r in reports)
    sys.exit(1 if any_unhealthy else 0)


if __name__ == "__main__":
    main()
