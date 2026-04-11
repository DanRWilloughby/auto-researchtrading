"""
Order safety layer — validates fills and handles retries.

Responsibilities:
  - Reject fills where slippage exceeds threshold
  - Retry failed orders with exponential backoff
  - Verify post-fill position matches intent
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .alerts import AlertDispatch
from .config import OrderSafetyConfig

logger = logging.getLogger(__name__)


@dataclass
class FillValidation:
    ok: bool
    slippage_bps: float = 0.0
    reason: Optional[str] = None


class OrderSafety:
    def __init__(self, config: OrderSafetyConfig, alerts: AlertDispatch):
        self.config = config
        self.alerts = alerts

    def validate_fill(
        self,
        expected_price: float,
        fill_price: float,
        side: str,
    ) -> FillValidation:
        """
        Check that a fill price is within acceptable slippage.

        For BUY: fill_price higher than expected = bad (paying more)
        For SELL: fill_price lower than expected = bad (receiving less)
        """
        if expected_price <= 0 or fill_price <= 0:
            return FillValidation(ok=False, reason="invalid price")

        if side.upper() == "BUY":
            slippage = (fill_price - expected_price) / expected_price
        else:
            slippage = (expected_price - fill_price) / expected_price

        slippage_bps = slippage * 10000

        if slippage_bps > self.config.max_slippage_bps:
            msg = (
                f"fill slippage {slippage_bps:.1f} bps exceeds "
                f"{self.config.max_slippage_bps} bps max"
            )
            self.alerts.order_error(
                msg,
                side=side,
                expected=expected_price,
                filled=fill_price,
                slippage_bps=slippage_bps,
            )
            return FillValidation(ok=False, slippage_bps=slippage_bps, reason=msg)
        return FillValidation(ok=True, slippage_bps=slippage_bps)

    def verify_position(
        self,
        symbol: str,
        expected_contracts: int,
        actual_contracts: float,
        tolerance: int = 0,
    ) -> bool:
        """Check that post-trade position matches intent."""
        if not self.config.fill_verification:
            return True
        delta = abs(actual_contracts - expected_contracts)
        if delta > tolerance:
            msg = (
                f"{symbol} position mismatch after fill: "
                f"expected {expected_contracts}, got {actual_contracts}"
            )
            self.alerts.order_error(msg, symbol=symbol, expected=expected_contracts,
                                    actual=actual_contracts)
            return False
        return True

    def retry_with_backoff(
        self,
        operation: Callable,
        operation_name: str = "order",
    ):
        """
        Execute operation() with retry + exponential backoff.

        operation should return a truthy value on success, or raise on failure.
        Returns the operation's result on success, or None if all retries exhausted.
        """
        last_error = None
        max_attempts = self.config.max_retries + 1  # +1 for initial attempt
        backoff = list(self.config.retry_backoff_sec)

        for attempt in range(max_attempts):
            try:
                result = operation()
                if result:
                    return result
            except Exception as e:
                last_error = e
                logger.warning("%s attempt %d failed: %s", operation_name, attempt + 1, e)

            # Wait before next attempt
            if attempt < max_attempts - 1:
                wait_idx = min(attempt, len(backoff) - 1)
                wait = backoff[wait_idx] if backoff else 2 ** attempt
                logger.info("Waiting %ds before %s retry", wait, operation_name)
                time.sleep(wait)

        # All retries exhausted
        self.alerts.order_error(
            f"{operation_name} failed after {max_attempts} attempts",
            error=str(last_error) if last_error else "unknown",
        )
        return None
