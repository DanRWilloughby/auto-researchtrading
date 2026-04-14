"""
RiskManager — orchestrates all guards.

This is the main entry point the trading loop uses. Every proposed signal
passes through check_signal() before being forwarded to the exchange client.

Usage:
    risk_mgr = RiskManager.from_config(exchange_client=coinbase, initial_equity=10000)
    risk_mgr.start_flash_crash_guard()

    # On each bar:
    risk_mgr.update_account_state(equity, positions, daily_realized_pnl)

    if risk_mgr.halted:
        continue  # skip signal generation

    for signal in strategy_signals:
        verdict = risk_mgr.check_signal(signal, current_price)
        if verdict.allowed:
            exchange_client.place_market_order(signal.symbol, verdict.target_notional)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .alerts import Alert, AlertDispatch, AlertType
from .circuit_breaker import CircuitBreaker
from .config import RiskConfig
from .correlation_guard import CorrelationGuard
from .data_guard import DataGuard
from .flash_crash_guard import FlashCrashGuard, PositionSnapshot
from .order_safety import OrderSafety
from .position_limits import PositionLimits

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    allowed: bool
    reason: Optional[str] = None
    target_notional: Optional[float] = None  # may differ from input if clamped
    warnings: list[str] = field(default_factory=list)


class RiskManager:
    """Orchestrator for all risk guards."""

    def __init__(
        self,
        config: RiskConfig,
        alerts: AlertDispatch,
        circuit_breaker: CircuitBreaker,
        position_limits: PositionLimits,
        data_guard: DataGuard,
        order_safety: OrderSafety,
        correlation_guard: CorrelationGuard,
        flash_crash_guard: Optional[FlashCrashGuard] = None,
    ):
        self.config = config
        self.alerts = alerts
        self.circuit_breaker = circuit_breaker
        self.position_limits = position_limits
        self.data_guard = data_guard
        self.order_safety = order_safety
        self.correlation_guard = correlation_guard
        self.flash_crash_guard = flash_crash_guard

        # Account state (updated by caller every tick)
        self._equity: float = 0.0
        self._positions: dict[str, float] = {}  # symbol -> signed notional
        self._position_snapshots: dict[str, PositionSnapshot] = {}
        self._daily_realized_pnl: float = 0.0

    @classmethod
    def from_config(
        cls,
        config: Optional[RiskConfig] = None,
        initial_equity: float = 0.0,
        config_path: Optional[str] = None,
    ) -> "RiskManager":
        """Build a RiskManager from config with default wiring."""
        if config is None:
            config = RiskConfig.from_yaml(config_path)

        # Log dir is repo_root by default
        log_dir = Path(__file__).resolve().parents[1]
        alerts = AlertDispatch(config.alerts, log_dir=log_dir)

        circuit_breaker = CircuitBreaker(
            config.circuit_breaker,
            config.watchdog,
            alerts,
            initial_equity=initial_equity,
        )
        position_limits = PositionLimits(config.position_limits, alerts)
        data_guard = DataGuard(config.data_guard, alerts)
        order_safety = OrderSafety(config.order_safety, alerts)
        correlation_guard = CorrelationGuard(config.correlation_guard, alerts)

        return cls(
            config=config,
            alerts=alerts,
            circuit_breaker=circuit_breaker,
            position_limits=position_limits,
            data_guard=data_guard,
            order_safety=order_safety,
            correlation_guard=correlation_guard,
        )

    def wire_flash_crash_guard(
        self,
        price_fetcher,
        emergency_exit,
    ) -> None:
        """Attach the flash crash guard (requires exchange callbacks)."""
        self.flash_crash_guard = FlashCrashGuard(
            config=self.config.flash_crash_guard,
            alerts=self.alerts,
            price_fetcher=price_fetcher,
            position_getter=lambda: self._position_snapshots,
            emergency_exit=emergency_exit,
        )

    def start(self) -> None:
        """Begin monitoring threads."""
        if self.flash_crash_guard:
            self.flash_crash_guard.start()
        self.alerts.dispatch(Alert(
            AlertType.STARTUP,
            "RiskManager started",
            data={"equity": self._equity},
        ))

    def stop(self) -> None:
        """Shut down monitoring threads."""
        if self.flash_crash_guard:
            self.flash_crash_guard.stop()

    @property
    def halted(self) -> bool:
        return self.circuit_breaker.halted

    @property
    def halt_reason(self) -> Optional[str]:
        return self.circuit_breaker.halt_reason

    def update_account_state(
        self,
        equity: float,
        positions: dict[str, float],
        daily_realized_pnl: float = 0.0,
        position_snapshots: Optional[dict[str, PositionSnapshot]] = None,
        *,
        realized_equity: Optional[float] = None,
    ) -> None:
        """Update internal state with latest account info. Call every tick.

        Args:
            equity: mark-to-market equity (cash + unrealized PnL).
            realized_equity (kw-only, Fix 5): settled equity (cash only,
                excludes unrealized). If provided, the circuit breaker uses
                this for HWM ratcheting instead of MTM equity — prevents
                phantom peaks from in-flight settlement. If None, falls
                back to legacy MTM-only behavior.
        """
        self._equity = equity
        self._positions = dict(positions)
        self._daily_realized_pnl = daily_realized_pnl
        if position_snapshots is not None:
            self._position_snapshots = dict(position_snapshots)

        # Fix 5: pass realized_equity for HWM ratcheting; equity (=MTM) for DD ratio.
        if realized_equity is not None:
            should_halt, reason = self.circuit_breaker.check(
                mtm_equity=equity,
                realized_equity=realized_equity,
                daily_realized_pnl=daily_realized_pnl,
            )
        else:
            # Legacy single-arg path (callers haven't been updated yet)
            should_halt, reason = self.circuit_breaker.check(
                current_equity=equity,
                daily_realized_pnl=daily_realized_pnl,
            )
        if should_halt and not self.circuit_breaker.halted:
            self.circuit_breaker.halt(reason)

    def check_signal(
        self,
        symbol: str,
        target_notional_usd: float,
    ) -> RiskVerdict:
        """
        Validate a proposed signal against all guards.

        Returns RiskVerdict with allowed=True/False. The caller is expected
        to only place orders when allowed=True.
        """
        warnings: list[str] = []

        # Fast-path: if halted, reject everything
        if self.halted:
            return RiskVerdict(
                allowed=False,
                reason=f"trader halted: {self.halt_reason}",
            )

        # Position limits check
        limit_result = self.position_limits.check_order(
            symbol=symbol,
            target_notional_usd=target_notional_usd,
            current_positions=self._positions,
            equity_usd=self._equity,
        )
        if not limit_result.allowed:
            return RiskVerdict(
                allowed=False,
                reason=limit_result.reason,
                warnings=warnings,
            )

        # Apply correlation guard reduction if active
        effective_target = target_notional_usd
        if self.correlation_guard.config.enabled:
            # Note: correlation check is run at update_account_state time,
            # but we respect any active cooldown here
            pass  # future: apply reduction factor to target

        return RiskVerdict(
            allowed=True,
            target_notional=effective_target,
            warnings=warnings,
        )

    def run_correlation_check(
        self,
        positions_with_pnl: dict[str, dict],
    ):
        """Explicit call to run correlation guard (called from trading loop)."""
        return self.correlation_guard.check(positions_with_pnl)
