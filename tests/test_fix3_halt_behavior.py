"""Tests for Fix 3: when halted, allow CLOSE/REDUCE signals but block OPEN/SCALE-UP.

Background: pre-fix, when CB triggers, trader returns early without running
the strategy. Strategy can't generate close signals -> existing positions
stay open and lose more money during the halt. Paper instance has no halt
and naturally lets positions complete TP/SL — that's part of why paper
recovers from drawdowns and live doesn't.

Fix: when halted, allow signals that REDUCE absolute exposure (closing,
trimming, flattening). Block signals that INCREASE absolute exposure (new
positions, scale-ups, position flips that grow other side).
"""
import pytest
import os
import tempfile
from unittest.mock import MagicMock

from risk.manager import RiskManager
from risk.config import RiskConfig
from risk.alerts import AlertDispatch


def _make_risk_mgr(initial_equity=10000.0):
    """Construct a RiskManager via the from_config factory with a temp kill-flag dir."""
    tmpdir = tempfile.mkdtemp()
    config = RiskConfig()
    config.watchdog.kill_flag_file = os.path.join(tmpdir, "kill.flag")
    rm = RiskManager.from_config(config=config, initial_equity=initial_equity)
    # Replace alerts with mock to suppress logging during tests
    rm.alerts = MagicMock(spec=AlertDispatch)
    rm.alerts.circuit_breaker = MagicMock()
    rm.alerts.order_error = MagicMock()
    rm.alerts.warning = MagicMock()
    rm.circuit_breaker.alerts = rm.alerts
    return rm


def _force_halt(rm: RiskManager, reason="test halt"):
    """Manually trigger halt for testing."""
    rm.circuit_breaker.halt(reason, create_kill_flag=False)


class TestHaltedReducesAllowed:
    """When halted, signals that REDUCE absolute exposure must be allowed."""

    def test_close_long_position_allowed(self):
        """Halted state: closing a long (target=0) should be allowed."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": 3000.0},  # long $3000
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=0.0)
        assert verdict.allowed is True, f"Close should be allowed when halted. reason={verdict.reason}"

    def test_close_short_position_allowed(self):
        """Halted state: closing a short (target=0) should be allowed."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": -3000.0},  # short $3000
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=0.0)
        assert verdict.allowed is True

    def test_reduce_long_allowed(self):
        """Halted state: reducing long ($3000 -> $1500) should be allowed."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": 3000.0},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=1500.0)
        assert verdict.allowed is True

    def test_reduce_short_allowed(self):
        """Halted state: reducing short (-$3000 -> -$1500) should be allowed."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": -3000.0},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=-1500.0)
        assert verdict.allowed is True


class TestHaltedOpensBlocked:
    """When halted, signals that OPEN or SCALE UP positions must be blocked."""

    def test_open_new_long_blocked(self):
        """Halted state: opening from flat ($0 -> $3000) should be blocked."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={},  # flat
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=3000.0)
        assert verdict.allowed is False
        assert "halt" in verdict.reason.lower()

    def test_open_new_short_blocked(self):
        """Halted: opening short from flat should be blocked."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=-3000.0)
        assert verdict.allowed is False

    def test_scale_up_long_blocked(self):
        """Halted: increasing long ($3000 -> $5000) should be blocked."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": 3000.0},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=5000.0)
        assert verdict.allowed is False

    def test_scale_up_short_blocked(self):
        """Halted: increasing short (-$3000 -> -$5000) should be blocked."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": -3000.0},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        verdict = rm.check_signal(symbol="BTC", target_notional_usd=-5000.0)
        assert verdict.allowed is False


class TestHaltedFlipsBlocked:
    """Position flips (long to short or vice versa) should be blocked when halted.

    A flip = close + open simultaneously. The open portion is what we don't
    want to allow. Simplest rule: block if abs(target) > abs(current).
    """

    def test_long_to_short_blocked(self):
        """Halted: flipping long $3000 to short $-3000 should be blocked."""
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": 3000.0},
            realized_equity=10000.0,
        )
        _force_halt(rm)

        # abs(-3000) == abs(3000), so technically same magnitude.
        # Conservative: flips that go through zero with same magnitude are
        # allowed (close = reduce to 0). But if abs(target) > abs(current),
        # the short side is bigger than what was being closed -> block.
        # For abs(target) == abs(current) on a flip: equivalent to close + open
        # at same size — block to be safe.
        verdict = rm.check_signal(symbol="BTC", target_notional_usd=-3000.0)
        # Equal-magnitude flip = close $3000 + open $3000 short. Block.
        assert verdict.allowed is False


class TestNotHaltedNormalBehavior:
    """When not halted, all signals pass position-limit checks normally."""

    def test_unhalted_open_allowed(self):
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={},
            realized_equity=10000.0,
        )
        # NOT halted
        verdict = rm.check_signal(symbol="BTC", target_notional_usd=3000.0)
        assert verdict.allowed is True

    def test_unhalted_scale_up_allowed_within_limits(self):
        rm = _make_risk_mgr()
        rm.update_account_state(
            equity=10000.0,
            positions={"BTC": 3000.0},
            realized_equity=10000.0,
        )
        verdict = rm.check_signal(symbol="BTC", target_notional_usd=4000.0)
        assert verdict.allowed is True


class TestHaltReasonReporting:
    """Verdict should clearly report WHY a signal was blocked when halted."""

    def test_halted_block_reason_mentions_halt(self):
        rm = _make_risk_mgr()
        rm.update_account_state(equity=10000.0, positions={}, realized_equity=10000.0)
        _force_halt(rm, reason="phantom DD test")
        verdict = rm.check_signal("BTC", 3000.0)
        assert verdict.allowed is False
        assert verdict.reason is not None
        # Should reference both halt state and the blocked-action category
        reason_lower = verdict.reason.lower()
        assert "halt" in reason_lower or "open" in reason_lower or "scale" in reason_lower
