"""
Correlation guard — detects correlated drawdowns across positions.

If all open positions are losing simultaneously (all 3 coins moving against us),
the strategy may be on the wrong side of a market-wide move. This guard
cuts exposure without fully flattening, to reduce risk while preserving the
ability to recover if the signal reverses.

This is NOT a kill switch — it's a proportional response.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .alerts import AlertDispatch
from .config import CorrelationGuardConfig

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    triggered: bool
    reason: Optional[str] = None
    reduction_factor: float = 1.0  # 1.0 = no change, 0.5 = halve positions


class CorrelationGuard:
    def __init__(self, config: CorrelationGuardConfig, alerts: AlertDispatch):
        self.config = config
        self.alerts = alerts
        self._triggered_at: Optional[float] = None
        self._cooldown_until: float = 0.0

    def check(
        self,
        positions: dict[str, dict],  # symbol -> {"notional": float, "unrealized_pnl_pct": float}
    ) -> CorrelationResult:
        """
        Evaluate whether to reduce exposure due to correlated drawdown.

        positions dict entries need:
          - "notional": signed USD notional
          - "unrealized_pnl_pct": unrealized P&L as % of notional (negative = losing)
        """
        if not self.config.enabled:
            return CorrelationResult(triggered=False)

        # Respect cooldown after previous trigger
        if time.time() < self._cooldown_until:
            return CorrelationResult(triggered=False, reason="in cooldown")

        # Filter to open positions only
        open_positions = {
            s: p for s, p in positions.items()
            if abs(p.get("notional", 0)) > 0
        }
        if len(open_positions) < self.config.min_positions_losing:
            return CorrelationResult(triggered=False)

        # Count positions losing beyond threshold
        losing = [
            s for s, p in open_positions.items()
            if p.get("unrealized_pnl_pct", 0) < self.config.unrealized_threshold_pct
        ]

        if len(losing) >= self.config.min_positions_losing:
            # Trigger reduction
            reduction = 1.0 - (self.config.exposure_reduction_pct / 100.0)
            self._triggered_at = time.time()
            self._cooldown_until = (
                self._triggered_at + self.config.cooldown_intervals * 1800
            )  # assumes 30m bars
            msg = (
                f"correlated drawdown: {len(losing)} positions losing > "
                f"{abs(self.config.unrealized_threshold_pct):.1f}% - "
                f"reducing exposure by {self.config.exposure_reduction_pct:.0f}%"
            )
            self.alerts.warning(
                msg,
                losing_symbols=losing,
                reduction_factor=reduction,
                open_positions=list(open_positions.keys()),
            )
            return CorrelationResult(
                triggered=True,
                reason=msg,
                reduction_factor=reduction,
            )

        return CorrelationResult(triggered=False)

    def in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until
