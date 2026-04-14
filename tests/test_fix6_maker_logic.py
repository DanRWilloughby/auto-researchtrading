"""Tests for exchanges/maker_logic.py — pure decision functions for Fix 6 maker pilot."""
import pytest

from exchanges.maker_logic import (
    should_disable_maker_for_dd,
    compute_realized_vol_bps,
    compute_vol_aware_timeout,
    compute_limit_price,
    should_fallback_to_taker,
    split_target_for_pilot,
    FallbackDecision,
)


# ---------------------------------------------------------------------------
# DD-approach disable
# ---------------------------------------------------------------------------

class TestShouldDisableMakerForDd:
    def test_low_dd_does_not_disable(self):
        # 2% DD, 10% threshold, 20% buffer → disable at 8%
        assert should_disable_maker_for_dd(2.0, 10.0, 20.0) is False

    def test_within_buffer_disables(self):
        # 8.5% DD, 10% threshold, 20% buffer → disable at 8%, 8.5 >= 8
        assert should_disable_maker_for_dd(8.5, 10.0, 20.0) is True

    def test_at_buffer_boundary_disables(self):
        # 8% DD, exactly at the disable threshold
        assert should_disable_maker_for_dd(8.0, 10.0, 20.0) is True

    def test_above_threshold_disables(self):
        # 11% DD — already past kill threshold, definitely disable
        assert should_disable_maker_for_dd(11.0, 10.0, 20.0) is True

    def test_zero_threshold_returns_false(self):
        # Defensive: bad config shouldn't crash
        assert should_disable_maker_for_dd(5.0, 0.0, 20.0) is False

    def test_negative_threshold_returns_false(self):
        assert should_disable_maker_for_dd(5.0, -10.0, 20.0) is False

    def test_zero_buffer_disables_only_at_threshold(self):
        # No buffer: disable only when DD reaches exact threshold
        assert should_disable_maker_for_dd(9.5, 10.0, 0.0) is False
        assert should_disable_maker_for_dd(10.0, 10.0, 0.0) is True


# ---------------------------------------------------------------------------
# Realized vol calculation
# ---------------------------------------------------------------------------

class TestComputeRealizedVolBps:
    def test_empty_returns_zero(self):
        assert compute_realized_vol_bps([]) == 0.0

    def test_too_few_prices_returns_zero(self):
        assert compute_realized_vol_bps([100.0, 101.0]) == 0.0

    def test_constant_price_zero_vol(self):
        # All identical prices → no variance → zero vol
        assert compute_realized_vol_bps([100.0] * 10) == 0.0

    def test_known_returns_compute_correctly(self):
        # Prices: 100, 101, 100 → returns: +1%, -1% → variance non-zero
        vol = compute_realized_vol_bps([100.0, 101.0, 100.0])
        assert vol > 0.0
        # Two opposite moves of ~1% → stdev ~ sqrt(2) * 100 bps roughly
        assert 100 < vol < 200

    def test_filters_invalid_prices(self):
        # None, negative, zero should be filtered out
        prices = [None, 100.0, -1.0, 101.0, 0.0, 100.5]
        # Valid: 100, 101, 100.5 → 3 prices, 2 returns, can compute
        vol = compute_realized_vol_bps(prices)
        assert vol > 0.0


# ---------------------------------------------------------------------------
# Vol-aware timeout
# ---------------------------------------------------------------------------

class TestComputeVolAwareTimeout:
    def test_low_vol_returns_base_timeout(self):
        result = compute_vol_aware_timeout(
            base_timeout_sec=300, current_vol_bps=10.0,
            high_vol_threshold_bps=30.0, extreme_vol_threshold_bps=50.0,
            high_vol_timeout_sec=60,
        )
        assert result == 300

    def test_high_vol_returns_short_timeout(self):
        result = compute_vol_aware_timeout(
            base_timeout_sec=300, current_vol_bps=35.0,
            high_vol_threshold_bps=30.0, extreme_vol_threshold_bps=50.0,
            high_vol_timeout_sec=60,
        )
        assert result == 60

    def test_extreme_vol_returns_none_skip_maker(self):
        result = compute_vol_aware_timeout(
            base_timeout_sec=300, current_vol_bps=55.0,
            high_vol_threshold_bps=30.0, extreme_vol_threshold_bps=50.0,
            high_vol_timeout_sec=60,
        )
        assert result is None

    def test_at_high_threshold_returns_short(self):
        # Inclusive boundary: vol == threshold triggers shorter timeout
        result = compute_vol_aware_timeout(300, 30.0, 30.0, 50.0, 60)
        assert result == 60

    def test_at_extreme_threshold_returns_none(self):
        result = compute_vol_aware_timeout(300, 50.0, 30.0, 50.0, 60)
        assert result is None


