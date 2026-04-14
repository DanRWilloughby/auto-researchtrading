"""Tests for Fix 4: kill flag auto-resets after cooldown when conditions clear.

Background: pre-fix, kill flag persisted indefinitely until manual deletion.
Every cron fire during persistence logged 'manual kill flag detected' and
created a cascade of halt entries. On Apr 13/14, multiple halt-restart
cycles occurred because the flag stayed up across many bars.

Fix: after the CB writes the kill flag (last_breach_ts set), subsequent
ticks call try_auto_reset(). If cooldown elapsed AND current DD is below
threshold, the flag is removed and halt cleared. Re-breach during cooldown
resets the timer.

Conservative: only flags WE wrote get auto-cleared. Externally created
flags (e.g., manual operator action) are not auto-removed.
"""
import os
import time
import tempfile
from unittest.mock import MagicMock

from risk.circuit_breaker import CircuitBreaker
from risk.config import CircuitBreakerConfig, WatchdogConfig


def _make_cb(initial_equity=10000.0, max_dd_hw=10.0, cooldown_sec=2):
    """Build CircuitBreaker with short cooldown for fast testing."""
    tmpdir = tempfile.mkdtemp()
    kill_flag_path = os.path.join(tmpdir, "kill.flag")
    config = CircuitBreakerConfig(
        max_dd_from_high_water_pct=max_dd_hw,
        max_dd_24h_pct=15.0,
        max_loss_daily_usd=500.0,
        auto_reset_cooldown_sec=cooldown_sec,
    )
    watchdog = WatchdogConfig(kill_flag_file=kill_flag_path)
    alerts = MagicMock()
    cb = CircuitBreaker(
        config=config,
        watchdog_config=watchdog,
        alerts=alerts,
        initial_equity=initial_equity,
    )
    return cb


class TestAutoResetClears:
    """Cooldown elapsed + DD recovered -> kill flag cleared."""

    def test_basic_cooldown_clears(self):
        """Halt fires, cooldown elapses, DD recovered -> auto-reset."""
        cb = _make_cb(cooldown_sec=1)
        # Trigger halt via DD breach
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        cb.halt("DD test halt")
        assert cb.halted is True
        assert cb.kill_flag.exists()

        # Wait for cooldown
        time.sleep(1.1)

        # Now MTM has recovered (DD below threshold)
        reset = cb.try_auto_reset(mtm_equity=9700.0, realized_equity=10000.0)
        assert reset is True
        assert cb.halted is False
        assert not cb.kill_flag.exists()


class TestAutoResetDoesNotClear:
    """Various conditions where auto-reset should NOT fire."""

    def test_within_cooldown_does_not_clear(self):
        """Less than cooldown_sec since last breach -> no reset."""
        cb = _make_cb(cooldown_sec=10)
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        cb.halt("DD test")

        # No wait — try immediately
        reset = cb.try_auto_reset(mtm_equity=9900.0, realized_equity=10000.0)
        assert reset is False
        assert cb.halted is True
        assert cb.kill_flag.exists()

    def test_cooldown_elapsed_but_dd_still_high_does_not_clear(self):
        """Cooldown done but DD still above threshold -> no reset, timer resets."""
        cb = _make_cb(cooldown_sec=1, max_dd_hw=10.0)
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        cb.halt("DD test")
        time.sleep(1.1)

        # MTM still in DD (below 90% of HWM)
        reset = cb.try_auto_reset(mtm_equity=8900.0, realized_equity=10000.0)
        assert reset is False, "DD still 11%, must not clear"
        assert cb.halted is True
        assert cb.kill_flag.exists()

    def test_external_kill_flag_not_auto_cleared(self):
        """Kill flag created externally (no last_breach_ts) -> manual only."""
        cb = _make_cb(cooldown_sec=1)
        # Simulate someone touching the kill flag directly (e.g., operator)
        cb.kill_flag.touch()
        # CB notices it on check, halts, but last_breach_ts is from kill flag detection
        # — we want to NOT auto-clear external flags
        cb.check(mtm_equity=10000.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        # Wait past cooldown
        time.sleep(1.1)

        reset = cb.try_auto_reset(mtm_equity=10000.0, realized_equity=10000.0)
        assert reset is False, "External kill flag should NOT auto-reset"
        assert cb.kill_flag.exists()


class TestRebreachResetsTimer:
    """Re-breach during cooldown extends the cooldown."""

    def test_rebreach_extends_cooldown(self):
        """Breach during cooldown -> timer resets, must wait full cooldown again."""
        cb = _make_cb(cooldown_sec=2, max_dd_hw=10.0)

        # Initial breach
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        cb.halt("first DD")

        # Wait part of cooldown
        time.sleep(0.5)

        # Recovery, then immediate re-breach
        cb.check(mtm_equity=9500.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)

        # Wait additional 1.5s — total elapsed since first breach > 2s, but
        # since re-breach < 2s ago, should NOT reset
        time.sleep(1.5)
        reset = cb.try_auto_reset(mtm_equity=9700.0, realized_equity=10000.0)
        assert reset is False, "Re-breach within cooldown should reset timer"

        # Wait the full cooldown from re-breach
        time.sleep(1.0)
        reset = cb.try_auto_reset(mtm_equity=9700.0, realized_equity=10000.0)
        assert reset is True, f"After full cooldown from last breach, should reset"


class TestCheckTracksBreachTime:
    """Every breach updates last_breach_ts."""

    def test_breach_sets_last_breach_ts(self):
        cb = _make_cb()
        assert cb.last_breach_ts is None
        cb.check(mtm_equity=8800.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        # Triggered breach: last_breach_ts should be set
        assert cb.last_breach_ts is not None
        assert cb.last_breach_ts > 0

    def test_no_breach_does_not_set_last_breach_ts(self):
        cb = _make_cb()
        cb.check(mtm_equity=10000.0, realized_equity=10000.0, daily_realized_pnl=0.0)
        assert cb.last_breach_ts is None
