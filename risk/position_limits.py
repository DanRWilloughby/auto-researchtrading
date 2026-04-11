"""
Position limits — hard caps enforced before every order is placed.

Rejects orders that would violate:
  - Max leverage (total notional / equity)
  - Max notional per coin
  - Max concentration (% of equity in single coin)
  - Max open positions
  - Allowed symbol list
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .alerts import AlertDispatch
from .config import PositionLimitsConfig

logger = logging.getLogger(__name__)


@dataclass
class LimitCheckResult:
    allowed: bool
    reason: Optional[str] = None
    clamped_notional: Optional[float] = None  # if we clamp instead of reject


class PositionLimits:
    def __init__(self, config: PositionLimitsConfig, alerts: AlertDispatch):
        self.config = config
        self.alerts = alerts

    def check_order(
        self,
        symbol: str,
        target_notional_usd: float,
        current_positions: dict[str, float],  # symbol -> signed notional
        equity_usd: float,
    ) -> LimitCheckResult:
        """
        Validate a proposed order against all position limits.

        Args:
            symbol: the symbol being traded
            target_notional_usd: the NEW target notional (signed) for this symbol
            current_positions: current signed notionals by symbol (not including this order)
            equity_usd: current account equity

        Returns LimitCheckResult with allowed=True/False and an optional reason.
        """
        # 1. Allowed symbol
        if symbol not in self.config.allowed_symbols:
            return LimitCheckResult(
                allowed=False,
                reason=f"symbol '{symbol}' not in allowed list {self.config.allowed_symbols}",
            )

        # 2. Max notional per coin
        if abs(target_notional_usd) > self.config.max_notional_per_coin_usd:
            msg = (
                f"{symbol} target ${abs(target_notional_usd):,.2f} exceeds "
                f"per-coin cap ${self.config.max_notional_per_coin_usd:,.2f}"
            )
            self.alerts.position_limit(msg, symbol=symbol, target=target_notional_usd)
            return LimitCheckResult(allowed=False, reason=msg)

        # 3. Max concentration (% of equity)
        max_per_coin = equity_usd * (self.config.max_concentration_pct / 100)
        if abs(target_notional_usd) > max_per_coin:
            msg = (
                f"{symbol} target ${abs(target_notional_usd):,.2f} exceeds "
                f"{self.config.max_concentration_pct}% concentration cap "
                f"(${max_per_coin:,.2f})"
            )
            self.alerts.position_limit(msg, symbol=symbol, target=target_notional_usd)
            return LimitCheckResult(allowed=False, reason=msg)

        # 4. Max leverage (aggregate check)
        # Simulate what the total exposure would be AFTER this order
        new_positions = dict(current_positions)
        new_positions[symbol] = target_notional_usd
        total_abs_notional = sum(abs(v) for v in new_positions.values())
        if equity_usd > 0:
            leverage = total_abs_notional / equity_usd
            if leverage > self.config.max_leverage:
                msg = (
                    f"total leverage {leverage:.2f}x would exceed "
                    f"{self.config.max_leverage}x limit "
                    f"(total notional ${total_abs_notional:,.2f} / equity ${equity_usd:,.2f})"
                )
                self.alerts.position_limit(msg, symbol=symbol, leverage=leverage)
                return LimitCheckResult(allowed=False, reason=msg)

        # 5. Max open positions
        nonzero_positions = sum(1 for v in new_positions.values() if abs(v) > 0)
        if nonzero_positions > self.config.max_open_positions:
            msg = (
                f"opening this position would exceed "
                f"{self.config.max_open_positions} max open positions"
            )
            self.alerts.position_limit(msg, symbol=symbol, count=nonzero_positions)
            return LimitCheckResult(allowed=False, reason=msg)

        return LimitCheckResult(allowed=True)