# ---------------------------------------------------------------------------
# Limit price selection
# ---------------------------------------------------------------------------

class TestComputeLimitPrice:
    def test_passive_buy_at_bid(self):
        assert compute_limit_price(74900.0, 75100.0, "BUY", "passive") == 74900.0

    def test_passive_sell_at_ask(self):
        assert compute_limit_price(74900.0, 75100.0, "SELL", "passive") == 75100.0

    def test_mid_returns_midpoint(self):
        result = compute_limit_price(74900.0, 75100.0, "BUY", "mid")
        assert result == 75000.0

    def test_aggressive_buy_at_ask(self):
        # Aggressive = cross to other side (effectively taker-like)
        assert compute_limit_price(74900.0, 75100.0, "BUY", "aggressive") == 75100.0

    def test_aggressive_sell_at_bid(self):
        assert compute_limit_price(74900.0, 75100.0, "SELL", "aggressive") == 74900.0

    def test_invalid_side_returns_none(self):
        assert compute_limit_price(74900.0, 75100.0, "INVALID", "passive") is None

    def test_invalid_aggressiveness_returns_none(self):
        assert compute_limit_price(74900.0, 75100.0, "BUY", "wild") is None

    def test_zero_bid_returns_none(self):
        assert compute_limit_price(0.0, 75100.0, "BUY", "passive") is None

    def test_inverted_book_returns_none(self):
        # ask < bid is invalid
        assert compute_limit_price(75100.0, 74900.0, "BUY", "passive") is None

    def test_case_insensitive_side(self):
        assert compute_limit_price(74900.0, 75100.0, "buy", "passive") == 74900.0
        assert compute_limit_price(74900.0, 75100.0, "sell", "passive") == 75100.0


# ---------------------------------------------------------------------------
# Adverse-move fallback decision
# ---------------------------------------------------------------------------

class TestShouldFallbackToTaker:
    def test_no_move_falls_back(self):
        # Price unchanged → safe to fall back
        d = should_fallback_to_taker(75000.0, 75000.0, "BUY", max_adverse_bps=20.0)
        assert d.proceed_to_taker is True
        assert d.adverse_move_bps == 0.0

    def test_buy_favorable_move_falls_back(self):
        # Price went DOWN after BUY limit placed → favorable for buyer (smaller adverse_move)
        d = should_fallback_to_taker(75000.0, 74000.0, "BUY", max_adverse_bps=20.0)
        # adverse_move is negative for BUY when price drops
        assert d.proceed_to_taker is True
        assert d.adverse_move_bps < 0  # negative = favorable

    def test_buy_small_adverse_move_falls_back(self):
        # Price up 10 bps → within 20 bps tolerance
        d = should_fallback_to_taker(75000.0, 75075.0, "BUY", max_adverse_bps=20.0)
        # 75 / 75000 * 10000 = 10 bps
        assert d.proceed_to_taker is True
        assert 9.5 < d.adverse_move_bps < 10.5

    def test_buy_large_adverse_move_does_not_fall_back(self):
        # Price up 50 bps → exceeds 20 bps threshold
        d = should_fallback_to_taker(75000.0, 75375.0, "BUY", max_adverse_bps=20.0)
        # 375/75000 * 10000 = 50 bps adverse
        assert d.proceed_to_taker is False
        assert d.adverse_move_bps > 20.0
        assert "adverse" in d.reason.lower()

    def test_sell_adverse_means_price_dropped(self):
        # SELL: price went DOWN (we receive less) → adverse
        d = should_fallback_to_taker(75000.0, 74625.0, "SELL", max_adverse_bps=20.0)
        # Adverse move = (75000 - 74625) / 75000 * 10000 = 50 bps
        assert d.proceed_to_taker is False
        assert d.adverse_move_bps > 20.0

    def test_sell_favorable_means_price_rose(self):
        d = should_fallback_to_taker(75000.0, 75100.0, "SELL", max_adverse_bps=20.0)
        # Favorable: price up after sell limit means we'd have done better with market
        # adverse_move = (75000 - 75100) / 75000 * 10000 = -13.33 bps (favorable)
        assert d.proceed_to_taker is True
        assert d.adverse_move_bps < 0

    def test_invalid_prices_does_not_fall_back(self):
        d = should_fallback_to_taker(0.0, 75000.0, "BUY", 20.0)
        assert d.proceed_to_taker is False
        d = should_fallback_to_taker(75000.0, -1.0, "BUY", 20.0)
        assert d.proceed_to_taker is False

    def test_invalid_side_does_not_fall_back(self):
        d = should_fallback_to_taker(75000.0, 75000.0, "WTF", 20.0)
        assert d.proceed_to_taker is False


