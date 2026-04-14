"""Tests for Fix 1: SKIP tolerance check in place_market_order.

The SKIP logic is extracted to a pure helper `should_skip_order_decision`
so we can test the decision without instantiating a CoinbaseClient (which
opens API connections at init).

Background: paper sim skips orders when |delta_notional| < $200. Live's
place_market_order only skipped when integer-contract delta == 0. Result:
77 unnecessary fills over a 3-day window on the live instance, ~$231 in
fees, while paper-cb-early skipped them all.

This test suite verifies the new tolerance-based skip behaves identically
to paper sim for typical scenarios.
"""
import pytest

from exchanges.coinbase_client import should_skip_order_decision


# --- Helper: realistic CB perp parameters ---
# BTC: contract_size=0.01, price ~$75K, contract notional ~$750
# ETH: contract_size=0.1, price ~$2400, contract notional ~$240
# SOL: contract_size=5.0, price ~$85, contract notional ~$425


class TestNotionalToleranceSkip:
    """The new $200 notional tolerance check (Fix 1)."""

    def test_zero_delta_skips(self):
        """Position already exactly at target -> skip."""
        skip, reason = should_skip_order_decision(
            target_notional_usd=3000.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True
        assert "tolerance" in reason.lower() or "delta" in reason.lower()

    def test_below_tolerance_skips(self):
        """|delta| < $200 -> skip, even if integer contract delta != 0."""
        # target=3100, current=3000 -> delta=$100, well below $200
        skip, reason = should_skip_order_decision(
            target_notional_usd=3100.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True

    def test_just_below_tolerance_skips(self):
        """|delta| = $199 -> skip (strictly less than)."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=3199.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True

    def test_at_tolerance_does_not_skip(self):
        """|delta| = exact tolerance AND crosses a contract boundary -> proceed.

        Note: tolerance check uses strict < (not <=), so delta == tolerance
        falls through to the integer-contract check. We need a target that
        rounds to a DIFFERENT contract count to confirm the order proceeds.
        """
        # Current = 3 contracts at $75000 = $2250.
        # Target = $2450 (delta = $200 = exact tolerance).
        # 2450 / 750 = 3.27 → rounds to 3 → same as current. Use bigger jump.
        # Better: current = 3 ($2250), target = $3000 (delta=$750=1 contract).
        # That's clearly above tolerance. For "at tolerance with contract change",
        # use current=2 contracts ($1500), target=$1700 (delta=$200, rounds to 2 -> same).
        # The fundamental issue: at $200 exact, with $750 contracts, rounding rarely
        # crosses. Use ETH-scale: contract_size=0.1, price=$2400, contract notional=$240.
        # Current = 8 contracts ($1920). Target = $2120 (delta=$200, rounds to 8.83 -> 9).
        # Now the integer check sees 9 vs 8 -> different -> proceed.
        skip, _ = should_skip_order_decision(
            target_notional_usd=2120.0,
            current_notional_usd=1920.0,
            current_contracts=8,
            contract_size=0.1,
            current_price=2400.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False

    def test_above_tolerance_does_not_skip(self):
        """|delta| = $750 (1 BTC contract) -> proceed."""
        # target = 3750 (5 contracts), current = 3000 (4 contracts)
        skip, _ = should_skip_order_decision(
            target_notional_usd=3750.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False

    def test_negative_delta_below_tolerance_skips(self):
        """Reducing position by less than tolerance -> skip."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=2900.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True

    def test_short_position_below_tolerance_skips(self):
        """Short position with tiny adjustment -> skip."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=-3050.0,
            current_notional_usd=-3000.0,
            current_contracts=-4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True

    def test_flat_to_open_above_tolerance_proceeds(self):
        """Going from flat to a real position -> proceed."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=3000.0,
            current_notional_usd=0.0,
            current_contracts=0,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False

    def test_close_position_above_tolerance_proceeds(self):
        """Closing a real position (target=0) -> proceed."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=0.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False

    def test_position_flip_proceeds(self):
        """Flipping long to short -> proceed (delta is huge)."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=-3000.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False


class TestIntegerContractFallback:
    """Even if notional delta exceeds tolerance, integer-contract delta == 0 still skips."""

    def test_above_tolerance_but_zero_contract_delta_skips(self):
        """If target rounds to same contract count as current, skip.

        Edge case: target_notional and current_notional differ by > $200,
        but both round to the same number of integer contracts. Order would
        be a no-op at the contract level, so skip.
        """
        # Current = 4 contracts at $75000 = $3000. Target = $3400 -> 4.53 contracts -> rounds to 5.
        # Wait — that's a real contract delta. Need a better example.
        # Try: current = 4 contracts ($3000), target = $3300 -> 4.4 -> rounds to 4. Same as current!
        skip, reason = should_skip_order_decision(
            target_notional_usd=3300.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        # Above tolerance ($300 > $200), so notional check doesn't skip.
        # But target rounds to 4 contracts = same as current. So integer check skips.
        assert skip is True
        assert "contract" in reason.lower()


class TestEdgeCases:
    """Edge cases: tiny prices, large positions, zero contract size."""

    def test_zero_target_zero_current_skips(self):
        """Both flat, no work to do -> skip."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=0.0,
            current_notional_usd=0.0,
            current_contracts=0,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True

    def test_zero_price_skips_safely(self):
        """If price is somehow zero, don't try to compute contracts -> skip."""
        skip, reason = should_skip_order_decision(
            target_notional_usd=3000.0,
            current_notional_usd=0.0,
            current_contracts=0,
            contract_size=0.01,
            current_price=0.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True
        assert "price" in reason.lower() or "invalid" in reason.lower()

    def test_eth_typical_position_above_tolerance_proceeds(self):
        """ETH typical sizes work correctly."""
        # ETH contract = 0.1 ETH * $2400 = $240. 12 contracts = $2880.
        # Target $3120 = +1 contract = +$240. Above tolerance.
        skip, _ = should_skip_order_decision(
            target_notional_usd=3120.0,
            current_notional_usd=2880.0,
            current_contracts=12,
            contract_size=0.1,
            current_price=2400.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is False

    def test_sol_typical_position_below_tolerance_skips(self):
        """SOL with sub-tolerance adjustment -> skip."""
        # SOL contract = 5 SOL * $85 = $425. 8 contracts = $3400.
        # Target $3500 = +$100, below tolerance.
        skip, _ = should_skip_order_decision(
            target_notional_usd=3500.0,
            current_notional_usd=3400.0,
            current_contracts=8,
            contract_size=5.0,
            current_price=85.0,
            skip_tolerance_usd=200.0,
        )
        assert skip is True


class TestCustomTolerance:
    """Tolerance is configurable per-call."""

    def test_zero_tolerance_only_skips_on_contract_match(self):
        """tolerance=0 disables notional check entirely."""
        # delta = $50, way above 0 tolerance, but rounds to same contract
        skip, reason = should_skip_order_decision(
            target_notional_usd=3050.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=0.0,
        )
        # $50 > $0 tolerance, but 3050/750 = 4.07 rounds to 4 = same contracts
        assert skip is True
        assert "contract" in reason.lower()

    def test_high_tolerance_skips_bigger_deltas(self):
        """tolerance=$1000 allows skipping a $750 delta."""
        skip, _ = should_skip_order_decision(
            target_notional_usd=3750.0,
            current_notional_usd=3000.0,
            current_contracts=4,
            contract_size=0.01,
            current_price=75000.0,
            skip_tolerance_usd=1000.0,
        )
        assert skip is True
