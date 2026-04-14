"""End-to-end smoke test for Phase 1 fixes.

Exercises the full code path of every fix using mocked exchange + alerts,
verifies the JSONL emitters write expected events, runs the aggregator,
and checks the produced JSON files match the expected schema.

Does NOT touch live trading or the VM. Pure local verification that
everything wires together correctly before deploy.

Run:
    uv run python scripts/smoke_test_phase1.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Configure logging dir to a temp location BEFORE importing fix modules
_TMPDIR = Path(tempfile.mkdtemp(prefix="phase1_smoke_"))
_LOG_DIR = _TMPDIR / "logs"
_LOG_DIR.mkdir()
_OUT_DIR = _TMPDIR / "monitoring"
_OUT_DIR.mkdir()

from monitoring import event_log  # noqa: E402
event_log.set_log_dir(_LOG_DIR)

from exchanges.coinbase_client import should_skip_order_decision  # noqa: E402
from risk.circuit_breaker import CircuitBreaker  # noqa: E402
from risk.config import CircuitBreakerConfig, WatchdogConfig  # noqa: E402
from risk.manager import RiskManager, _is_close_or_reduce  # noqa: E402
from risk.config import RiskConfig  # noqa: E402
from monitoring import aggregate  # noqa: E402


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
INFO = "\033[94mINFO\033[0m"


def step(name: str) -> None:
    print(f"\n{INFO} {name}")


def assert_true(cond: bool, msg: str) -> None:
    if cond:
        print(f"  {PASS} {msg}")
    else:
        print(f"  {FAIL} {msg}")
        raise AssertionError(msg)


def main() -> int:
    print(f"Smoke test started. Logs: {_LOG_DIR}, Output: {_OUT_DIR}")

    # ----- Fix 1: SKIP decision + emitter -----
    step("Fix 1: SKIP tolerance check")
    skip, reason = should_skip_order_decision(
        target_notional_usd=3100.0,
        current_notional_usd=3000.0,
        current_contracts=4,
        contract_size=0.01,
        current_price=75000.0,
        skip_tolerance_usd=200.0,
    )
    assert_true(skip is True, "Tiny delta below tolerance returns SKIP")
    assert_true("tolerance" in reason.lower() or "delta" in reason.lower(), "SKIP reason mentions tolerance")

    # Emit attribution event (would normally happen inside place_market_order)
    event_log.log_skip_event(
        symbol="BTC",
        target_notional_usd=3100.0,
        current_notional_usd=3000.0,
        delta_notional_usd=100.0,
        implied_fee_avoided_usd=0.03,
        tolerance_used_usd=200.0,
        skip_reason=reason,
    )
    skip_files = list(_LOG_DIR.glob("skip_events_*.jsonl"))
    assert_true(len(skip_files) == 1, f"SKIP event JSONL written ({len(skip_files)} file)")
    with open(skip_files[0]) as f:
        skip_records = [json.loads(line) for line in f]
    assert_true(len(skip_records) == 1, "1 SKIP event recorded")
    assert_true(skip_records[0]["symbol"] == "BTC", "Event symbol matches")

    # ----- Fix 5: HWM realized-only + DD asymmetry -----
    step("Fix 5: HWM realized-only ratcheting + dual-track logging")
    tmp_kill = _TMPDIR / "kill.flag"
    cb_config = CircuitBreakerConfig(
        max_dd_from_high_water_pct=10.0,
        max_dd_24h_pct=10.0,
        max_loss_daily_usd=500.0,
        auto_reset_cooldown_sec=2,
    )
    wd_config = WatchdogConfig(kill_flag_file=str(tmp_kill))
    cb = CircuitBreaker(cb_config, wd_config, alerts=MagicMock(), initial_equity=10000.0)

    # Apr 14 cascade replay scenario
    cb.update_equity(mtm_equity=10880.0, realized_equity=10000.0)
    assert_true(cb.high_water == 10000.0, "MTM spike does NOT inflate HWM")

    cb.update_equity(mtm_equity=10200.0, realized_equity=10200.0)
    assert_true(cb.high_water == 10200.0, "Realized gain ratchets HWM")

    should_halt, reason = cb.check(
        mtm_equity=10128.0, realized_equity=10200.0, daily_realized_pnl=128.0
    )
    assert_true(should_halt is False, f"Apr 14 cascade scenario does NOT trigger (got reason={reason})")

    hwm_files = list(_LOG_DIR.glob("hwm_track_*.jsonl"))
    assert_true(len(hwm_files) == 1, f"HWM track JSONL written")
    with open(hwm_files[0]) as f:
        hwm_records = [json.loads(line) for line in f]
    assert_true(len(hwm_records) >= 3, f"HWM ticks logged: {len(hwm_records)}")

    # ----- Fix 3: halt close-only behavior -----
    step("Fix 3: halt allows close/reduce, blocks open/scale-up")
    config = RiskConfig()
    config.watchdog.kill_flag_file = str(_TMPDIR / "kill_fix3.flag")
    rm = RiskManager.from_config(config=config, initial_equity=10000.0)

    rm.update_account_state(
        equity=10000.0,
        positions={"BTC": 3000.0},
        realized_equity=10000.0,
    )
    rm.circuit_breaker.halt("test halt", create_kill_flag=False)

    verdict_close = rm.check_signal(symbol="BTC", target_notional_usd=0.0)
    assert_true(verdict_close.allowed, "CLOSE signal allowed when halted")

    verdict_reduce = rm.check_signal(symbol="BTC", target_notional_usd=1500.0)
    assert_true(verdict_reduce.allowed, "REDUCE signal allowed when halted")

    verdict_scale = rm.check_signal(symbol="BTC", target_notional_usd=5000.0)
    assert_true(not verdict_scale.allowed, "SCALE-UP blocked when halted")

    verdict_flip = rm.check_signal(symbol="BTC", target_notional_usd=-3000.0)
    assert_true(not verdict_flip.allowed, "FLIP blocked when halted")

    # ----- Fix 4: auto-reset cooldown -----
    step("Fix 4: kill flag auto-resets after cooldown")
    cb.check(mtm_equity=8800.0, realized_equity=10200.0, daily_realized_pnl=0.0)  # breach
    cb.halt("Fix 4 test", create_kill_flag=True)
    assert_true(cb.kill_flag.exists(), "Kill flag written on halt")
    assert_true(cb.last_breach_ts is not None, "last_breach_ts set on breach")

    # Wait for cooldown
    time.sleep(2.5)
    reset = cb.try_auto_reset(mtm_equity=10100.0, realized_equity=10200.0)
    assert_true(reset is True, "Auto-reset cleared kill flag after cooldown + DD recovery")
    assert_true(not cb.kill_flag.exists(), "Kill flag deleted")

    cooldown_files = list(_LOG_DIR.glob("cooldown_events_*.jsonl"))
    assert_true(len(cooldown_files) == 1, "Cooldown event JSONL written")

    # ----- Aggregator end-to-end -----
    step("Aggregator: produces dashboard JSON files")
    written = aggregate.write_all(log_dir=_LOG_DIR, output_dir=_OUT_DIR)
    assert_true("phase1_attribution.json" in written, "phase1_attribution.json written")
    assert_true("kill_switch_status.json" in written, "kill_switch_status.json written")
    assert_true("recent_events.json" in written, "recent_events.json written")

    # Verify content
    with open(_OUT_DIR / "phase1_attribution.json") as f:
        attr = json.load(f)
    assert_true(attr["fix_1_skip_bug"]["skip_events_fired"] == 1, "Attribution shows 1 SKIP event")
    assert_true(attr["fix_1_skip_bug"]["fees_avoided_usd"] > 0, "Attribution shows fees avoided")
    assert_true(attr["fix_4_cooldown"]["auto_clear_events"] == 1, "Attribution shows 1 cooldown event")

    with open(_OUT_DIR / "kill_switch_status.json") as f:
        kill = json.load(f)
    assert_true(any(s["name"] == "dd_approach" for s in kill["switches"]), "dd_approach switch present")
    assert_true(any(s["name"] == "hwm_drift_detection" for s in kill["switches"]), "hwm_drift_detection present")

    with open(_OUT_DIR / "recent_events.json") as f:
        events = json.load(f)
    assert_true(len(events["events"]) > 0, f"Recent events feed populated ({len(events['events'])} events)")

    print(f"\n{PASS} All Phase 1 smoke checks passed.")
    print(f"\nGenerated artifacts:")
    print(f"  Logs: {_LOG_DIR}/")
    for f in sorted(_LOG_DIR.glob("*.jsonl")):
        print(f"    - {f.name}")
    print(f"  Monitoring JSONs: {_OUT_DIR}/")
    for f in sorted(_OUT_DIR.glob("*.json")):
        print(f"    - {f.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n{FAIL} Smoke test failed: {e}", file=sys.stderr)
        sys.exit(1)
