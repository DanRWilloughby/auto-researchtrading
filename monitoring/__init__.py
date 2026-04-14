"""Monitoring package: per-fix attribution logging + dashboard JSON aggregators.

Modules:
    event_log: thin JSONL writer used by Fixes 1, 3, 4, 5 to emit attribution events
    aggregate: reads JSONL files, produces dashboard-consumable JSON (specced in PLANNED_FIXES.md Fix 7)

The logging is fail-soft: if writing fails, we log a warning and continue.
Trading must never fail because monitoring failed.
"""
from .event_log import (
    log_skip_event,
    log_hwm_tick,
    log_halt_event,
    log_cooldown_event,
    log_btc_paired_trade,
)

__all__ = [
    "log_skip_event",
    "log_hwm_tick",
    "log_halt_event",
    "log_cooldown_event",
    "log_btc_paired_trade",
]
