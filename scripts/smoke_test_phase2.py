"""End-to-end smoke test for Phase 2 (Fix 6 — BTC paired maker/taker pilot).

Exercises:
  - Pure decision logic (vol-aware, DD-approach, fallback decisions, splitting)
  - State machine orchestration (run_limit_with_fallback) with mocked SDK callbacks
  - Paired-trade event logging
  - Phase 2 aggregator output

Does NOT touch live trading or place real orders. All CB SDK calls are mocked.

Run:
    uv run python scripts/smoke_test_phase2.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TMPDIR = Path(tempfile.mkdtemp(prefix="phase2_smoke_"))
_LOG_DIR = _TMPDIR / "logs"
_LOG_DIR.mkdir()
_OUT_DIR = _TMPDIR / "monitoring"
_OUT_DIR.mkdir()

from monitoring import event_log  # noqa: E402
event_log.set_log_dir(_LOG_DIR)

from exchanges.maker_logic import (  # noqa: E402
    should_disable_maker_for_dd,
    compute_realized_vol_bps,
    compute_vol_aware_timeout,
    compute_limit_price,
    should_fallback_to_taker,
    split_target_for_pilot,
    run_limit_with_fallback,
)
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
    print(f"Phase 2 smoke test started. Logs: {_LOG_DIR}")

    # ----- DD-approach disable -----
    step("DD-approach disable rule")
    assert_true(
        should_disable_maker_for_dd(2.0, 10.0, 20.0) is False,
        "Low DD (2%) does not disable maker",
    )
    assert_true(
        should_disable_maker_for_dd(8.5, 10.0, 20.0) is True,
        "DD within 80% of threshold disables maker",
    )

    # ----- Vol-aware timeout -----
    step("Vol-aware timeout selection")
    assert_true(
        compute_vol_aware_timeout(300, 10.0, 30.0, 50.0, 60) == 300,
        "Low vol → base timeout (300s)",
    )
    assert_true(
        compute_vol_aware_timeout(300, 35.0, 30.0, 50.0, 60) == 60,
        "High vol → shortened timeout (60s)",
    )
    assert_true(
        compute_vol_aware_timeout(300, 55.0, 30.0, 50.0, 60) is None,
        "Extreme vol → skip maker entirely",
    )

    # ----- Limit price selection -----
    step("Limit price selection")
    assert_true(
        compute_limit_price(74900.0, 75100.0, "BUY", "passive") == 74900.0,
        "Passive BUY = bid",
    )
    assert_true(
        compute_limit_price(74900.0, 75100.0, "SELL", "passive") == 75100.0,
        "Passive SELL = ask",
    )

    # ----- Fallback decision -----
    step("Adverse-move fallback decision")
    safe = should_fallback_to_taker(75000.0, 75000.0, "BUY", 20.0)
    assert_true(safe.proceed_to_taker is True, "Unchanged price → fallback safe")
    danger = should_fallback_to_taker(75000.0, 75500.0, "BUY", 20.0)
    assert_true(
        danger.proceed_to_taker is False,
        "67 bps adverse on BUY → no fallback (cancel)",
    )

    # ----- Order splitting -----
    step("Order splitting")
    taker, maker = split_target_for_pilot(3000.0, 0.0, 0.5)
    assert_true(taker == 1500.0 and maker == 3000.0, "50/50 split: taker=$1500, maker=$3000")

    # ----- Full state machine: maker fills immediately -----
    step("State machine: maker fills immediately")
    poll_idx = {"i": 0}
    seq = [("filled", 75000.0)]
    result = run_limit_with_fallback(
        side="BUY", limit_price=75000.0,
        timeout_sec=300, poll_interval_sec=1.0, fallback_max_adverse_bps=20.0,
        place_limit_fn=lambda: "ord-1",
        poll_status_fn=lambda _id: seq[min(poll_idx["i"], len(seq) - 1)],
        cancel_fn=lambda _id: True,
        fetch_current_price_fn=lambda: 75000.0,
        place_market_fn=lambda: (True, 75050.0),
        sleep_fn=lambda _: None,
    )
    assert_true(result.success and result.final_state == "filled_maker", "Maker filled immediately")

    # ----- State machine: timeout → safe fallback -----
    step("State machine: timeout with safe fallback")
    fast_clock = {"t": 0.0}
    def elapsed():
        fast_clock["t"] += 1.0
        return fast_clock["t"]
    result = run_limit_with_fallback(
        side="BUY", limit_price=75000.0,
        timeout_sec=2, poll_interval_sec=1.0, fallback_max_adverse_bps=20.0,
        place_limit_fn=lambda: "ord-2",
        poll_status_fn=lambda _id: ("open", None),
        cancel_fn=lambda _id: True,
        fetch_current_price_fn=lambda: 75050.0,  # 6.7 bps adverse, within tolerance
        place_market_fn=lambda: (True, 75055.0),
        sleep_fn=lambda _: None,
        elapsed_fn=elapsed,
    )
    assert_true(result.success and result.is_fallback, "Timeout → fallback to taker (price safe)")

    # ----- State machine: timeout with adverse move → cancel -----
    step("State machine: timeout with adverse move → cancel without fallback")
    fast_clock = {"t": 0.0}
    def elapsed2():
        fast_clock["t"] += 1.0
        return fast_clock["t"]
    result = run_limit_with_fallback(
        side="BUY", limit_price=75000.0,
        timeout_sec=2, poll_interval_sec=1.0, fallback_max_adverse_bps=20.0,
        place_limit_fn=lambda: "ord-3",
        poll_status_fn=lambda _id: ("open", None),
        cancel_fn=lambda _id: True,
        fetch_current_price_fn=lambda: 75500.0,  # 67 bps adverse
        place_market_fn=lambda: (True, 75500.0),
        sleep_fn=lambda _: None,
        elapsed_fn=elapsed2,
    )
    assert_true(
        not result.success and result.final_state == "cancelled",
        "Adverse move → cancel, no fallback",
    )

    # ----- Paired-trade event logging -----
    step("Paired-trade event logging")
    import time as _t
    now = int(_t.time() * 1000)
    event_log.log_btc_paired_trade(
        signal_ts_ms=now, signal_size_usd=3000.0,
        taker_fill_px=75050.0, taker_fill_time_ms=now + 100, taker_fee_usd=0.45,
        maker_limit_px=75000.0, maker_fill_px=75000.0, maker_fill_time_ms=now + 60000,
        maker_fallback=False, maker_fallback_penalty_bps=0.0, maker_fee_usd=0.38,
        realized_vol_15m_at_signal_bps=12.5,
        bid=75000.0, ask=75100.0, spread_bps=13.3,
    )
    pilot_files = list(_LOG_DIR.glob("maker_pilot_*.jsonl"))
    assert_true(len(pilot_files) == 1, "Paired trade JSONL written")
    with open(pilot_files[0]) as f:
        records = [json.loads(line) for line in f]
    assert_true(len(records) == 1, "1 paired observation recorded")
    assert_true(records[0]["maker_fallback_bool"] is False, "Fallback flag captured")

    # Add a fallback event for aggregator
    event_log.log_btc_paired_trade(
        signal_ts_ms=now + 1000, signal_size_usd=3000.0,
        taker_fill_px=75050.0, taker_fill_time_ms=now + 1100, taker_fee_usd=0.45,
        maker_limit_px=75000.0, maker_fill_px=75100.0, maker_fill_time_ms=now + 301000,
        maker_fallback=True, maker_fallback_penalty_bps=13.3, maker_fee_usd=0.45,
        realized_vol_15m_at_signal_bps=22.0,
    )

    # Add a skipped event
    event_log.log_btc_paired_trade(
        signal_ts_ms=now + 2000, signal_size_usd=3000.0,
        taker_fill_px=75050.0, taker_fill_time_ms=now + 2100, taker_fee_usd=0.45,
        maker_limit_px=None, maker_fill_px=None, maker_fill_time_ms=None,
        maker_fallback=False, maker_fallback_penalty_bps=0.0, maker_fee_usd=0.0,
        realized_vol_15m_at_signal_bps=55.0, skipped_reason="extreme_vol",
    )

    # ----- Aggregator -----
    step("Phase 2 aggregator")
    written = aggregate.write_all(log_dir=_LOG_DIR, output_dir=_OUT_DIR)
    assert_true("phase2_maker_pilot.json" in written, "phase2_maker_pilot.json written")
    with open(_OUT_DIR / "phase2_maker_pilot.json") as f:
        p2 = json.load(f)
    assert_true(p2["pilot_active"] is True, "Pilot active flag set")
    assert_true(p2["paired_observations_total"] == 2, "2 paired observations counted")
    assert_true(p2["skipped_signals"] == 1, "1 skipped signal counted")
    assert_true(p2["fill_rate_pct"] == 50.0, "Fill rate 50% (1 maker, 1 fallback)")
    assert_true(p2["fallback_rate_pct"] == 50.0, "Fallback rate 50%")

    print(f"\n{PASS} All Phase 2 smoke checks passed.")
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
        print(f"\n{FAIL} Phase 2 smoke test failed: {e}", file=sys.stderr)
        sys.exit(1)
