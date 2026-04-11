"""
Flash crash guard — inter-bar price monitoring.

The 30-min trading loop can't react to flash crashes that happen between
bars. This guard runs on a separate thread, polling prices every N seconds
and triggering emergency exits if:

  - Any single position moves > per_position_move_pct against us from entry
  - Total portfolio unrealized P&L < portfolio_unrealized_pct

When an emergency exit fires:
  - All positions are immediately flattened via market orders
  - A kill flag is created (requires manual resume)
  - A critical alert is dispatched

By default this uses REST polling (simpler, no auth for public price data).
A WebSocket upgrade is possible but requires a more complex teardown path.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .alerts import AlertDispatch
from .config import FlashCrashGuardConfig

logger = logging.getLogger(__name__)


@dataclass
class PositionSnapshot:
    """Snapshot of a position at the time the strategy last updated it."""
    symbol: str
    contracts: float
    entry_price: float
    notional_at_entry: float


class FlashCrashGuard:
    """Monitors prices between bars and triggers emergency exits."""

    def __init__(
        self,
        config: FlashCrashGuardConfig,
        alerts: AlertDispatch,
        price_fetcher: Callable[[str], float],
        position_getter: Callable[[], dict[str, PositionSnapshot]],
        emergency_exit: Callable[[str], None],
    ):
        """
        Args:
            config: FlashCrashGuardConfig
            alerts: AlertDispatch instance for notifications
            price_fetcher: callable(symbol) -> current price
            position_getter: callable() -> {symbol: PositionSnapshot}
            emergency_exit: callable(reason) -> flattens all + halts
        """
        self.config = config
        self.alerts = alerts
        self.price_fetcher = price_fetcher
        self.position_getter = position_getter
        self.emergency_exit = emergency_exit
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._triggered = False

    def start(self) -> None:
        """Start the monitoring thread."""
        if not self.config.enabled:
            logger.info("FlashCrashGuard disabled in config")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="flash-crash-guard")
        self._thread.start()
        logger.info("FlashCrashGuard started (poll=%ds)", self.config.poll_interval_sec)

    def stop(self) -> None:
        """Stop the monitoring thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        """Monitoring loop."""
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.error("FlashCrashGuard check error: %s", e)
            self._stop.wait(self.config.poll_interval_sec)

    def _check_once(self) -> None:
        """Single pass: fetch prices, compute moves, trigger if needed."""
        if self._triggered:
            return  # already fired, waiting for stop

        snapshots = self.position_getter()
        if not snapshots:
            return  # no open positions

        # Per-position check
        total_unrealized_pct = 0.0
        total_notional = 0.0

        for symbol, snap in snapshots.items():
            if abs(snap.contracts) == 0:
                continue
            try:
                current_price = self.price_fetcher(symbol)
            except Exception as e:
                logger.warning("Price fetch failed for %s: %s", symbol, e)
                continue

            if current_price <= 0 or snap.entry_price <= 0:
                continue

            # % move against the position's direction
            # For LONG: loss = (entry - current) / entry
            # For SHORT: loss = (current - entry) / entry
            if snap.contracts > 0:  # long
                move_against_pct = (snap.entry_price - current_price) / snap.entry_price * 100
            else:  # short
                move_against_pct = (current_price - snap.entry_price) / snap.entry_price * 100

            # Update running portfolio PnL
            pnl_pct = -move_against_pct
            total_unrealized_pct += pnl_pct * abs(snap.notional_at_entry)
            total_notional += abs(snap.notional_at_entry)

            # Per-position trigger
            if move_against_pct >= self.config.per_position_move_pct:
                reason = (
                    f"{symbol} moved {move_against_pct:.2f}% against position "
                    f"(entry ${snap.entry_price:,.2f} -> current ${current_price:,.2f})"
                )
                self._fire(reason, symbol=symbol, move_pct=move_against_pct)
                return

        # Portfolio-wide check
        if total_notional > 0:
            portfolio_pct = total_unrealized_pct / total_notional
            if portfolio_pct <= self.config.portfolio_unrealized_pct:
                reason = (
                    f"portfolio unrealized {portfolio_pct:.2f}% "
                    f"exceeds {self.config.portfolio_unrealized_pct}% threshold"
                )
                self._fire(reason, portfolio_pct=portfolio_pct)
                return

    def _fire(self, reason: str, **data) -> None:
        """Trigger emergency exit."""
        self._triggered = True
        self.alerts.flash_crash(f"FLASH CRASH GUARD TRIGGERED: {reason}", **data)
        try:
            self.emergency_exit(reason)
        except Exception as e:
            logger.critical("Emergency exit failed in FlashCrashGuard: %s", e)
            self.alerts.flash_crash(
                f"EMERGENCY EXIT FAILED: {e}",
                original_reason=reason,
            )