# ---------------------------------------------------------------------------
# Order splitting
# ---------------------------------------------------------------------------

class TestSplitTargetForPilot:
    def test_default_split_50_50(self):
        # From flat to $3000 long, 50/50 split
        taker, maker = split_target_for_pilot(
            target_notional_usd=3000.0,
            current_notional_usd=0.0,
            maker_fraction=0.5,
        )
        # Taker advances by half the delta = $1500
        assert taker == 1500.0
        # Maker completes to full target
        assert maker == 3000.0

    def test_short_side_split(self):
        # From flat to $3000 short
        taker, maker = split_target_for_pilot(
            target_notional_usd=-3000.0,
            current_notional_usd=0.0,
            maker_fraction=0.5,
        )
        assert taker == -1500.0
        assert maker == -3000.0

    def test_partial_close(self):
        # From $3000 long to $1500 long (close half)
        taker, maker = split_target_for_pilot(
            target_notional_usd=1500.0,
            current_notional_usd=3000.0,
            maker_fraction=0.5,
        )
        # Delta = -1500. Taker advances by half = -750 → taker_target = $2250
        assert taker == 2250.0
        # Maker completes to $1500
        assert maker == 1500.0

    def test_third_split(self):
        # 1/3 maker, 2/3 taker
        taker, maker = split_target_for_pilot(
            target_notional_usd=3000.0,
            current_notional_usd=0.0,
            maker_fraction=1.0 / 3.0,
        )
        # Taker advances by 2/3 = $2000
        assert taker == pytest.approx(2000.0)
        assert maker == 3000.0

    def test_out_of_range_fraction_returns_full_target(self):
        # maker_fraction out of (0, 1) → safer default: both legs target full
        taker, maker = split_target_for_pilot(3000.0, 0.0, maker_fraction=0.0)
        assert taker == 3000.0
        assert maker == 3000.0
        taker, maker = split_target_for_pilot(3000.0, 0.0, maker_fraction=1.0)
        assert taker == 3000.0
        assert maker == 3000.0
        taker, maker = split_target_for_pilot(3000.0, 0.0, maker_fraction=1.5)
        assert taker == 3000.0
        assert maker == 3000.0

    def test_no_change_returns_current(self):
        # target == current, no work
        taker, maker = split_target_for_pilot(3000.0, 3000.0, maker_fraction=0.5)
        assert taker == 3000.0
        assert maker == 3000.0


# ---------------------------------------------------------------------------
# State machine: run_limit_with_fallback orchestration
# ---------------------------------------------------------------------------

from exchanges.maker_logic import run_limit_with_fallback, LimitFillResult


