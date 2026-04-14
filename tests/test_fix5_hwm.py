"""Tests for Fix 5: HWM ratchets on realized equity, DD checks use MTM equity.

Background: the original CircuitBreaker.update_equity() ratcheted high-water
on whatever equity value was passed in. live/trader.py passes
`equity = cash + unrealized_sum` — a mark-to-market value that includes
unrealized PnL. During order settlement, this MTM briefly inflates (the
position is still open with unrealized gain at the moment the trader queries
state, but the close fill hasn't settled yet). The transient peak gets
locked into HWM. Subsequent DD checks measure against the phantom peak.

Both Apr 13 18:44 and Apr 14 09:44 cascades on $10K live were caused by
this. Fix: HWM ratchets on REALIZED equity only. DD check still uses MTM
(so unrealized losses contribute to drawdown — preserves tail-risk
protection asymmetry).
"""
import pytest
import os
import tempfile
from unittest.mock import MagicMock

from risk.circuit_breaker import CircuitBreaker
from risk.config import CircuitBreakerConfig, WatchdogConfig


def _make_cb(initial_equity=10000.0, max_dd_hw=10.0, max_dd_24h=15.0, max_loss=500.0):
    """Build a CircuitBreaker with a temp kill-flag directory and mocked alerts."""
    tmpdir = tempfile.mkdtemp()
    kill_flag_path = os.path.join(tmpdir, "kill.flag")
    config = CircuitBreakerConfig(
        max_dd_from_high_water_pct=max_dd_hw,
        max_dd_24h_pct=max_dd_24h,
        max_loss_daily_usd=max_loss,
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


class TestHWMRachetsOnRealizedOnly:
    """The core fix: high-water only updates from realized_equity, ignores MTM spikes."""

    def test_mtm_spike_does_not_inflate_hwm(self):
        """Unrealized gain spike should NOT ratchet HWM."""
        cb = _make_cb(initial_equity=10000.0)
        # Position spikes: realized stays at 10000 (no fills closed yet), MTM goes to 10800
        cb.update_equity(mtm_equity=10800.0, realized_equity=10000.0)
        assert cb.high_water == 10000.0, "HWM should NOT ratchet on transient MTM gain"

    def test_realized_gain_ratchets_hwm(self):
        """When position closes profitably, realized_equity rises and HWM follows."""
        cb = _make_cb(initial_equity=10000.0)
        cb.update_equity(mtm_equity=10500.0, realized_equity=10500.0)
        assert cb.high_water == 10500.0

    def test_apr_14_cascade_scenario(self):
        """Replay the Apr 14 09:44 cascade pattern: phantom peak, then real drawdown.

        Pre-fix behavior would have ratcheted HWM to ~$10880 from a transient
        MTM peak, then triggered the CB when MTM dropped to $10128 (5.65% DD).
        Post-fix: HWM only ratchets on realized_equity which never reached $10880.
        """
        cb = _make_cb(initial_equity=10000.0, max_dd_24h=5.0)

        # Tick 1: position is open with +$880 unrealized, but realized hasn't moved
        # (the close order hasn't settled yet). Pre-fix bug captures this peak.
        cb.update_equity(mtm_equity=10880.0, realized_equity=10000.0)

        # Tick 2: close settles. Realized = +$200 (some of the gain stuck), unrealized = 0
        cb.update_equity(mtm_equity=10200.0, realized_equity=10200.0)

        # Tick 3: subsequent normal pullback. MTM drops to 10128.
        # Pre-fix: dd from $10880 phantom = 6.9% -> CB fires at 5% threshold!
        # Post-fix: HWM = $10200 (realized peak), dd = 0.7% -> no trigger.
        cb.update_equity(mtm_equity=10128.0, realized_equity=10200.0)
        should_halt, reason = cb.check(
            mtm_equity=10128.0, realized_equity=10200.0, daily_realized_pnl=128.0
        )
        assert should_halt is False, (
            f"Phantom-peak cascade should NOT trigger after fix. Got reason: {reason}"
        )
        assert cb.high_water == 10200.0, "HWM should track realized peak only"

    def test_unrealized_loss_does_not_pull_down_hwm(self):
        """Unrealized losses don't reduce HWM (HWM is monotonic non-decreasing)."""
        cb = _make_cb(initial_equity=10000.0)
        cb.update_equity(mtm_equity=10500.0, realized_equity=10500.0)  # HWM = 10500
        cb.update_equity(mtm_equity=9800.0, realized_equity=10500.0)  # unrealized -700
        assert cb.high_water == 10500.0, "HWM stays at peak even when MTM drops"


class TestDDChecksUseMTM:
    """The complementary asymmetry: DD ratio uses MTM so unrealized losses count."""

    def test_unrealized_loss_triggers_dd(self):
        """Big unrealized loss should still trigger DD even if no realized loss."""
        cb = _make_cb(initial_equity=10000.0, max_dd_hw=10.0)
        # HWM stays at 10000 (no realized gains). MTM drops to 8900 from unrealized losses.
        # DD from HWM = 11% > 10% threshold -> should trigger.
        should_halt, reason = cb.check(
            mtm_equity=8900.0, realized_equity=10000.0, daily_realized_pnl=0.0
        )
        assert should_halt is True
        assert "drawdown" in reason.lower() or "high-water" in reason.lower()

    def test_unrealized_gain_does_not_negative_dd(self):
        """Unrealized gain (MTM > HWM) should NOT trigger; DD is just <= 0."""
        cb = _make_cb(initial_equity=10000.0)
        should_halt, reason = cb.check(
            mtm_equity=10500.0, realized_equity=10000.0, daily_realized_pnl=0.0
        )
        assert should_halt is False, f"MTM above HWM should be safe. reason={reason}"

    def test_realized_loss_triggers_daily_check(self):
        """Realized loss exceeding daily threshold triggers regardless of MTM."""
        cb = _make_cb(initial_equity=10000.0, max_loss=500.0)
        should_halt, reason = cb.check(
            mtm_equity=9400.0, realized_equity=9400.0, daily_realized_pnl=-600.0
        )
        assert should_halt is True
        assert "daily" in reason.lower()


class Test24hPeakUsesRealized:
    """Rolling 24h peak should also use realized equity, not MTM."""

    def test_24h_peak_ignores_mtm_spike(self):
        """Rolling 24h peak from MTM spike should not enable spurious 24h DD trigger."""
        cb = _make_cb(initial_equity=10000.0, max_dd_24h=5.0)
        # MTM spike of $880, but realized stays flat
        cb.update_equity(mtm_equity=10880.0, realized_equity=10000.0)
        # Subsequent normal MTM pullback
        cb.update_equity(mtm_equity=10100.0, realized_equity=10000.0)
        should_halt, reason = cb.check(
            mtm_equity=10100.0, realized_equity=10000.0, daily_realized_pnl=0.0
        )
        # Pre-fix: 24h peak captures 10880 spike, DD = 7.2% -> trigger
        # Post-fix: 24h peak from realized = 10000, DD = 0% -> no trigger
        assert should_halt is False, f"24h peak should ignore MTM spike. reason={reason}"


class TestKillFlag:
    """Manual kill flag still triggers (Fix 5 doesn't change this)."""

    def test_kill_flag_triggers_halt(self):
        cb = _make_cb()
        cb.kill_flag.touch()
        should_halt, reason = cb.check(
            mtm_equity=10000.0, realized_equity=10000.0, daily_realized_pnl=0.0
        )
        assert should_halt is True
        assert "kill flag" in reason.lower()


class TestBackwardCompatibility:
    """Old-style single-arg update_equity() should still work via shim."""

    def test_legacy_single_arg_treats_as_realized(self):
        """If old code calls update_equity(value), we treat it conservatively as realized."""
        cb = _make_cb(initial_equity=10000.0)
        # Old API
        cb.update_equity(10500.0)
        assert cb.high_water == 10500.0  # treated as realized

    def test_legacy_check_single_arg(self):
        """Old-style check(equity, daily_pnl) should still function."""
        cb = _make_cb(initial_equity=10000.0, max_dd_hw=10.0)
        # Old caller passes only equity (no separate realized arg)
        should_halt, reason = cb.check(current_equity=8800.0, daily_realized_pnl=0.0)
        # 12% DD from $10000 HWM -> trigger
        assert should_halt is True