def _make_callbacks(
    *,
    place_returns="ord-123",
    poll_sequence=None,    # list of (status, fill_price) tuples returned in order
    cancel_returns=True,
    current_price=75000.0,
    market_returns=(True, 75000.0),
):
    """Build a set of mock callbacks for run_limit_with_fallback testing."""
    poll_sequence = list(poll_sequence or [("open", None)])
    poll_calls = {"i": 0}

    def place_limit_fn():
        return place_returns

    def poll_status_fn(order_id):
        i = poll_calls["i"]
        poll_calls["i"] = min(i + 1, len(poll_sequence) - 1)
        return poll_sequence[i]

    def cancel_fn(order_id):
        return cancel_returns

    def fetch_current_price_fn():
        return current_price

    def place_market_fn():
        return market_returns

    return {
        "place_limit_fn": place_limit_fn,
        "poll_status_fn": poll_status_fn,
        "cancel_fn": cancel_fn,
        "fetch_current_price_fn": fetch_current_price_fn,
        "place_market_fn": place_market_fn,
        "sleep_fn": lambda _: None,  # don't actually sleep in tests
    }


def _fake_clock():
    """Returns an elapsed_fn that advances by 1 second per call (fast tests)."""
    state = {"t": 0.0}
    def elapsed():
        state["t"] += 1.0
        return state["t"]
    return elapsed


class TestRunLimitWithFallback:
    def test_immediate_fill_returns_filled_maker(self):
        cbs = _make_callbacks(poll_sequence=[("filled", 75000.0)])
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=300, poll_interval_sec=5.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is True
        assert result.final_state == "filled_maker"
        assert result.fill_price == 75000.0
        assert result.is_fallback is False

    def test_open_then_filled_returns_filled_maker(self):
        cbs = _make_callbacks(poll_sequence=[
            ("open", None), ("open", None), ("filled", 74999.0)
        ])
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=300, poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is True
        assert result.final_state == "filled_maker"

    def test_timeout_with_safe_price_falls_back_to_taker(self):
        # Always "open" → timeout. Current price unchanged → safe fallback.
        cbs = _make_callbacks(
            poll_sequence=[("open", None)],
            current_price=75000.0,
            market_returns=(True, 75002.0),
        )
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=3,  # timeout fast
            poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is True
        assert result.final_state == "filled_fallback"
        assert result.is_fallback is True
        assert result.fill_price == 75002.0

    def test_timeout_with_adverse_move_does_not_fall_back(self):
        # Open → timeout. Current price moved 50 bps adverse on BUY → cancel without fallback.
        cbs = _make_callbacks(
            poll_sequence=[("open", None)],
            current_price=75375.0,  # +50 bps from 75000
        )
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=3,
            poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is False
        assert result.final_state == "cancelled"
        assert result.fallback_decision is not None
        assert result.fallback_decision.proceed_to_taker is False
        assert result.fallback_decision.adverse_move_bps > 20.0

    def test_place_limit_failure_returns_error(self):
        cbs = _make_callbacks(place_returns=None)
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=300, poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is False
        assert result.final_state == "error"

    def test_external_cancel_does_not_fall_back(self):
        cbs = _make_callbacks(poll_sequence=[("cancelled", None)])
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=300, poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is False
        assert result.final_state == "cancelled"
        assert result.is_fallback is False

    def test_market_fallback_failure_reports_error(self):
        cbs = _make_callbacks(
            poll_sequence=[("open", None)],
            current_price=75000.0,
            market_returns=(False, None),  # market fill also fails
        )
        result = run_limit_with_fallback(
            side="BUY", limit_price=75000.0,
            timeout_sec=3, poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is False
        assert result.final_state == "error"
        assert result.is_fallback is True  # we tried, it failed

    def test_sell_side_adverse_move(self):
        # SELL limit at 75000. Current price dropped to 74625 → 50 bps adverse for SELL → cancel
        cbs = _make_callbacks(
            poll_sequence=[("open", None)],
            current_price=74625.0,
        )
        result = run_limit_with_fallback(
            side="SELL", limit_price=75000.0,
            timeout_sec=3, poll_interval_sec=1.0,
            fallback_max_adverse_bps=20.0,
            elapsed_fn=_fake_clock(),
            **cbs,
        )
        assert result.success is False
        assert result.final_state == "cancelled"
